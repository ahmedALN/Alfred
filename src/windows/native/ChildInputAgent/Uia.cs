using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Automation;

// ====================================================================
// The accessibility layer, living inside the session it acts on.
//
// UI Automation is session-scoped: a client in one Windows session
// cannot see or drive the windows of another. Alfred's own process
// lives in the user's session, so its UIA calls could only ever reach
// the user's screen - which made "do this without disturbing me"
// impossible for anything more precise than screenshot-and-click.
//
// This is the same control surface as src/windows/uia.py, reimplemented
// where it can actually reach Alfred's private desktop. The JSON it
// returns is deliberately identical to the Python backend's, so the
// ui_control tool - and the model driving it - cannot tell which
// session it is working in.
// ====================================================================

internal sealed class UiaService
{
    private static readonly Lazy<UiaService> Shared =
        new(() => new UiaService());

    public static UiaService Instance => Shared.Value;

    // One pipe client, but a stray timed-out call could still be in
    // flight inside COM, so every mutation of the caches is guarded.
    private readonly object _gate = new();

    private readonly Dictionary<int, AutomationElement> _byRef = new();
    private readonly List<ControlRecord> _controls = new();

    private AutomationElement? _window;
    private string _windowTitle = string.Empty;
    private string? _specTitle;
    private int? _specPid;

    // A wedged app can block a UIA call indefinitely. The pipe is
    // synchronous, so that would hang the whole agent - and with it
    // Alfred. Every action gets a ceiling and returns a clean error.
    private const int DefaultActionTimeoutMs = 25000;

    // How deep to walk unless the caller says otherwise.
    //
    // This was 14, to stop a browser's enormous tree hanging the call.
    // The node budget is what actually bounds that; the depth limit just
    // silently truncated. On a YouTube channel page, depth 14 found 18
    // controls and NOT ONE video link - depth 25 found 173 controls and
    // all 30 videos, in the same 0.2s. Alfred could not click what it
    // could not see.
    private const int DefaultMaxDepth = 30;

    // Controls worth showing a planner. Mirrors _ACTIONABLE in uia.py.
    private static readonly HashSet<string> Actionable = new(
        StringComparer.Ordinal)
    {
        "Button", "Edit", "Document", "ListItem", "MenuItem", "Hyperlink",
        "TabItem", "CheckBox", "RadioButton", "ComboBox", "TreeItem",
        "SplitButton", "Slider", "Text", "List", "Menu", "Tab", "Group",
    };

    // Structural containers are noise unless they are labelled.
    private static readonly HashSet<string> NeedsName = new(
        StringComparer.Ordinal)
    {
        "Group", "List", "Menu", "Tab", "Text",
    };

    private sealed record ControlRecord(
        int Ref,
        string Type,
        string Name,
        string AutomationId,
        int Left,
        int Top,
        int Right,
        int Bottom,
        bool Enabled,
        bool IsPassword)
    {
        public object ToJson()
        {
            var payload = new Dictionary<string, object?>
            {
                ["ref"] = Ref,
                ["type"] = Type,
                ["name"] = Name,
                ["id"] = string.IsNullOrEmpty(AutomationId)
                    ? null
                    : AutomationId,
                ["center"] = new[]
                {
                    (Left + Right) / 2,
                    (Top + Bottom) / 2,
                },
                ["enabled"] = Enabled,
            };

            if (IsPassword)
            {
                payload["password_field"] = true;
            }

            return payload;
        }
    }

    private sealed class UiaFailure : Exception
    {
        public UiaFailure(string message) : base(message)
        {
        }
    }

    // ================================================================
    // Dispatch
    // ================================================================

    public static string Handle(JsonElement root)
    {
        string action = ReadString(root, "action") ?? string.Empty;

        if (string.IsNullOrWhiteSpace(action))
        {
            return Program.UiaFail(
                "missing_action",
                "A 'uia' request needs an 'action'."
            );
        }

        try
        {
            return Instance.Dispatch(action.ToLowerInvariant(), root);
        }
        catch (UiaFailure ex)
        {
            return Program.UiaFail("uia_error", ex.Message);
        }
        catch (ElementNotAvailableException)
        {
            return Program.UiaFail(
                "element_gone",
                "That control disappeared - read the tree again."
            );
        }
        catch (Exception ex)
        {
            return Program.UiaFail(ex.GetType().Name, ex.Message);
        }
    }

    private string Dispatch(string action, JsonElement root)
    {
        // Waits do their own pacing and must outlive the default ceiling.
        int ceiling = action is "wait_for" or "wait_ready"
            ? (int)(ReadDouble(root, "timeout", 25.0) * 1000) + 10000
            : DefaultActionTimeoutMs;

        return Guarded(action, ceiling, () => Run(action, root));
    }

    private string Run(string action, JsonElement root)
    {
        lock (_gate)
        {
            return action switch
            {
                "windows" => OpWindows(root),
                "focus" => OpFocus(root),
                "tree" => OpTree(root),
                "find" => OpFind(root),
                "click" => OpClick(root, doubleClick: false, right: false),
                "double_click" => OpClick(root, doubleClick: true, right: false),
                "right_click" => OpClick(root, doubleClick: false, right: true),
                "invoke" => OpInvoke(root),
                "type" => OpType(root),
                "get" => OpGet(root),
                "select" => OpSelect(root),
                "expand" => OpExpand(root),
                "scroll" => OpScroll(root),
                "menu" => OpMenu(root),
                "exists" => OpExists(root),
                "wait_for" => OpWaitFor(root),
                "wait_ready" => OpWaitReady(root),
                "info" => OpInfo(root),
                "unnamed" => OpUnnamed(root),
                _ => Program.UiaFail(
                    "unknown_action",
                    $"Unknown uia action '{action}'."
                ),
            };
        }
    }

    /// <summary>
    /// Runs a UIA operation with a hard ceiling. A hung app blocks
    /// inside COM where there is nothing to cancel, so the worker is
    /// abandoned rather than killed - but the agent stays responsive,
    /// which is what matters.
    /// </summary>
    private static string Guarded(string action, int timeoutMs, Func<string> work)
    {
        Task<string> task = Task.Run(work);

        bool finished;

        try
        {
            finished = task.Wait(timeoutMs);
        }
        catch (AggregateException)
        {
            // Wait() re-wraps a failure. GetResult below unwraps it, so
            // the caller sees "no control matches ref=999" rather than
            // "AggregateException: One or more errors occurred".
            finished = true;
        }

        if (finished)
        {
            return task.GetAwaiter().GetResult();
        }

        return Program.UiaFail(
            "uia_timeout",
            $"'{action}' did not finish within {timeoutMs / 1000}s - the app "
            + "is not responding to the accessibility layer."
        );
    }

    // ================================================================
    // Finding windows
    // ================================================================

    private static string TypeName(AutomationElement el, bool cached)
    {
        try
        {
            ControlType type = cached
                ? el.Cached.ControlType
                : el.Current.ControlType;

            string name = type.ProgrammaticName ?? string.Empty;
            int dot = name.LastIndexOf('.');
            return dot >= 0 ? name[(dot + 1)..] : name;
        }
        catch
        {
            return string.Empty;
        }
    }

    private static string SafeName(AutomationElement el)
    {
        try
        {
            return (el.Current.Name ?? string.Empty).Trim();
        }
        catch
        {
            return string.Empty;
        }
    }

    private List<AutomationElement> TopLevelWindows()
    {
        var found = new List<AutomationElement>();

        try
        {
            AutomationElementCollection children =
                AutomationElement.RootElement.FindAll(
                    TreeScope.Children,
                    Condition.TrueCondition
                );

            foreach (AutomationElement el in children)
            {
                found.Add(el);
            }
        }
        catch (Exception ex)
        {
            throw new UiaFailure($"could not list windows: {ex.Message}");
        }

        return found;
    }

    /// <summary>
    /// Title matching is deliberately forgiving: models describe windows
    /// loosely ("notepad", "Untitled - Notepad"). Plain substring first,
    /// then regex for callers who really meant one.
    /// </summary>
    private static bool TitleMatches(string title, string wanted)
    {
        if (string.IsNullOrWhiteSpace(wanted))
        {
            return true;
        }

        if (title.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        try
        {
            return Regex.IsMatch(
                title,
                wanted,
                RegexOptions.IgnoreCase,
                TimeSpan.FromMilliseconds(250)
            );
        }
        catch
        {
            return false;
        }
    }

    private AutomationElement ResolveWindow(string? title, int? pid)
    {
        if (string.IsNullOrWhiteSpace(title) && pid is null)
        {
            IntPtr foreground = Program.UiaForegroundWindow();

            if (foreground != IntPtr.Zero)
            {
                try
                {
                    return AutomationElement.FromHandle(foreground);
                }
                catch
                {
                    // Fall through to the scan below.
                }
            }
        }

        var candidates = new List<(AutomationElement El, string Title)>();

        foreach (AutomationElement el in TopLevelWindows())
        {
            string name;
            int elementPid;

            try
            {
                name = (el.Current.Name ?? string.Empty).Trim();
                elementPid = el.Current.ProcessId;
            }
            catch
            {
                continue;
            }

            if (pid is not null && elementPid != pid.Value)
            {
                continue;
            }

            if (!string.IsNullOrWhiteSpace(title) && !TitleMatches(name, title!))
            {
                continue;
            }

            if (pid is null && string.IsNullOrEmpty(name))
            {
                continue;
            }

            candidates.Add((el, name));
        }

        if (candidates.Count == 0)
        {
            string asked = title ?? (pid is null ? "the foreground window" : $"pid {pid}");
            throw new UiaFailure(
                $"window not found: {asked}. Use action 'windows' to see "
                + "what is open."
            );
        }

        if (candidates.Count == 1)
        {
            return candidates[0].El;
        }

        // Shortest-title-wins used to decide this, and it picked the
        // wrong window as soon as titles moved: searching in Explorer
        // renamed it "notepad - Search Results in Windows - File
        // Explorer", at which point a terminal called
        // "C:\WINDOWS\system32\cmd.exe" was the shorter match for
        // "Windows" and the next action went there instead.
        return candidates
            .Select(c => new
            {
                c.El,
                Score = ScoreTitle(c.Title, title ?? string.Empty)
                        + (SameAsLastWindow(c.El) ? 250 : 0),
            })
            .OrderByDescending(c => c.Score)
            .First()
            .El;
    }

    private static int ScoreTitle(string title, string wanted)
    {
        if (string.IsNullOrWhiteSpace(wanted))
        {
            return 0;
        }

        string haystack = title.ToLowerInvariant();
        string want = wanted.Trim().ToLowerInvariant();

        int score;

        if (haystack == want)
        {
            score = 1000;
        }
        else if (haystack.StartsWith(want, StringComparison.Ordinal))
        {
            score = 500;
        }
        else if (haystack.Contains(want, StringComparison.Ordinal))
        {
            // A whole word beats a fragment inside a path.
            score = Regex.IsMatch(haystack, $@"{Regex.Escape(want)}")
                ? 300
                : 100;
        }
        else
        {
            score = 50;   // matched by regex rather than plain text
        }

        // Among equals the tighter title is the better answer.
        return score - Math.Min(title.Length / 4, 40);
    }

    /// <summary>
    /// Staying on the window we were just working in is almost always
    /// right: "search Explorer, then open the result" is one job, and
    /// the second half should not wander to a different window whose
    /// title happens to match too.
    /// </summary>
    private bool SameAsLastWindow(AutomationElement candidate)
    {
        if (_window is null)
        {
            return false;
        }

        try
        {
            return Automation.Compare(_window, candidate);
        }
        catch
        {
            return false;
        }
    }

    private string OpWindows(JsonElement root)
    {
        int limit = ReadInt(root, "limit") ?? 40;
        var listed = new List<object>();

        foreach (AutomationElement el in TopLevelWindows())
        {
            try
            {
                string title = (el.Current.Name ?? string.Empty).Trim();

                if (string.IsNullOrEmpty(title))
                {
                    continue;
                }

                System.Windows.Rect box = el.Current.BoundingRectangle;

                listed.Add(new
                {
                    title = title.Length > 90 ? title[..90] : title,
                    pid = el.Current.ProcessId,
                    @class = el.Current.ClassName ?? string.Empty,
                    // Needed to turn a learned landmark - a position
                    // within the window - back into a screen point.
                    rect = new[]
                    {
                        ToPixels(box.Left), ToPixels(box.Top),
                        ToPixels(box.Right), ToPixels(box.Bottom),
                    },
                });

                if (listed.Count >= limit)
                {
                    break;
                }
            }
            catch
            {
                // A window closing mid-enumeration is normal.
            }
        }

        return Program.UiaOk(new { count = listed.Count, windows = listed });
    }

    private string OpFocus(JsonElement root)
    {
        string? title = ReadString(root, "window");
        int? pid = ReadInt(root, "pid");

        AutomationElement window = ResolveWindow(title, pid);
        FocusWindow(window);

        _window = window;
        _specTitle = title;
        _specPid = pid;
        _windowTitle = SafeName(window);

        return Program.UiaOk(new
        {
            focused = string.IsNullOrEmpty(_windowTitle)
                ? (object?)title
                : _windowTitle,
            session = Program.UiaSessionId(),
        });
    }

    private static void FocusWindow(AutomationElement window)
    {
        // Alfred owns this whole session, so taking the foreground here
        // disturbs nobody - unlike the same call on the user's desktop.
        try
        {
            IntPtr handle = new(window.Current.NativeWindowHandle);

            if (handle != IntPtr.Zero)
            {
                Program.UiaActivateWindow(handle);
                Thread.Sleep(120);
                return;
            }
        }
        catch
        {
            // Fall through to the UIA route.
        }

        try
        {
            window.SetFocus();
            Thread.Sleep(120);
        }
        catch
        {
            // Some windows refuse focus; the caller can still act via
            // patterns, so this is not fatal.
        }
    }

    // ================================================================
    // Reading the control tree
    // ================================================================

    /// <summary>
    /// Breadth-first, depth-bounded, budget-bounded walk.
    ///
    /// Every property read over UIA is a cross-process call, so a naive
    /// walk of a browser's tree takes tens of seconds. Under an active
    /// CacheRequest each FindAll returns a whole level of children with
    /// their properties already populated, which is the difference
    /// between "instant" and "the model gave up waiting".
    /// </summary>
    private static List<AutomationElement> Descendants(
        AutomationElement root,
        int maxDepth,
        int budget)
    {
        var collected = new List<AutomationElement>();
        var frontier = new List<AutomationElement> { root };

        var cache = new CacheRequest
        {
            // Full keeps a live reference, so a cached element can still
            // be clicked afterwards. None would be faster and useless.
            AutomationElementMode = AutomationElementMode.Full,
            TreeScope = TreeScope.Element | TreeScope.Children,
        };

        cache.Add(AutomationElement.NameProperty);
        cache.Add(AutomationElement.ControlTypeProperty);
        cache.Add(AutomationElement.AutomationIdProperty);
        cache.Add(AutomationElement.BoundingRectangleProperty);
        cache.Add(AutomationElement.IsEnabledProperty);
        cache.Add(AutomationElement.IsPasswordProperty);
        cache.Add(AutomationElement.IsOffscreenProperty);

        using (cache.Activate())
        {
            for (int depth = 0;
                 depth < maxDepth && frontier.Count > 0 && collected.Count < budget;
                 depth++)
            {
                var next = new List<AutomationElement>();

                foreach (AutomationElement parent in frontier)
                {
                    AutomationElementCollection children;

                    try
                    {
                        children = parent.FindAll(
                            TreeScope.Children,
                            Condition.TrueCondition
                        );
                    }
                    catch
                    {
                        continue;
                    }

                    foreach (AutomationElement child in children)
                    {
                        collected.Add(child);
                        next.Add(child);

                        if (collected.Count >= budget)
                        {
                            break;
                        }
                    }

                    if (collected.Count >= budget)
                    {
                        break;
                    }
                }

                frontier = next;
            }
        }

        return collected;
    }

    private string OpTree(JsonElement root)
    {
        string? title = ReadString(root, "window");
        int? pid = ReadInt(root, "pid");
        string? contains = ReadString(root, "contains");
        int limit = ReadInt(root, "limit") ?? 80;
        int maxDepth = ReadInt(root, "max_depth") ?? DefaultMaxDepth;

        AutomationElement window = ResolveWindow(title, pid);

        _window = window;
        _specTitle = title;
        _specPid = pid;
        _windowTitle = SafeName(window);

        // Read without stealing focus first. Chromium and Electron apps
        // only expose a real tree once focused, so a thin result earns
        // one focused retry.
        List<AutomationElement> found = Descendants(window, maxDepth, 4000);

        if (CountActionable(found) < 4)
        {
            WakeAccessibility(window);
            FocusWindow(window);
            Thread.Sleep(300);
            found = Descendants(window, maxDepth, 4000);
        }

        BuildRecords(found, contains, limit);

        return Program.UiaOk(new
        {
            window = _windowTitle,
            count = _controls.Count,
            // How much of the tree the walk actually saw. A big app that
            // reports a handful of elements is not a small app - it is an
            // app whose accessibility tree has not woken up yet, and that
            // distinction is invisible without this.
            scanned = found.Count,
            controls = _controls.Select(c => c.ToJson()).ToList(),
            session = Program.UiaSessionId(),
        });
    }

    private static void WakeAccessibility(AutomationElement window)
    {
        try
        {
            IntPtr handle = new(window.Current.NativeWindowHandle);

            if (handle != IntPtr.Zero)
            {
                Program.UiaWakeAccessibility(handle);
            }
        }
        catch
        {
            // No native handle to poke; nothing lost.
        }
    }

    private static int CountActionable(List<AutomationElement> elements)
    {
        int n = 0;

        foreach (AutomationElement el in elements)
        {
            if (Actionable.Contains(TypeName(el, cached: true)))
            {
                n++;
            }
        }

        return n;
    }

    private static int ToPixels(double value)
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
        {
            return 0;
        }

        value = Math.Max(-1000000, Math.Min(1000000, value));
        return (int)Math.Round(value, MidpointRounding.AwayFromZero);
    }

    private void BuildRecords(
        List<AutomationElement> elements,
        string? contains,
        int limit)
    {
        _byRef.Clear();
        _controls.Clear();

        string want = (contains ?? string.Empty).Trim().ToLowerInvariant();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        int next = 0;

        foreach (AutomationElement el in elements)
        {
            try
            {
                string type = TypeName(el, cached: true);

                if (!Actionable.Contains(type))
                {
                    continue;
                }

                string name = (el.Cached.Name ?? string.Empty).Trim();

                // Empty-named edit surfaces are still worth offering -
                // that is exactly what Notepad's text area looks like.
                if (name.Length == 0 && type != "Edit" && type != "Document")
                {
                    continue;
                }

                if (name.Length == 0 && NeedsName.Contains(type))
                {
                    continue;
                }

                string automationId = el.Cached.AutomationId ?? string.Empty;
                string key = type + "|" + name + "|" + automationId;

                if (!seen.Add(key))
                {
                    continue;
                }

                if (want.Length > 0)
                {
                    string haystack =
                        (name + " " + type + " " + automationId)
                            .ToLowerInvariant();

                    if (!haystack.Contains(want, StringComparison.Ordinal))
                    {
                        continue;
                    }
                }

                System.Windows.Rect rect = el.Cached.BoundingRectangle;

                bool enabled = true;
                try
                {
                    enabled = el.Cached.IsEnabled;
                }
                catch
                {
                    // Property unsupported; assume usable.
                }

                bool password = false;
                try
                {
                    password = el.Cached.IsPassword;
                }
                catch
                {
                    // Property unsupported; the name guard still applies.
                }

                if (name.Length > 80)
                {
                    name = name[..80];
                }

                var record = new ControlRecord(
                    next,
                    type,
                    name,
                    automationId,
                    ToPixels(rect.Left),
                    ToPixels(rect.Top),
                    ToPixels(rect.Right),
                    ToPixels(rect.Bottom),
                    enabled,
                    password
                );

                _byRef[next] = el;
                _controls.Add(record);
                next++;

                if (next >= limit)
                {
                    break;
                }
            }
            catch
            {
                // Elements vanish mid-walk; skip and carry on.
            }
        }
    }

    /// <summary>
    /// Controls that are visible but carry no name.
    ///
    /// Qt apps and game launchers draw their buttons without labels, so
    /// a search by name finds nothing while the user is looking right
    /// at them. These cannot be identified from the tree - only located
    /// - which is enough to click one and ask what it did.
    /// </summary>
    private string OpUnnamed(JsonElement root)
    {
        string? title = ReadString(root, "window");
        int? pid = ReadInt(root, "pid");
        int limit = ReadInt(root, "limit") ?? 40;

        AutomationElement window = ResolveWindow(title, pid);
        _window = window;
        _specTitle = title;
        _specPid = pid;

        System.Windows.Rect frame = window.Current.BoundingRectangle;
        double width = Math.Max(1, frame.Width);
        double height = Math.Max(1, frame.Height);

        var found = new List<Dictionary<string, object?>>();

        foreach (AutomationElement el in Descendants(window, DefaultMaxDepth, 4000))
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(el.Cached.Name))
                {
                    continue;
                }

                if (!string.IsNullOrWhiteSpace(el.Cached.AutomationId))
                {
                    continue;
                }

                string kind = TypeName(el, cached: true);

                if (kind is "Pane" or "Group" or "Window" or "TitleBar"
                    or "Thumb" or "")
                {
                    continue;
                }

                System.Windows.Rect box = el.Cached.BoundingRectangle;

                if (box.Width < 16 || box.Height < 10)
                {
                    continue;
                }

                if (box.Width > width * 0.9 && box.Height > height * 0.9)
                {
                    continue;
                }

                int cx = ToPixels(box.Left + (box.Width / 2));
                int cy = ToPixels(box.Top + (box.Height / 2));

                found.Add(new Dictionary<string, object?>
                {
                    ["type"] = kind,
                    ["center"] = new[] { cx, cy },
                    ["rel"] = new[]
                    {
                        Math.Round((cx - frame.Left) / width, 4),
                        Math.Round((cy - frame.Top) / height, 4),
                    },
                    ["size"] = new[]
                    {
                        ToPixels(box.Width), ToPixels(box.Height),
                    },
                });

                if (found.Count >= limit)
                {
                    break;
                }
            }
            catch
            {
                // Vanished mid-walk.
            }
        }

        found.Sort((a, b) =>
        {
            int[] left = (int[])a["center"]!;
            int[] right = (int[])b["center"]!;
            int byRow = left[1].CompareTo(right[1]);
            return byRow != 0 ? byRow : left[0].CompareTo(right[0]);
        });

        for (int i = 0; i < found.Count; i++)
        {
            found[i]["index"] = i;
        }

        return Program.UiaOk(new
        {
            window = SafeName(window),
            count = found.Count,
            controls = found,
        });
    }

    private string OpFind(JsonElement root)
    {
        string query = ReadString(root, "query") ?? string.Empty;

        if (string.IsNullOrWhiteSpace(query))
        {
            throw new UiaFailure("'find' needs a query.");
        }

        if (_controls.Count == 0)
        {
            RereadLastWindow();
        }

        string want = query.Trim().ToLowerInvariant();
        int limit = ReadInt(root, "limit") ?? 20;

        List<ControlRecord> hits = _controls
            .Where(c => (c.Name + " " + c.Type + " " + c.AutomationId)
                .ToLowerInvariant()
                .Contains(want, StringComparison.Ordinal))
            .Take(limit)
            .ToList();

        return Program.UiaOk(new
        {
            count = hits.Count,
            controls = hits.Select(c => c.ToJson()).ToList(),
        });
    }

    private void RereadLastWindow()
    {
        if (_window is null && _specTitle is null && _specPid is null)
        {
            return;
        }

        try
        {
            AutomationElement window = ResolveWindow(_specTitle, _specPid);
            _window = window;
            _windowTitle = SafeName(window);
            BuildRecords(Descendants(window, DefaultMaxDepth, 4000), null, 80);
        }
        catch (UiaFailure)
        {
            // The window went away; callers surface their own error.
        }
    }

    // ================================================================
    // Resolving a target control
    // ================================================================

    /// <summary>
    /// Refs go stale the moment the UI moves on - a click that opens a
    /// dialog invalidates everything. Rather than making the model
    /// re-read the tree after every action, a miss re-reads once and
    /// tries again, which is what a person would do.
    /// </summary>
    private AutomationElement Resolve(int? reference, string? name)
    {
        AutomationElement? el = ResolveCached(reference, name);

        if (el is not null && StillMatches(el, name))
        {
            return el;
        }

        RereadLastWindow();
        el = ResolveCached(reference, name);

        if (el is not null && StillMatches(el, name))
        {
            return el;
        }

        throw new UiaFailure(
            $"no control matches ref={reference?.ToString() ?? "null"} "
            + $"name={(name is null ? "null" : "'" + name + "'")} - run "
            + "'tree' first, or the control may not be on screen yet"
        );
    }

    /// <summary>
    /// Is this element still the one that was asked for?
    ///
    /// Cached elements go stale on a live page - a tree read a second
    /// ago has been rebuilt underneath, and the handle that was "Deji"
    /// now points at something called "Unwatched". Clicking it anyway
    /// and reporting success is worse than failing: the user is told the
    /// right thing happened while the wrong thing did.
    /// </summary>
    private static bool StillMatches(AutomationElement el, string? name)
    {
        if (string.IsNullOrWhiteSpace(name))
        {
            return true;
        }

        string want = name!.Trim().ToLowerInvariant();

        try
        {
            string live = (el.Current.Name ?? string.Empty)
                .Trim()
                .ToLowerInvariant();

            if (live.Contains(want, StringComparison.Ordinal))
            {
                return true;
            }

            // The label may be empty or generic while the id still
            // identifies it.
            string id = (el.Current.AutomationId ?? string.Empty)
                .Trim()
                .ToLowerInvariant();

            return id.Length > 0 && id.Contains(want, StringComparison.Ordinal);
        }
        catch
        {
            return false;
        }
    }

    private AutomationElement? ResolveCached(int? reference, string? name)
    {
        if (reference is not null
            && _byRef.TryGetValue(reference.Value, out AutomationElement? byRef)
            && IsAlive(byRef))
        {
            return byRef;
        }

        if (string.IsNullOrWhiteSpace(name))
        {
            return null;
        }

        string want = name!.Trim().ToLowerInvariant();
        AutomationElement? best = null;
        int bestLength = int.MaxValue;

        for (int i = 0; i < _controls.Count; i++)
        {
            if (!_byRef.TryGetValue(i, out AutomationElement? el))
            {
                continue;
            }

            ControlRecord record = _controls[i];
            string candidate = record.Name.Trim().ToLowerInvariant();

            if (candidate == want)
            {
                return el;
            }

            string automationId =
                record.AutomationId.Trim().ToLowerInvariant();

            if (automationId.Length > 0 && automationId == want)
            {
                return el;
            }

            // Shortest containing name wins: "Play" should beat
            // "Play next in queue" when the model asked for "Play".
            if (candidate.Contains(want, StringComparison.Ordinal)
                && candidate.Length < bestLength)
            {
                best = el;
                bestLength = candidate.Length;
            }
        }

        return best;
    }

    private static bool IsAlive(AutomationElement el)
    {
        try
        {
            _ = el.Current.ControlType;
            return true;
        }
        catch
        {
            return false;
        }
    }

    private ControlRecord? RecordFor(int? reference, string? name)
    {
        if (reference is not null
            && reference.Value >= 0
            && reference.Value < _controls.Count)
        {
            return _controls[reference.Value];
        }

        if (string.IsNullOrWhiteSpace(name))
        {
            return null;
        }

        string want = name!.Trim().ToLowerInvariant();

        foreach (ControlRecord c in _controls)
        {
            if (c.Name.Trim().ToLowerInvariant() == want)
            {
                return c;
            }
        }

        foreach (ControlRecord c in _controls)
        {
            if (c.Name.Trim().ToLowerInvariant()
                .Contains(want, StringComparison.Ordinal))
            {
                return c;
            }
        }

        return null;
    }

    private string OpInfo(JsonElement root)
    {
        ControlRecord? record = RecordFor(
            ReadInt(root, "ref"),
            ReadString(root, "name")
        );

        return Program.UiaOk(new
        {
            control = record?.ToJson(),
        });
    }

    // ================================================================
    // Acting on controls
    // ================================================================

    private static string Label(AutomationElement el)
    {
        return SafeName(el);
    }

    private static bool CenterOf(AutomationElement el, out int x, out int y)
    {
        x = 0;
        y = 0;

        try
        {
            System.Windows.Rect rect = el.Current.BoundingRectangle;

            if (rect.IsEmpty
                || double.IsInfinity(rect.Left)
                || rect.Width <= 0
                || rect.Height <= 0)
            {
                return false;
            }

            x = ToPixels(rect.Left + (rect.Width / 2));
            y = ToPixels(rect.Top + (rect.Height / 2));
            return true;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Brings the control into view and its window to the front, so a
    /// real mouse click lands on it rather than on whatever is on top.
    /// </summary>
    private static void PrepareForClick(AutomationElement el)
    {
        try
        {
            if (el.TryGetCurrentPattern(
                    ScrollItemPattern.Pattern,
                    out object scrollItem))
            {
                ((ScrollItemPattern)scrollItem).ScrollIntoView();
                Thread.Sleep(80);
            }
        }
        catch
        {
            // Not scrollable, or already visible.
        }

        try
        {
            IntPtr handle = new(el.Current.NativeWindowHandle);

            if (handle == IntPtr.Zero)
            {
                AutomationElement top = TreeWalker.ControlViewWalker
                    .GetParent(el);

                while (top is not null
                       && top.Current.NativeWindowHandle == 0)
                {
                    top = TreeWalker.ControlViewWalker.GetParent(top);
                }

                if (top is not null)
                {
                    handle = new IntPtr(top.Current.NativeWindowHandle);
                }
            }

            if (handle != IntPtr.Zero)
            {
                Program.UiaActivateWindow(handle);
                Thread.Sleep(80);
            }
        }
        catch
        {
            // Best effort - patterns still work without foreground.
        }
    }

    private string OpClick(JsonElement root, bool doubleClick, bool right)
    {
        int? reference = ReadInt(root, "ref");
        string? name = ReadString(root, "name");

        AutomationElement el = Resolve(reference, name);
        string label = Label(el);

        PrepareForClick(el);

        // A real mouse click is what the user would do, and it works on
        // controls whose patterns are unimplemented or lie. Patterns are
        // the fallback, not the first choice - same order as the Python
        // backend, so behaviour matches on both desktops.
        if (CenterOf(el, out int x, out int y)
            && Program.UiaClickAt(
                x,
                y,
                right ? "right" : "left",
                doubleClick))
        {
            return Program.UiaOk(new
            {
                clicked = label,
                via = "mouse",
                x,
                y,
            });
        }

        if (!right && TryPatternClick(el, doubleClick))
        {
            return Program.UiaOk(new { clicked = label, via = "pattern" });
        }

        throw new UiaFailure(
            $"could not click {(label.Length > 0 ? label : "that control")}"
        );
    }

    private static bool TryPatternClick(AutomationElement el, bool doubleClick)
    {
        try
        {
            if (el.TryGetCurrentPattern(
                    InvokePattern.Pattern,
                    out object invoke))
            {
                ((InvokePattern)invoke).Invoke();

                if (doubleClick)
                {
                    Thread.Sleep(60);
                    ((InvokePattern)invoke).Invoke();
                }

                return true;
            }
        }
        catch
        {
            // Fall through.
        }

        try
        {
            if (el.TryGetCurrentPattern(
                    SelectionItemPattern.Pattern,
                    out object selection))
            {
                ((SelectionItemPattern)selection).Select();
                return true;
            }
        }
        catch
        {
            // Fall through.
        }

        try
        {
            if (el.TryGetCurrentPattern(
                    TogglePattern.Pattern,
                    out object toggle))
            {
                ((TogglePattern)toggle).Toggle();
                return true;
            }
        }
        catch
        {
            // Nothing left to try.
        }

        return false;
    }

    private string OpInvoke(JsonElement root)
    {
        AutomationElement el = Resolve(
            ReadInt(root, "ref"),
            ReadString(root, "name")
        );

        string label = Label(el);

        if (TryPatternClick(el, doubleClick: false))
        {
            return Program.UiaOk(new { invoked = label });
        }

        PrepareForClick(el);

        if (CenterOf(el, out int x, out int y)
            && Program.UiaClickAt(x, y, "left", false))
        {
            return Program.UiaOk(new { invoked = label, via = "mouse" });
        }

        throw new UiaFailure("could not invoke the control");
    }

    // Names that mean "secret". The Python tool refuses these before the
    // request is ever sent; this is the same rule enforced where the
    // typing actually happens, so no future caller can route around it.
    private static readonly Regex SecretName = new(
        @"\b(password|passwd|pwd|passcode|pass\s*phrase|passphrase|pin|"
        + @"security\s*code|secret|cvv|cvc|card\s*number|otp|"
        + @"one[-\s]?time\s*code|2fa|authenticator|recovery\s*key|"
        + @"private\s*key|api\s*key|token)\b",
        RegexOptions.IgnoreCase | RegexOptions.Compiled
    );

    private static void RefuseIfSecret(AutomationElement el, string? name)
    {
        bool masked = false;

        try
        {
            masked = el.Current.IsPassword;
        }
        catch
        {
            // Property unsupported; the name check below still applies.
        }

        if (masked)
        {
            throw new UiaFailure(
                "that is a password field - Alfred does not type credentials"
            );
        }

        string label = SafeName(el);

        if (label.Length == 0)
        {
            label = name ?? string.Empty;
        }

        if (label.Length > 0 && SecretName.IsMatch(label))
        {
            throw new UiaFailure(
                $"the field '{label}' asks for a secret - Alfred does not "
                + "type credentials"
            );
        }
    }

    /// <summary>
    /// Types text and waits for the app to actually receive it.
    ///
    /// SendInput queues each character and returns immediately, so a
    /// 'get' straight afterwards read back "se" of "second". Waiting
    /// roughly as long as the keystrokes take to arrive makes typing
    /// mean the same thing as SetValue does: done when it returns.
    /// </summary>
    private static bool TypeAndSettle(string text)
    {
        if (!Program.UiaTypeText(text))
        {
            return false;
        }

        Thread.Sleep(Math.Min(600, 40 + (text.Length * 8)));
        return true;
    }

    private string OpType(JsonElement root)
    {
        string text = ReadString(root, "text") ?? string.Empty;
        int? reference = ReadInt(root, "ref");
        string? name = ReadString(root, "name");

        if (reference is null && string.IsNullOrWhiteSpace(name))
        {
            // Free typing into whatever holds focus.
            if (!TypeAndSettle(text))
            {
                throw new UiaFailure("could not send the text");
            }

            return Program.UiaOk(new { typed = text });
        }

        AutomationElement el = Resolve(reference, name);
        RefuseIfSecret(el, name);

        try
        {
            el.SetFocus();
            Thread.Sleep(90);
        }
        catch
        {
            // Some fields cannot take programmatic focus; click instead.
        }

        // SetValue is instant and exact, and does not depend on focus or
        // keyboard layout. Not every field implements it.
        try
        {
            if (el.TryGetCurrentPattern(ValuePattern.Pattern, out object value))
            {
                var pattern = (ValuePattern)value;

                if (!pattern.Current.IsReadOnly)
                {
                    pattern.SetValue(text);

                    return Program.UiaOk(new { typed = text, via = "value" });
                }
            }
        }
        catch
        {
            // Fall through to typing it for real.
        }

        PrepareForClick(el);

        if (CenterOf(el, out int x, out int y))
        {
            Program.UiaClickAt(x, y, "left", false);
            Thread.Sleep(90);
        }

        if (!TypeAndSettle(text))
        {
            throw new UiaFailure("could not send the text");
        }

        return Program.UiaOk(new { typed = text, via = "keyboard" });
    }

    private string OpGet(JsonElement root)
    {
        AutomationElement el = Resolve(
            ReadInt(root, "ref"),
            ReadString(root, "name")
        );

        // A control that carries a value reports that value, INCLUDING
        // when it is empty. Falling through to the label made a cleared
        // text box read back as "Text editor", which a model takes for
        // its contents.
        try
        {
            if (el.TryGetCurrentPattern(ValuePattern.Pattern, out object value))
            {
                return Program.UiaOk(new
                {
                    text = ((ValuePattern)value).Current.Value ?? string.Empty,
                });
            }
        }
        catch
        {
            // Pattern present but unusable; try the text one.
        }

        try
        {
            if (el.TryGetCurrentPattern(TextPattern.Pattern, out object text))
            {
                return Program.UiaOk(new
                {
                    text = ((TextPattern)text).DocumentRange.GetText(20000)
                        ?? string.Empty,
                });
            }
        }
        catch
        {
            // Neither: the label is the best answer available.
        }

        return Program.UiaOk(new { text = SafeName(el) });
    }

    private string OpSelect(JsonElement root)
    {
        string item = ReadString(root, "item") ?? string.Empty;

        if (string.IsNullOrWhiteSpace(item))
        {
            throw new UiaFailure("'select' needs an item.");
        }

        AutomationElement el = Resolve(
            ReadInt(root, "ref"),
            ReadString(root, "name")
        );

        // The control itself may be the item (a list row, a tab).
        try
        {
            if (el.TryGetCurrentPattern(
                    SelectionItemPattern.Pattern,
                    out object own)
                && SafeName(el).Equals(item, StringComparison.OrdinalIgnoreCase))
            {
                ((SelectionItemPattern)own).Select();
                return Program.UiaOk(new { selected = item });
            }
        }
        catch
        {
            // Fall through.
        }

        // Otherwise it is a container: open it and pick the child.
        try
        {
            if (el.TryGetCurrentPattern(
                    ExpandCollapsePattern.Pattern,
                    out object expand))
            {
                ((ExpandCollapsePattern)expand).Expand();
                Thread.Sleep(220);
            }
        }
        catch
        {
            // Combo boxes that cannot expand may still list children.
        }

        AutomationElement? match = FindDescendantNamed(el, item);

        if (match is not null)
        {
            try
            {
                if (match.TryGetCurrentPattern(
                        SelectionItemPattern.Pattern,
                        out object pick))
                {
                    ((SelectionItemPattern)pick).Select();
                    return Program.UiaOk(new { selected = item });
                }
            }
            catch
            {
                // Fall through to clicking it.
            }

            PrepareForClick(match);

            if (CenterOf(match, out int mx, out int my)
                && Program.UiaClickAt(mx, my, "left", false))
            {
                return Program.UiaOk(new { selected = item });
            }
        }

        // Editable combo boxes accept the value directly.
        try
        {
            if (el.TryGetCurrentPattern(ValuePattern.Pattern, out object value))
            {
                var pattern = (ValuePattern)value;

                if (!pattern.Current.IsReadOnly)
                {
                    pattern.SetValue(item);
                    return Program.UiaOk(new { selected = item });
                }
            }
        }
        catch
        {
            // Nothing left to try.
        }

        throw new UiaFailure($"could not select '{item}'");
    }

    private static AutomationElement? FindDescendantNamed(
        AutomationElement root,
        string wanted)
    {
        string want = wanted.Trim().ToLowerInvariant();

        foreach (AutomationElement el in Descendants(root, 4, 600))
        {
            try
            {
                string name = (el.Cached.Name ?? string.Empty)
                    .Trim()
                    .ToLowerInvariant();

                if (name == want)
                {
                    return el;
                }
            }
            catch
            {
                // Skip.
            }
        }

        foreach (AutomationElement el in Descendants(root, 4, 600))
        {
            try
            {
                string name = (el.Cached.Name ?? string.Empty)
                    .Trim()
                    .ToLowerInvariant();

                if (name.Length > 0
                    && name.Contains(want, StringComparison.Ordinal))
                {
                    return el;
                }
            }
            catch
            {
                // Skip.
            }
        }

        return null;
    }

    private string OpExpand(JsonElement root)
    {
        AutomationElement el = Resolve(
            ReadInt(root, "ref"),
            ReadString(root, "name")
        );

        string label = Label(el);

        try
        {
            if (el.TryGetCurrentPattern(
                    ExpandCollapsePattern.Pattern,
                    out object expand))
            {
                ((ExpandCollapsePattern)expand).Expand();
                return Program.UiaOk(new { expanded = label });
            }
        }
        catch
        {
            // Fall through to a click.
        }

        PrepareForClick(el);

        if (CenterOf(el, out int x, out int y)
            && Program.UiaClickAt(x, y, "left", false))
        {
            return Program.UiaOk(new { expanded = label });
        }

        throw new UiaFailure("could not expand the control");
    }

    private string OpScroll(JsonElement root)
    {
        string direction =
            (ReadString(root, "direction") ?? "down").ToLowerInvariant();

        if (direction is not ("up" or "down" or "left" or "right"))
        {
            throw new UiaFailure("scroll direction must be up/down/left/right");
        }

        int amount = Math.Max(1, ReadInt(root, "amount") ?? 3);

        AutomationElement? target = null;

        int? reference = ReadInt(root, "ref");
        string? name = ReadString(root, "name");

        if (reference is not null || !string.IsNullOrWhiteSpace(name))
        {
            try
            {
                target = Resolve(reference, name);
            }
            catch (UiaFailure)
            {
                target = null;
            }
        }

        target ??= _window;

        if (target is null)
        {
            throw new UiaFailure("nothing to scroll - focus a window first");
        }

        try
        {
            if (target.TryGetCurrentPattern(
                    ScrollPattern.Pattern,
                    out object scroll))
            {
                var pattern = (ScrollPattern)scroll;

                for (int i = 0; i < amount; i++)
                {
                    if (direction is "up" or "down")
                    {
                        pattern.ScrollVertical(
                            direction == "down"
                                ? ScrollAmount.SmallIncrement
                                : ScrollAmount.SmallDecrement
                        );
                    }
                    else
                    {
                        pattern.ScrollHorizontal(
                            direction == "right"
                                ? ScrollAmount.SmallIncrement
                                : ScrollAmount.SmallDecrement
                        );
                    }
                }

                return Program.UiaOk(new { scrolled = $"scrolled {direction}" });
            }
        }
        catch
        {
            // Fall through to the wheel.
        }

        if (direction is "up" or "down")
        {
            if (CenterOf(target, out int x, out int y))
            {
                Program.UiaMoveCursor(x, y);
            }

            if (Program.UiaWheel(direction == "down" ? -amount : amount))
            {
                return Program.UiaOk(new
                {
                    scrolled = $"scrolled {direction} (wheel)",
                });
            }
        }

        string key = direction switch
        {
            "down" => "pagedown",
            "up" => "pageup",
            "left" => "left",
            _ => "right",
        };

        for (int i = 0; i < Math.Min(amount, 10); i++)
        {
            Program.UiaKeyChord(new[] { key });
        }

        return Program.UiaOk(new
        {
            scrolled = $"scrolled {direction} (keyboard)",
        });
    }

    private string OpMenu(JsonElement root)
    {
        string path = ReadString(root, "path") ?? string.Empty;

        if (string.IsNullOrWhiteSpace(path))
        {
            throw new UiaFailure("'menu' needs a path.");
        }

        // Both separators people write, and both at once: replacing '>'
        // with '->' first turned 'File->Exit' into 'File-->Exit', which
        // split into 'File-' and 'Exit'.
        string[] parts = Regex
            .Split(path, @"\s*(?:->|>)\s*")
            .Select(p => p.Trim())
            .Where(p => p.Length > 0)
            .ToArray();

        string normalised = string.Join("->", parts);

        if (parts.Length == 0)
        {
            throw new UiaFailure($"could not read a menu path from '{path}'");
        }

        if (_window is null)
        {
            RereadLastWindow();
        }

        if (_window is null)
        {
            throw new UiaFailure("focus a window first");
        }

        // Modern Windows apps rarely have a real menu bar, so each part
        // is opened in turn and the tree re-read between clicks - the
        // next level does not exist until the previous one is open.
        foreach (string part in parts)
        {
            AutomationElement? item = FindDescendantNamed(_window, part);

            if (item is null)
            {
                RereadLastWindow();

                AutomationElement? window = _window;
                item = window is null
                    ? null
                    : FindDescendantNamed(window, part);
            }

            if (item is null)
            {
                throw new UiaFailure($"no menu item named '{part}'");
            }

            bool opened = false;

            try
            {
                if (item.TryGetCurrentPattern(
                        ExpandCollapsePattern.Pattern,
                        out object expand))
                {
                    ((ExpandCollapsePattern)expand).Expand();
                    opened = true;
                }
            }
            catch
            {
                // Fall through.
            }

            if (!opened && !TryPatternClick(item, doubleClick: false))
            {
                PrepareForClick(item);

                if (CenterOf(item, out int x, out int y))
                {
                    Program.UiaClickAt(x, y, "left", false);
                }
            }

            Thread.Sleep(320);
        }

        return Program.UiaOk(new { menu = normalised });
    }

    // ================================================================
    // Waiting
    // ================================================================

    /// <summary>
    /// Answers "is this on screen yet" WITHOUT touching the ref cache.
    ///
    /// It used to rebuild it, which quietly renumbered every ref the
    /// caller was holding: read the tree, check a button exists, then
    /// act on ref 0 and hit a different control entirely. Checking
    /// whether something is there must not move everything else.
    /// </summary>
    private bool ControlExists(string name, string? title)
    {
        string? specTitle = title ?? _specTitle;
        int? specPid = title is null ? _specPid : null;

        AutomationElement window;

        try
        {
            window = ResolveWindow(specTitle, specPid);
        }
        catch (UiaFailure)
        {
            return false;
        }

        string want = name.Trim().ToLowerInvariant();

        foreach (AutomationElement el in Descendants(window, DefaultMaxDepth, 4000))
        {
            try
            {
                if (!Actionable.Contains(TypeName(el, cached: true)))
                {
                    continue;
                }

                string candidate = (el.Cached.Name ?? string.Empty)
                    .Trim()
                    .ToLowerInvariant();

                if (candidate.Length > 0
                    && candidate.Contains(want, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            catch
            {
                // Element vanished mid-scan.
            }
        }

        return false;
    }

    private string OpExists(JsonElement root)
    {
        string name = ReadString(root, "name") ?? string.Empty;

        if (string.IsNullOrWhiteSpace(name))
        {
            throw new UiaFailure("'exists' needs a name.");
        }

        return Program.UiaOk(new
        {
            exists = ControlExists(name, ReadString(root, "window")),
        });
    }

    private string OpWaitFor(JsonElement root)
    {
        string name = ReadString(root, "name") ?? string.Empty;

        if (string.IsNullOrWhiteSpace(name))
        {
            throw new UiaFailure("'wait_for' needs a name.");
        }

        string? window = ReadString(root, "window");
        double timeout = ReadDouble(root, "timeout", 10.0);

        var clock = Stopwatch.StartNew();

        while (true)
        {
            if (ControlExists(name, window))
            {
                return Program.UiaOk(new { found = true });
            }

            if (clock.Elapsed.TotalSeconds >= timeout)
            {
                return Program.UiaOk(new { found = false });
            }

            Thread.Sleep(700);
        }
    }

    /// <summary>
    /// A freshly launched app is not usable the moment its window
    /// exists - it paints for a second or two first, and a tree read in
    /// that gap comes back empty, which the executor reads as a broken
    /// app. Waiting for real controls is the difference between "open
    /// Notepad and type" working and failing on a cold start.
    /// </summary>
    private string OpWaitReady(JsonElement root)
    {
        string? window = ReadString(root, "window");
        int? pid = ReadInt(root, "pid");
        double timeout = ReadDouble(root, "timeout", 25.0);
        int minimum = ReadInt(root, "min_controls") ?? 3;

        var clock = Stopwatch.StartNew();

        while (true)
        {
            try
            {
                AutomationElement el = ResolveWindow(window, pid);
                _window = el;
                _specTitle = window;
                _specPid = pid;
                _windowTitle = SafeName(el);

                BuildRecords(Descendants(el, DefaultMaxDepth, 4000), null, 80);

                if (_controls.Count >= minimum)
                {
                    return Program.UiaOk(new
                    {
                        ready = true,
                        window = _windowTitle,
                        count = _controls.Count,
                    });
                }
            }
            catch (UiaFailure)
            {
                // The window is not up yet.
            }

            if (clock.Elapsed.TotalSeconds >= timeout)
            {
                return Program.UiaOk(new { ready = false });
            }

            Thread.Sleep(900);
        }
    }

    // ================================================================
    // JSON helpers
    // ================================================================

    private static string? ReadString(JsonElement root, string key)
    {
        if (!root.TryGetProperty(key, out JsonElement el))
        {
            return null;
        }

        return el.ValueKind switch
        {
            JsonValueKind.String => el.GetString(),
            JsonValueKind.Null => null,
            JsonValueKind.Number => el.ToString(),
            _ => null,
        };
    }

    private static int? ReadInt(JsonElement root, string key)
    {
        if (!root.TryGetProperty(key, out JsonElement el))
        {
            return null;
        }

        if (el.ValueKind == JsonValueKind.Number
            && el.TryGetInt32(out int number))
        {
            return number;
        }

        // Models pass refs as "0" as readily as 0.
        if (el.ValueKind == JsonValueKind.String
            && int.TryParse(
                el.GetString(),
                NumberStyles.Integer,
                CultureInfo.InvariantCulture,
                out int parsed))
        {
            return parsed;
        }

        return null;
    }

    private static double ReadDouble(
        JsonElement root,
        string key,
        double fallback)
    {
        if (!root.TryGetProperty(key, out JsonElement el))
        {
            return fallback;
        }

        if (el.ValueKind == JsonValueKind.Number
            && el.TryGetDouble(out double number))
        {
            return number;
        }

        if (el.ValueKind == JsonValueKind.String
            && double.TryParse(
                el.GetString(),
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out double parsed))
        {
            return parsed;
        }

        return fallback;
    }
}
