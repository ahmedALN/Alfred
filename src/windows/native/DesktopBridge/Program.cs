using System;
using System.Runtime.InteropServices;

internal static class Program
{
    // ================================================================
    // Windows virtual desktop GUIDs
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
    // Entry point
    // ================================================================

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            Console.WriteLine(
                "Alfred DesktopBridge diagnostic startup."
            );

            if (args.Length == 0)
            {
                PrintUsage();
                return 1;
            }

            return args[0].ToLowerInvariant() switch
            {
                "diagnose" => Diagnose(),
                "count" => HandleCount(),
                "current" => HandleCurrent(),
                "window-desktop" => HandleWindowDesktop(args),
                "move-window" => HandleMoveWindow(args),
                _ => PrintUsageAndFail()
            };
        }
        catch (COMException ex)
        {
            Console.Error.WriteLine(
                $"COM ERROR: 0x{ex.HResult:X8}"
            );

            Console.Error.WriteLine(
                ex.Message
            );

            return 10;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(
                $"ERROR: {ex.GetType().Name}: {ex.Message}"
            );

            return 1;
        }
    }

    // ================================================================
    // Diagnostic bootstrap
    // ================================================================

    private static int Diagnose()
    {
        Console.WriteLine("[1] Creating ImmersiveShell...");

        Type? shellType =
            Type.GetTypeFromCLSID(
                ClsidImmersiveShell
            );

        if (shellType is null)
        {
            Console.Error.WriteLine(
                "FAILED: ImmersiveShell CLSID could not be resolved."
            );

            return 2;
        }

        var shell =
            (IServiceProvider)
            Activator.CreateInstance(shellType)!;

        Console.WriteLine(
            "[1] OK: ImmersiveShell created."
        );

        Console.WriteLine(
            "[2] Querying IVirtualDesktopManagerInternal..."
        );

        IVirtualDesktopManagerInternal internalManager =
            QueryService<
                IVirtualDesktopManagerInternal
            >(
                shell,
                ClsidVirtualDesktopManagerInternal,
                IidVirtualDesktopManagerInternal
            );

        Console.WriteLine(
            "[2] OK: IVirtualDesktopManagerInternal obtained."
        );

        Console.WriteLine(
            "[3] Calling GetCount()..."
        );

        int count =
            internalManager.GetCount();

        Console.WriteLine(
            $"[3] OK: GetCount returned {count}."
        );

        Console.WriteLine(
            "[4] Querying IApplicationViewCollection..."
        );

        IApplicationViewCollection views =
            QueryService<
                IApplicationViewCollection
            >(
                shell,
                IidApplicationViewCollection,
                IidApplicationViewCollection
            );

        Console.WriteLine(
            "[4] OK: IApplicationViewCollection obtained."
        );

        Console.WriteLine(
            "[5] Creating public VirtualDesktopManager..."
        );

        Type? publicManagerType =
            Type.GetTypeFromCLSID(
                ClsidVirtualDesktopManager
            );

        if (publicManagerType is null)
        {
            Console.Error.WriteLine(
                "FAILED: VirtualDesktopManager CLSID could not be resolved."
            );

            return 3;
        }

        var publicManager =
            (IVirtualDesktopManager)
            Activator.CreateInstance(
                publicManagerType
            )!;

        Console.WriteLine(
            "[5] OK: public VirtualDesktopManager created."
        );

        Console.WriteLine(
            "[6] Calling GetCurrentDesktop()..."
        );

        IVirtualDesktop current =
            internalManager.GetCurrentDesktop();

        if (current is null)
        {
            Console.Error.WriteLine(
                "FAILED: GetCurrentDesktop returned null."
            );

            return 4;
        }

        Guid currentId =
            current.GetId();

        Console.WriteLine(
            $"[6] OK: Current desktop ID = {currentId}"
        );

        Console.WriteLine(
            "[7] Enumerating desktops..."
        );

        internalManager.GetDesktops(
            out IObjectArray desktopArray
        );

        int arrayCount = 0;

        desktopArray.GetCount(
            out arrayCount
        );

        Console.WriteLine(
            $"[7] OK: IObjectArray contains {arrayCount} desktops."
        );

        ReleaseComObject(
            desktopArray
        );

        ReleaseComObject(
            publicManager
        );

        ReleaseComObject(
            views
        );

        ReleaseComObject(
            current
        );

        ReleaseComObject(
            internalManager
        );

        ReleaseComObject(
            shell
        );

        Console.WriteLine(
            "DIAGNOSTIC PASSED."
        );

        return 0;
    }

    // ================================================================
    // Normal commands
    // ================================================================

    private static int HandleCount()
    {
        using var bridge =
            new DesktopBridge();

        Console.WriteLine(
            $"Desktop count: {bridge.GetDesktopCount()}"
        );

        return 0;
    }

    private static int HandleCurrent()
    {
        using var bridge =
            new DesktopBridge();

        Console.WriteLine(
            $"Current desktop: "
            + $"{bridge.GetCurrentDesktopNumber()}"
        );

        return 0;
    }

    private static int HandleWindowDesktop(
        string[] args)
    {
        if (args.Length != 2 ||
            !TryParseHwnd(
                args[1],
                out IntPtr hwnd))
        {
            Console.Error.WriteLine(
                "Usage: DesktopBridge.exe "
                + "window-desktop <hwnd>"
            );

            return 1;
        }

        using var bridge =
            new DesktopBridge();

        int desktop =
            bridge.GetWindowDesktopNumber(
                hwnd
            );

        Console.WriteLine(
            $"Window {hwnd} is on Desktop {desktop}"
        );

        return 0;
    }

    private static int HandleMoveWindow(
        string[] args)
    {
        if (args.Length != 3 ||
            !TryParseHwnd(
                args[1],
                out IntPtr hwnd) ||
            !int.TryParse(
                args[2],
                out int desktop))
        {
            Console.Error.WriteLine(
                "Usage: DesktopBridge.exe "
                + "move-window <hwnd> <desktop>"
            );

            return 1;
        }

        using var bridge =
            new DesktopBridge();

        bridge.MoveWindowToDesktop(
            hwnd,
            desktop
        );

        int actualDesktop =
            bridge.GetWindowDesktopNumber(
                hwnd
            );

        Console.WriteLine(
            $"Window {hwnd} moved to Desktop "
            + $"{actualDesktop}"
        );

        return actualDesktop == desktop
            ? 0
            : 2;
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

        public int GetDesktopCount()
        {
            return _internalManager.GetCount();
        }

        public int GetCurrentDesktopNumber()
        {
            IVirtualDesktop current =
                _internalManager.GetCurrentDesktop();

            try
            {
                Guid id =
                    current.GetId();

                return GetDesktopNumber(id);
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
            Guid desktopId =
                _publicManager.GetWindowDesktopId(
                    hwnd
                );

            return GetDesktopNumber(
                desktopId
            );
        }

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
                    $"Desktop must be between 1 and {count}."
                );
            }

            int hr =
                _viewCollection.GetViewForHwnd(
                    hwnd,
                    out IApplicationView view
                );

            if (hr != 0)
            {
                throw new COMException(
                    "GetViewForHwnd failed.",
                    hr
                );
            }

            if (view is null)
            {
                throw new InvalidOperationException(
                    "GetViewForHwnd returned null."
                );
            }

            try
            {
                if (!_internalManager
                        .CanViewMoveDesktops(view))
                {
                    throw new InvalidOperationException(
                        "Windows reports that this "
                        + "view cannot move desktops."
                    );
                }

                IVirtualDesktop target =
                    GetDesktopByNumber(
                        desktopNumber
                    );

                try
                {
                    _internalManager
                        .MoveViewToDesktop(
                            view,
                            target
                        );
                }
                finally
                {
                    ReleaseComObject(
                        target
                    );
                }
            }
            finally
            {
                ReleaseComObject(
                    view
                );
            }
        }

        private IVirtualDesktop
            GetDesktopByNumber(
                int desktopNumber)
        {
            _internalManager.GetDesktops(
                out IObjectArray desktops
            );

            try
            {
                Guid iid =
                    IidVirtualDesktop;

                desktops.GetAt(
                    desktopNumber - 1,
                    ref iid,
                    out object desktop
                );

                return (IVirtualDesktop)
                    desktop;
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
            _internalManager.GetDesktops(
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
                $"Desktop {desktopId} was not found."
            );
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
    // IServiceProvider helper
    // ================================================================

    private static T QueryService<T>(
        IServiceProvider shell,
        Guid serviceGuid,
        Guid interfaceGuid)
        where T : class
    {
        Guid service = serviceGuid;
        Guid iid = interfaceGuid;

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
    // Utilities
    // ================================================================

    private static bool TryParseHwnd(
        string value,
        out IntPtr hwnd)
    {
        hwnd = IntPtr.Zero;

        if (value.StartsWith(
                "0x",
                StringComparison.OrdinalIgnoreCase))
        {
            if (!long.TryParse(
                    value[2..],
                    System.Globalization.NumberStyles.HexNumber,
                    null,
                    out long parsed))
            {
                return false;
            }

            hwnd = new IntPtr(parsed);

            return hwnd != IntPtr.Zero;
        }

        if (!long.TryParse(
                value,
                out long decimalValue))
        {
            return false;
        }

        hwnd = new IntPtr(decimalValue);

        return hwnd != IntPtr.Zero;
    }

    private static int PrintUsageAndFail()
    {
        PrintUsage();
        return 1;
    }

    private static void PrintUsage()
    {
        Console.WriteLine(
            """
            Alfred DesktopBridge

            Commands:

              diagnose
                  Diagnose Windows COM virtual-desktop access.

              count
                  Print the number of virtual desktops.

              current
                  Print the current desktop.

              window-desktop <hwnd>
                  Print the desktop containing a window.

              move-window <hwnd> <desktop>
                  Move a window without switching desktops.
            """
        );
    }

    private static void ReleaseComObject(
        object? value)
    {
        if (value is not null &&
            Marshal.IsComObject(value))
        {
            Marshal.ReleaseComObject(value);
        }
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

        void MoveWindowToDesktop(
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

        void SwitchDesktopAndMoveForegroundView(
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
            [MarshalAs(UnmanagedType.LPWStr)]
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

        int GetShowInSwitchers(
            out int flag
        );

        int SetShowInSwitchers(
            int flag
        );

        int GetScaleFactor(
            out int factor
        );

        int CanReceiveInput(
            out bool canReceiveInput
        );

        int GetCompatibilityPolicyType(
            out int flags
        );

        int SetCompatibilityPolicyType(
            int flags
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
            [MarshalAs(UnmanagedType.Interface)]
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
}