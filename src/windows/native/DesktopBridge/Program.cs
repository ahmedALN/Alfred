using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;

internal static class Program
{
    // ================================================================
    // COM identifiers
    // ================================================================

    private static readonly Guid ClsidImmersiveShell =
        new("C2F03A33-21F5-47FA-B4BB-156362A2F239");

    private static readonly Guid ClsidVirtualDesktopManager =
        new("AA509086-5CA9-4C25-8F95-589D3C07B48A");

    private static readonly Guid ClsidVirtualDesktopManagerInternal =
        new("C5E0CDCA-7B6E-41B2-9FC4-D93975CC467B");

    private static readonly Guid IidVirtualDesktopManager =
        new("A5CD92FF-29BE-454C-8D04-D82879FB3F1B");

    private static readonly Guid IidVirtualDesktopManagerInternal =
        new("53F5CA0B-158F-4124-900C-057158060B27");

    private static readonly Guid IidApplicationViewCollection =
        new("1841C6D7-4F9D-42C0-AF41-8747538F10E5");

    private static readonly Guid IidVirtualDesktop =
        new("3F07F4BE-B107-441A-AF0F-39D82529072C");

    // ================================================================
    // Win32 constants
    // ================================================================

    private const int SwHide = 0;
    private const int SwShowNoActivate = 4;

    private const uint GwOwner = 4;

    // ================================================================
    // Entry point
    // ================================================================

    [STAThread]
    private static int Main()
    {
        Console.Error.WriteLine(
            "Alfred DesktopBridge started."
        );

        try
        {
            using var bridge = new DesktopBridge();

            RunServer(bridge);

            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(
                $"FATAL: {ex.GetType().Name}: {ex.Message}"
            );

            return 1;
        }
    }

    // ================================================================
    // Persistent JSONL server
    // ================================================================

    private static void RunServer(
        DesktopBridge bridge)
    {
        while (true)
        {
            string? line =
                Console.ReadLine();

            if (line is null)
            {
                return;
            }

            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            string response;

            try
            {
                using JsonDocument document =
                    JsonDocument.Parse(line);

                response =
                    HandleRequest(
                        bridge,
                        document.RootElement
                    );
            }
            catch (JsonException ex)
            {
                response =
                    JsonSerializer.Serialize(
                        new
                        {
                            ok = false,
                            error = "invalid_json",
                            message = ex.Message
                        }
                    );
            }
            catch (COMException ex)
            {
                response =
                    JsonSerializer.Serialize(
                        new
                        {
                            ok = false,
                            error = "com_error",
                            hresult =
                                $"0x{ex.HResult:X8}",
                            message = ex.Message
                        }
                    );
            }
            catch (Win32Exception ex)
            {
                response =
                    JsonSerializer.Serialize(
                        new
                        {
                            ok = false,
                            error = "win32_error",
                            code = ex.NativeErrorCode,
                            message = ex.Message
                        }
                    );
            }
            catch (Exception ex)
            {
                response =
                    JsonSerializer.Serialize(
                        new
                        {
                            ok = false,
                            error = ex.GetType().Name,
                            message = ex.Message
                        }
                    );
            }

            Console.WriteLine(response);
            Console.Out.Flush();
        }
    }

    // ================================================================
    // Request dispatch
    // ================================================================

    private static string HandleRequest(
        DesktopBridge bridge,
        JsonElement request)
    {
        if (!request.TryGetProperty(
                "op",
                out JsonElement opElement))
        {
            return Error(
                "missing_operation",
                "Request must contain an 'op' property."
            );
        }

        string? operation =
            opElement.GetString();

        if (string.IsNullOrWhiteSpace(operation))
        {
            return Error(
                "invalid_operation",
                "'op' must be a non-empty string."
            );
        }

        return operation.ToLowerInvariant() switch
        {
            "ping" =>
                JsonSerializer.Serialize(
                    new
                    {
                        ok = true,
                        pong = true
                    }
                ),

            "count" =>
                JsonSerializer.Serialize(
                    new
                    {
                        ok = true,
                        count =
                            bridge.GetDesktopCount()
                    }
                ),

            "current" =>
                JsonSerializer.Serialize(
                    new
                    {
                        ok = true,
                        desktop =
                            bridge.GetCurrentDesktopNumber()
                    }
                ),

            "window_desktop" =>
                HandleWindowDesktop(
                    bridge,
                    request
                ),

            "can_move" =>
                HandleCanMove(
                    bridge,
                    request
                ),

            "move_window" =>
                HandleMoveWindow(
                    bridge,
                    request
                ),

            "launch_hidden" =>
                HandleLaunchHidden(
                    bridge,
                    request
                ),

            "shutdown" =>
                HandleShutdown(),

            _ =>
                Error(
                    "unknown_operation",
                    $"Unknown operation '{operation}'."
                )
        };
    }

    // ================================================================
    // Window operations
    // ================================================================

    private static string HandleWindowDesktop(
        DesktopBridge bridge,
        JsonElement request)
    {
        if (!TryGetHwnd(
                request,
                out IntPtr hwnd,
                out string error))
        {
            return Error(
                "invalid_hwnd",
                error
            );
        }

        int desktop =
            bridge.GetWindowDesktopNumber(
                hwnd
            );

        return JsonSerializer.Serialize(
            new
            {
                ok = true,
                hwnd = hwnd.ToInt64(),
                desktop
            }
        );
    }

    private static string HandleCanMove(
        DesktopBridge bridge,
        JsonElement request)
    {
        if (!TryGetHwnd(
                request,
                out IntPtr hwnd,
                out string error))
        {
            return Error(
                "invalid_hwnd",
                error
            );
        }

        bool movable =
            bridge.CanMoveWindow(
                hwnd
            );

        return JsonSerializer.Serialize(
            new
            {
                ok = true,
                hwnd = hwnd.ToInt64(),
                movable
            }
        );
    }

    private static string HandleMoveWindow(
        DesktopBridge bridge,
        JsonElement request)
    {
        if (!TryGetHwnd(
                request,
                out IntPtr hwnd,
                out string hwndError))
        {
            return Error(
                "invalid_hwnd",
                hwndError
            );
        }

        if (!TryGetDesktop(
                request,
                out int desktop,
                out string desktopError))
        {
            return Error(
                "invalid_desktop",
                desktopError
            );
        }

        bridge.MoveWindowToDesktop(
            hwnd,
            desktop
        );

        int actualDesktop =
            bridge.GetWindowDesktopNumber(
                hwnd
            );

        return JsonSerializer.Serialize(
            new
            {
                ok = actualDesktop == desktop,
                hwnd = hwnd.ToInt64(),
                requested_desktop = desktop,
                actual_desktop = actualDesktop
            }
        );
    }

    // ================================================================
    // Hidden launch experiment
    // ================================================================

    private static string HandleLaunchHidden(
        DesktopBridge bridge,
        JsonElement request)
    {
        if (!request.TryGetProperty(
                "executable",
                out JsonElement executableElement))
        {
            return Error(
                "missing_executable",
                "Request must contain 'executable'."
            );
        }

        string? executable =
            executableElement.GetString();

        if (string.IsNullOrWhiteSpace(
                executable))
        {
            return Error(
                "invalid_executable",
                "'executable' must be a non-empty string."
            );
        }

        if (!request.TryGetProperty(
                "title",
                out JsonElement titleElement))
        {
            return Error(
                "missing_title",
                "Request must contain 'title'."
            );
        }

        string? title =
            titleElement.GetString();

        if (string.IsNullOrWhiteSpace(title))
        {
            return Error(
                "invalid_title",
                "'title' must be a non-empty string."
            );
        }

        if (!TryGetDesktop(
                request,
                out int desktop,
                out string desktopError))
        {
            return Error(
                "invalid_desktop",
                desktopError
            );
        }

        return bridge.LaunchHidden(
            executable,
            title,
            desktop
        );
    }

    // ================================================================
    // Request helpers
    // ================================================================

    private static bool TryGetHwnd(
        JsonElement request,
        out IntPtr hwnd,
        out string error)
    {
        hwnd = IntPtr.Zero;
        error = "";

        if (!request.TryGetProperty(
                "hwnd",
                out JsonElement element))
        {
            error =
                "Request must contain 'hwnd'.";

            return false;
        }

        if (!element.TryGetInt64(
                out long value))
        {
            error =
                "'hwnd' must be an integer.";

            return false;
        }

        if (value <= 0)
        {
            error =
                "'hwnd' must be greater than zero.";

            return false;
        }

        hwnd =
            new IntPtr(value);

        return true;
    }

    private static bool TryGetDesktop(
        JsonElement request,
        out int desktop,
        out string error)
    {
        desktop = 0;
        error = "";

        if (!request.TryGetProperty(
                "desktop",
                out JsonElement element))
        {
            error =
                "Request must contain 'desktop'.";

            return false;
        }

        if (!element.TryGetInt32(
                out desktop))
        {
            error =
                "'desktop' must be an integer.";

            return false;
        }

        if (desktop < 1)
        {
            error =
                "'desktop' must be at least 1.";

            return false;
        }

        return true;
    }

    private static string Error(
        string code,
        string message)
    {
        return JsonSerializer.Serialize(
            new
            {
                ok = false,
                error = code,
                message
            }
        );
    }

    private static string HandleShutdown()
    {
        Environment.Exit(0);

        return JsonSerializer.Serialize(
            new
            {
                ok = true
            }
        );
    }

    // ================================================================
    // DesktopBridge
    // ================================================================

    private sealed class DesktopBridge : IDisposable
    {
        private readonly IServiceProvider _shell;

        private readonly IVirtualDesktopManagerInternal
            _internalManager;

        private readonly IApplicationViewCollection
            _viewCollection;

        private readonly IVirtualDesktopManager
            _publicManager;

        public DesktopBridge()
        {
            Type? shellType =
                Type.GetTypeFromCLSID(
                    ClsidImmersiveShell
                );

            if (shellType is null)
            {
                throw new InvalidOperationException(
                    "Could not resolve ImmersiveShell."
                );
            }

            _shell =
                (IServiceProvider)
                Activator.CreateInstance(
                    shellType
                )!;

            _internalManager =
                QueryService<
                    IVirtualDesktopManagerInternal
                >(
                    _shell,
                    ClsidVirtualDesktopManagerInternal,
                    IidVirtualDesktopManagerInternal
                );

            _viewCollection =
                QueryService<
                    IApplicationViewCollection
                >(
                    _shell,
                    IidApplicationViewCollection,
                    IidApplicationViewCollection
                );

            Type? managerType =
                Type.GetTypeFromCLSID(
                    ClsidVirtualDesktopManager
                );

            if (managerType is null)
            {
                throw new InvalidOperationException(
                    "Could not resolve VirtualDesktopManager."
                );
            }

            _publicManager =
                (IVirtualDesktopManager)
                Activator.CreateInstance(
                    managerType
                )!;
        }

        // ------------------------------------------------------------
        // Desktop information
        // ------------------------------------------------------------

        public int GetDesktopCount()
        {
            return _internalManager.GetCount();
        }

        public int GetCurrentDesktopNumber()
        {
            IVirtualDesktop current =
                _internalManager
                    .GetCurrentDesktop();

            try
            {
                return GetDesktopNumber(
                    current.GetId()
                );
            }
            finally
            {
                ReleaseComObject(
                    current
                );
            }
        }

        public int GetWindowDesktopNumber(
            IntPtr hwnd)
        {
            Guid id =
                _publicManager
                    .GetWindowDesktopId(
                        hwnd
                    );

            return GetDesktopNumber(id);
        }

        // ------------------------------------------------------------
        // Window capability
        // ------------------------------------------------------------

        public bool CanMoveWindow(
            IntPtr hwnd)
        {
            int hr =
                _viewCollection
                    .GetViewForHwnd(
                        hwnd,
                        out IApplicationView view
                    );

            if (hr != 0 ||
                view is null)
            {
                return false;
            }

            try
            {
                return _internalManager
                    .CanViewMoveDesktops(
                        view
                    );
            }
            finally
            {
                ReleaseComObject(
                    view
                );
            }
        }

        // ------------------------------------------------------------
        // Existing window move
        // ------------------------------------------------------------

        public void MoveWindowToDesktop(
            IntPtr hwnd,
            int desktopNumber)
        {
            int count =
                GetDesktopCount();

            if (desktopNumber < 1 ||
                desktopNumber > count)
            {
                throw new ArgumentOutOfRangeException(
                    nameof(desktopNumber),
                    $"Desktop must be between 1 and "
                    + $"{count}."
                );
            }

            Guid targetId =
                GetDesktopId(
                    desktopNumber
                );

            int result =
                _publicManager
                    .MoveWindowToDesktop(
                        hwnd,
                        ref targetId
                    );

            if (result != 0)
            {
                throw new COMException(
                    $"MoveWindowToDesktop failed. "
                    + $"HRESULT=0x{result:X8}",
                    result
                );
            }
        }

        // ------------------------------------------------------------
        // Hidden launch
        // ------------------------------------------------------------

        public string LaunchHidden(
            string executable,
            string windowTitle,
            int desktopNumber)
        {
            int desktopCount =
                GetDesktopCount();

            if (desktopNumber < 1 ||
                desktopNumber > desktopCount)
            {
                throw new ArgumentOutOfRangeException(
                    nameof(desktopNumber),
                    $"Desktop must be between 1 and "
                    + $"{desktopCount}."
                );
            }

            Console.Error.WriteLine(
                $"Launching hidden: {executable}"
            );

            Console.Error.WriteLine(
                $"Target desktop: {desktopNumber}"
            );

            var startupInfo =
                new STARTUPINFO();

            startupInfo.cb =
                Marshal.SizeOf<STARTUPINFO>();

            startupInfo.dwFlags =
                StartfUseShowWindow;

            startupInfo.wShowWindow =
                SwHide;

            string commandLine =
                executable;

            bool created =
                CreateProcess(
                    null,
                    commandLine,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    false,
                    0,
                    IntPtr.Zero,
                    null,
                    ref startupInfo,
                    out PROCESS_INFORMATION processInfo
                );

            if (!created)
            {
                int error =
                    Marshal.GetLastWin32Error();

                throw new Win32Exception(
                    error,
                    $"CreateProcess failed for "
                    + $"'{executable}'."
                );
            }

            try
            {
                int pid =
                    unchecked(
                        (int)processInfo.dwProcessId
                    );

                Console.Error.WriteLine(
                    $"PID: {pid}"
                );

                IntPtr hwnd =
                    FindWindowForTitle(
                        windowTitle,
                        10_000
                    );

                if (hwnd == IntPtr.Zero)
                {
                    throw new TimeoutException(
                        $"Could not find a window titled "
                        + $"'{windowTitle}' within 10 seconds."
                    );
                }

                Console.Error.WriteLine(
                    $"Found HWND: {hwnd}"
                );

                ShowWindow(
                    hwnd,
                    SwHide
                );

                Console.Error.WriteLine(
                    "Window hidden."
                );

                Guid targetId =
                    GetDesktopId(
                        desktopNumber
                    );

                Console.Error.WriteLine(
                    "Moving hidden window..."
                );

                int moveResult =
                    _publicManager
                        .MoveWindowToDesktop(
                            hwnd,
                            ref targetId
                        );

                if (moveResult != 0)
                {
                    throw new COMException(
                        $"MoveWindowToDesktop failed. "
                        + $"HRESULT=0x{moveResult:X8}",
                        moveResult
                    );
                }

                int actualDesktop =
                    GetDesktopNumber(
                        _publicManager
                            .GetWindowDesktopId(
                                hwnd
                            )
                    );

                Console.Error.WriteLine(
                    $"Verified desktop: "
                    + $"{actualDesktop}"
                );

                if (actualDesktop !=
                    desktopNumber)
                {
                    throw new InvalidOperationException(
                        $"Window ended up on Desktop "
                        + $"{actualDesktop}, expected "
                        + $"{desktopNumber}."
                    );
                }

                ShowWindow(
                    hwnd,
                    SwShowNoActivate
                );

                Console.Error.WriteLine(
                    "Window shown."
                );

                return JsonSerializer.Serialize(
                    new
                    {
                        ok = true,
                        executable,
                        hwnd = hwnd.ToInt64(),
                        desktop = actualDesktop,
                        method =
                            "hidden-launch-public-manager"
                    }
                );
            }
            finally
            {
                CloseHandle(
                    processInfo.hProcess
                );

                CloseHandle(
                    processInfo.hThread
                );
            }
        }

        // ------------------------------------------------------------
        // Desktop helpers
        // ------------------------------------------------------------

        private Guid GetDesktopId(
            int desktopNumber)
        {
            IVirtualDesktop desktop =
                GetDesktopByNumber(
                    desktopNumber
                );

            try
            {
                return desktop.GetId();
            }
            finally
            {
                ReleaseComObject(
                    desktop
                );
            }
        }

        private IVirtualDesktop
            GetDesktopByNumber(
                int desktopNumber)
        {
            _internalManager
                .GetDesktops(
                    out IObjectArray desktops
                );

            try
            {
                Guid iid =
                    IidVirtualDesktop;

                desktops.GetAt(
                    desktopNumber - 1,
                    ref iid,
                    out object desktopObject
                );

                return (IVirtualDesktop)
                    desktopObject;
            }
            finally
            {
                ReleaseComObject(
                    desktops
                );
            }
        }

        private int GetDesktopNumber(
            Guid desktopId)
        {
            _internalManager
                .GetDesktops(
                    out IObjectArray desktops
                );

            try
            {
                int count =
                    _internalManager.GetCount();

                for (
                    int index = 0;
                    index < count;
                    index++)
                {
                    Guid iid =
                        IidVirtualDesktop;

                    desktops.GetAt(
                        index,
                        ref iid,
                        out object desktopObject
                    );

                    var desktop =
                        (IVirtualDesktop)
                        desktopObject;

                    try
                    {
                        if (desktop.GetId() ==
                            desktopId)
                        {
                            return index + 1;
                        }
                    }
                    finally
                    {
                        ReleaseComObject(
                            desktop
                        );
                    }
                }
            }
            finally
            {
                ReleaseComObject(
                    desktops
                );
            }

            throw new InvalidOperationException(
                $"Virtual desktop {desktopId} "
                + "was not found."
            );
        }

        // ------------------------------------------------------------
        // Window discovery
        // ------------------------------------------------------------

        private static IntPtr FindWindowForTitle(
            string title,
            int timeoutMs)
        {
            string wanted =
                title.Trim();

            long deadline =
                Environment.TickCount64
                + timeoutMs;

            while (
                Environment.TickCount64
                < deadline)
            {
                IntPtr found =
                    FindWindowByExactTitle(
                        wanted
                    );

                if (found != IntPtr.Zero)
                {
                    return found;
                }

                Thread.Sleep(50);
            }

            return IntPtr.Zero;
        }

        private static IntPtr FindWindowByExactTitle(
            string wantedTitle)
        {
            IntPtr found =
                IntPtr.Zero;

            EnumWindows(
                (hwnd, _) =>
                {
                    if (!IsWindowVisible(hwnd))
                    {
                        return true;
                    }

                    if (GetParent(hwnd) !=
                        IntPtr.Zero)
                    {
                        return true;
                    }

                    if (GetWindow(
                            hwnd,
                            GwOwner) !=
                        IntPtr.Zero)
                    {
                        return true;
                    }

                    int length =
                        GetWindowTextLength(hwnd);

                    if (length <= 0)
                    {
                        return true;
                    }

                    var buffer =
                        new System.Text.StringBuilder(
                            length + 1
                        );

                    GetWindowText(
                        hwnd,
                        buffer,
                        buffer.Capacity
                    );

                    if (
                        string.Equals(
                            buffer.ToString().Trim(),
                            wantedTitle,
                            StringComparison.OrdinalIgnoreCase
                        ))
                    {
                        found =
                            hwnd;

                        return false;
                    }

                    return true;
                },
                IntPtr.Zero
            );

            return found;
        }

        // ------------------------------------------------------------
        // COM cleanup
        // ------------------------------------------------------------

        private static void ReleaseComObject(
            object? value)
        {
            if (value is not null &&
                Marshal.IsComObject(value))
            {
                Marshal.ReleaseComObject(
                    value
                );
            }
        }

        public void Dispose()
        {
            ReleaseComObject(
                _publicManager
            );

            ReleaseComObject(
                _viewCollection
            );

            ReleaseComObject(
                _internalManager
            );

            ReleaseComObject(
                _shell
            );
        }
    }

    // ================================================================
    // COM service helper
    // ================================================================

    private static T QueryService<T>(
        IServiceProvider shell,
        Guid serviceGuid,
        Guid interfaceGuid)
        where T : class
    {
        Guid service =
            serviceGuid;

        Guid iid =
            interfaceGuid;

        object result =
            shell.QueryService(
                ref service,
                ref iid
            );

        if (result is not T typed)
        {
            throw new InvalidOperationException(
                $"QueryService returned an unexpected "
                + $"COM object for {typeof(T).Name}."
            );
        }

        return typed;
    }

    // ================================================================
    // COM interfaces
    // ================================================================

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "6D5140C1-7436-11CE-8034-00AA006009FA")]
    private interface IServiceProvider
    {
        [return: MarshalAs(
            UnmanagedType.IUnknown)]
        object QueryService(
            ref Guid service,
            ref Guid riid
        );
    }

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "A5CD92FF-29BE-454C-8D04-D82879FB3F1B")]
    private interface IVirtualDesktopManager
    {
        bool IsWindowOnCurrentVirtualDesktop(
            IntPtr topLevelWindow
        );

        Guid GetWindowDesktopId(
            IntPtr topLevelWindow
        );

        int MoveWindowToDesktop(
            IntPtr topLevelWindow,
            ref Guid desktopId
        );
    }

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "53F5CA0B-158F-4124-900C-057158060B27")]
    private interface IVirtualDesktopManagerInternal
    {
        int GetCount();

        void MoveViewToDesktop(
            IApplicationView view,
            IVirtualDesktop desktop
        );

        bool CanViewMoveDesktops(
            IApplicationView view
        );

        IVirtualDesktop GetCurrentDesktop();

        void GetDesktops(
            out IObjectArray desktops
        );

        [PreserveSig]
        int GetAdjacentDesktop(
            IVirtualDesktop from,
            int direction,
            out IVirtualDesktop desktop
        );

        void SwitchDesktop(
            IVirtualDesktop desktop
        );

        IVirtualDesktop CreateDesktop();

        void MoveDesktop(
            IVirtualDesktop desktop,
            int index
        );

        void RemoveDesktop(
            IVirtualDesktop desktop,
            IVirtualDesktop fallback
        );

        IVirtualDesktop FindDesktop(
            ref Guid desktopId
        );
    }

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "372E1D3B-38D3-42E4-A15B-8AB2B178F513")]
    private interface IApplicationView
    {
        int SetFocus();

        int SwitchTo();

        int TryInvokeBack(
            IntPtr callback
        );

        int GetThumbnailWindow(
            out IntPtr hwnd
        );

        int GetMonitor(
            out IntPtr monitor
        );

        int GetVisibility(
            out int visibility
        );

        int SetCloak(
            int cloakType,
            int unknown
        );

        int GetPosition(
            ref Guid guid,
            out IntPtr position
        );

        int SetPosition(
            ref IntPtr position
        );

        int InsertAfterWindow(
            IntPtr hwnd
        );

        int GetExtendedFramePosition(
            out long rect
        );

        int GetAppUserModelId(
            [MarshalAs(
                UnmanagedType.LPWStr)]
            out string id
        );

        int SetAppUserModelId(
            string id
        );

        int IsEqualByAppUserModelId(
            string id,
            out int result
        );

        int GetViewState(
            out uint state
        );

        int SetViewState(
            uint state
        );

        int GetNeediness(
            out int neediness
        );

        int GetLastActivationTimestamp(
            out ulong timestamp
        );

        int SetLastActivationTimestamp(
            ulong timestamp
        );

        int GetVirtualDesktopId(
            out Guid guid
        );

        int SetVirtualDesktopId(
            ref Guid guid
        );
    }

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "1841C6D7-4F9D-42C0-AF41-8747538F10E5")]
    private interface IApplicationViewCollection
    {
        int GetViews(
            out IObjectArray array
        );

        int GetViewsByZOrder(
            out IObjectArray array
        );

        int GetViewsByAppUserModelId(
            string id,
            out IObjectArray array
        );

        int GetViewForHwnd(
            IntPtr hwnd,
            out IApplicationView view
        );

        int GetViewForApplication(
            object application,
            out IApplicationView view
        );

        int GetViewForAppUserModelId(
            string id,
            out IApplicationView view
        );

        int GetViewInFocus(
            out IntPtr view
        );

        int Unknown1(
            out IntPtr view
        );

        void RefreshCollection();

        int RegisterForApplicationViewChanges(
            object listener,
            out int cookie
        );

        int UnregisterForApplicationViewChanges(
            int cookie
        );
    }

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "92CA9DCD-5622-4BBA-A805-5E9F541BD8C9")]
    private interface IObjectArray
    {
        void GetCount(
            out int count
        );

        void GetAt(
            int index,
            ref Guid iid,
            [MarshalAs(
                UnmanagedType.IUnknown)]
            out object value
        );
    }

    [ComImport]
    [InterfaceType(
        ComInterfaceType.InterfaceIsIUnknown)]
    [Guid(
        "3F07F4BE-B107-441A-AF0F-39D82529072C")]
    private interface IVirtualDesktop
    {
        bool IsViewVisible(
            IApplicationView view
        );

        Guid GetId();

        [return: MarshalAs(
            UnmanagedType.HString)]
        string GetName();

        [return: MarshalAs(
            UnmanagedType.HString)]
        string GetWallpaperPath();

        bool IsRemote();
    }

    // ================================================================
    // Win32 API
    // ================================================================

    private const int StartfUseShowWindow =
        0x00000001;

    [StructLayout(
        LayoutKind.Sequential,
        CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;

        public string? lpReserved;

        public string? lpDesktop;

        public string? lpTitle;

        public int dwX;

        public int dwY;

        public int dwXSize;

        public int dwYSize;

        public int dwXCountChars;

        public int dwYCountChars;

        public int dwFillAttribute;

        public int dwFlags;

        public short wShowWindow;

        public short cbReserved2;

        public IntPtr lpReserved2;

        public IntPtr hStdInput;

        public IntPtr hStdOutput;

        public IntPtr hStdError;
    }

    [StructLayout(
        LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;

        public IntPtr hThread;

        public uint dwProcessId;

        public uint dwThreadId;
    }

    [DllImport(
        "kernel32.dll",
        SetLastError = true,
        CharSet = CharSet.Unicode)]
    [return: MarshalAs(
        UnmanagedType.Bool)]
    private static extern bool CreateProcess(
        string? lpApplicationName,
        string lpCommandLine,
        IntPtr lpProcessAttributes,
        IntPtr lpThreadAttributes,
        bool bInheritHandles,
        uint dwCreationFlags,
        IntPtr lpEnvironment,
        string? lpCurrentDirectory,
        ref STARTUPINFO lpStartupInfo,
        out PROCESS_INFORMATION lpProcessInformation
    );

    [DllImport(
        "kernel32.dll",
        SetLastError = true)]
    [return: MarshalAs(
        UnmanagedType.Bool)]
    private static extern bool CloseHandle(
        IntPtr hObject
    );

    [DllImport(
        "user32.dll")]
    private static extern bool EnumWindows(
        EnumWindowsProc lpEnumFunc,
        IntPtr lParam
    );

    private delegate bool EnumWindowsProc(
        IntPtr hWnd,
        IntPtr lParam
    );

    [DllImport(
        "user32.dll")]
    private static extern bool IsWindowVisible(
        IntPtr hWnd
    );

    [DllImport(
        "user32.dll")]
    private static extern IntPtr GetParent(
        IntPtr hWnd
    );

    [DllImport(
        "user32.dll")]
    private static extern IntPtr GetWindow(
        IntPtr hWnd,
        uint uCmd
    );

    [DllImport(
        "user32.dll",
        CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextLength(
        IntPtr hWnd
    );

    [DllImport(
        "user32.dll",
        CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(
        IntPtr hWnd,
        System.Text.StringBuilder lpString,
        int nMaxCount
    );

    [DllImport(
        "user32.dll")]
    private static extern bool ShowWindow(
        IntPtr hWnd,
        int nCmdShow
    );
}