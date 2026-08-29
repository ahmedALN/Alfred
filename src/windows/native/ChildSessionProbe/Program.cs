using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace Alfred.ChildSessionProbe
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Console.WriteLine(
                "Alfred Child Session Probe"
            );

            Console.WriteLine(
                "=========================="
            );

            Console.WriteLine();

            PrintChildSessionState();

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(
                false
            );

            Application.Run(
                new ProbeForm()
            );
        }

        private static void PrintChildSessionState()
        {
            if (!WTSIsChildSessionsEnabled(
                    out bool enabled))
            {
                throw new Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "WTSIsChildSessionsEnabled failed."
                );
            }

            Console.WriteLine(
                "Child sessions enabled: "
                + enabled
            );

            if (WTSGetChildSessionId(
                    out uint childSessionId))
            {
                if (childSessionId == uint.MaxValue)
                {
                    Console.WriteLine(
                        "Existing child session: none"
                    );
                }
                else
                {
                    Console.WriteLine(
                        "Existing child session: "
                        + childSessionId
                    );
                }
            }
            else
            {
                Console.WriteLine(
                    "Existing child session: none"
                );
            }

            Console.WriteLine();
        }

        private sealed class ProbeForm : Form
        {
            // Fixed geometry for Alfred's session. Stable coordinates
            // matter more than matching the host window.
            private const int ChildDesktopWidth = 1600;
            private const int ChildDesktopHeight = 900;

            private readonly RdpHostControl _host;
            private readonly RdpEventSink _sink;

            private dynamic _ocx;

            private bool _connected;
            private bool _loginComplete;

            public ProbeForm()
            {
                Text =
                    "Alfred Child Session Probe";

                Width = 1280;
                Height = 800;

                StartPosition =
                    FormStartPosition.CenterScreen;

                BackColor =
                    System.Drawing.Color.Black;

                _sink =
                    new RdpEventSink();

                _sink.Connecting +=
                    OnConnecting;

                _sink.Connected +=
                    OnConnected;

                _sink.LoginComplete +=
                    OnLoginComplete;

                _sink.Disconnected +=
                    OnDisconnected;

                _sink.FatalError +=
                    OnFatalError;

                _host =
                    new RdpHostControl();

                _host.Sink =
                    _sink;

                _host.Dock =
                    DockStyle.Fill;

                Controls.Add(
                    _host
                );

                Shown +=
                    OnShown;
            }

            private void OnShown(
                object sender,
                EventArgs e)
            {
                try
                {
                    Console.WriteLine(
                        "Creating RDP ActiveX control..."
                    );

                    _host.CreateControl();

                    _ocx =
                        _host.Ocx;

                    Console.WriteLine(
                        "RDP ActiveX created."
                    );

                    Configure();

                    Console.WriteLine(
                        "Calling Connect()..."
                    );

                    _ocx.Connect();

                    Console.WriteLine(
                        "Connect() returned."
                    );

                    _connected =
                        true;
                }
                catch (Exception ex)
                {
                    ShowFatal(
                        "RDP initialization failed.",
                        ex
                    );
                }
            }

            private void Configure()
            {
                // ----------------------------------------------------
                // Basic RDP settings
                // ----------------------------------------------------

                _ocx.Server =
                    "localhost";

                // Pin the child desktop to a FIXED resolution rather
                // than the host window's size. A vision model reading
                // pixel coordinates needs the geometry to be stable
                // between runs; sizing to the window makes every
                // learned coordinate resolution-dependent.
                _ocx.DesktopWidth =
                    ChildDesktopWidth;

                _ocx.DesktopHeight =
                    ChildDesktopHeight;

                _ocx.ColorDepth =
                    32;

                // ----------------------------------------------------
                // Advanced settings
                // ----------------------------------------------------

                dynamic advanced =
                    null;

                try
                {
                    advanced =
                        _ocx.AdvancedSettings9;
                }
                catch
                {
                    try
                    {
                        advanced =
                            _ocx.AdvancedSettings8;
                    }
                    catch
                    {
                        // Optional.
                    }
                }

                if (advanced != null)
                {
                    TrySet(
                        "EnableCredSspSupport",
                        () =>
                            advanced.EnableCredSspSupport =
                                true
                    );

                    TrySet(
                        "AuthenticationLevel",
                        () =>
                            advanced.AuthenticationLevel =
                                0
                    );

                    TrySet(
                        "DisplayConnectionBar",
                        () =>
                            advanced.DisplayConnectionBar =
                                false
                    );

                    // ------------------------------------------------
                    // Keep the two sessions genuinely separate.
                    //
                    // Device/resource redirection is ON by default, and
                    // that includes the CLIPBOARD - without this, the
                    // child session shares the user's clipboard, which
                    // is exactly the kind of interference Alfred is
                    // supposed to avoid.
                    // ------------------------------------------------

                    TrySet(
                        "DisableRdpdr",
                        () =>
                            advanced.DisableRdpdr =
                                true
                    );

                    TrySet(
                        "RedirectClipboard",
                        () =>
                            advanced.RedirectClipboard =
                                false
                    );

                    TrySet(
                        "RedirectDrives",
                        () =>
                            advanced.RedirectDrives =
                                false
                    );

                    TrySet(
                        "RedirectPrinters",
                        () =>
                            advanced.RedirectPrinters =
                                false
                    );

                    TrySet(
                        "SmartSizing",
                        () =>
                            advanced.SmartSizing =
                                true
                    );

                    TrySet(
                        "EnableAutoReconnect",
                        () =>
                            advanced.EnableAutoReconnect =
                                true
                    );

                    TrySet(
                        "PerformanceFlags",
                        () =>
                            advanced.PerformanceFlags =
                                0x00000190
                    );
                }

                // ----------------------------------------------------
                // Extended settings
                //
                // This is the critical child-session setting.
                // ----------------------------------------------------

                var extended =
                    (IMsRdpExtendedSettings)
                    _host.Ocx;

                object childSession =
                    true;

                extended.set_Property(
                    "ConnectToChildSession",
                    ref childSession
                );

                // Loopback-specific performance options.
                TryExtended(
                    extended,
                    "EnableHardwareMode",
                    true
                );

                TryExtended(
                    extended,
                    "EnableFrameBufferRedirection",
                    true
                );

                Console.WriteLine(
                    "ConnectToChildSession = true"
                );
            }

            private void OnConnecting(
                object sender,
                EventArgs e)
            {
                Console.WriteLine(
                    "[RDP] Connecting..."
                );
            }

            private void OnConnected(
                object sender,
                EventArgs e)
            {
                Console.WriteLine(
                    "[RDP] Connected."
                );

                _connected =
                    true;

                CheckChildSession();
            }

            private void OnLoginComplete(
                object sender,
                EventArgs e)
            {
                Console.WriteLine(
                    "[RDP] Login complete."
                );

                _loginComplete =
                    true;

                CheckChildSession();
            }

            private void OnDisconnected(
                object sender,
                RdpDisconnectedEventArgs e)
            {
                Console.Error.WriteLine(
                    "[RDP] Disconnected. "
                    + $"Reason=0x{e.Reason:X}"
                );

                CheckChildSession();

                if (!IsDisposed)
                {
                    BeginInvoke(
                        new Action(
                            () =>
                                Text =
                                    "Alfred Child Session "
                                    + "(Disconnected)"
                        )
                    );
                }
            }

            private void OnFatalError(
                object sender,
                RdpFatalErrorEventArgs e)
            {
                Console.Error.WriteLine(
                    "[RDP] Fatal error: "
                    + e.Code
                );
            }

            private void CheckChildSession()
            {
                if (!WTSGetChildSessionId(
                        out uint sessionId))
                {
                    return;
                }

                if (sessionId ==
                    uint.MaxValue)
                {
                    return;
                }

                Console.WriteLine(
                    "================================"
                );

                Console.WriteLine(
                    "CHILD SESSION ID: "
                    + sessionId
                );

                Console.WriteLine(
                    "Connected: "
                    + _connected
                );

                Console.WriteLine(
                    "Login complete: "
                    + _loginComplete
                );

                Console.WriteLine(
                    "================================"
                );
            }

            private void ShowFatal(
                string heading,
                Exception exception)
            {
                Console.Error.WriteLine(
                    heading
                );

                Console.Error.WriteLine(
                    exception
                );

                MessageBox.Show(
                    this,
                    exception.ToString(),
                    heading,
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }

            private static void TrySet(
                string name,
                Action action)
            {
                try
                {
                    action();
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(
                        $"Optional RDP property "
                        + $"{name} failed: "
                        + $"{ex.Message}"
                    );
                }
            }

            private static void TryExtended(
                IMsRdpExtendedSettings extended,
                string name,
                bool value)
            {
                try
                {
                    object boxed =
                        value;

                    extended.set_Property(
                        name,
                        ref boxed
                    );
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(
                        $"Optional extended property "
                        + $"{name} failed: "
                        + $"{ex.Message}"
                    );
                }
            }
        }

        // ============================================================
        // RDP host
        // ============================================================

        [ComImport]
        [Guid(
            "A0C63C30-F08D-4AB4-907C-34905D770C7D")]
        [InterfaceType(
            ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMsRdpClient
        {
        }

        internal sealed class RdpHostControl : AxHost
        {
            private const string Clsid =
                "a0c63c30-f08d-4ab4-907c-34905d770c7d";

            private AxHost.ConnectionPointCookie _cookie;

            public RdpEventSink Sink
            {
                get;
                set;
            }

            public RdpHostControl()
                : base(Clsid)
            {
            }

            public object Ocx
            {
                get
                {
                    return GetOcx();
                }
            }

            protected override void CreateSink()
            {
                base.CreateSink();

                if (Sink == null)
                {
                    return;
                }

                try
                {
                    _cookie =
                        new AxHost.ConnectionPointCookie(
                            GetOcx(),
                            Sink,
                            typeof(
                                IMsTscAxEvents
                            )
                        );
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(
                        "RDP event subscription failed: "
                        + ex.Message
                    );
                }
            }

            protected override void DetachSink()
            {
                try
                {
                    if (_cookie != null)
                    {
                        _cookie.Disconnect();
                        _cookie = null;
                    }
                }
                catch
                {
                }

                base.DetachSink();
            }
        }

        // ============================================================
        // Extended settings
        // ============================================================

        [ComImport]
        [Guid(
            "302D8188-0052-4807-806A-362B628F9AC5")]
        [InterfaceType(
            ComInterfaceType.InterfaceIsIUnknown)]
        internal interface IMsRdpExtendedSettings
        {
            void set_Property(
                [In]
                [MarshalAs(
                    UnmanagedType.BStr)]
                string propertyName,

                [In]
                [MarshalAs(
                    UnmanagedType.Struct)]
                ref object value
            );

            [return: MarshalAs(
                UnmanagedType.Struct)]
            object get_Property(
                [In]
                [MarshalAs(
                    UnmanagedType.BStr)]
                string propertyName
            );
        }

        // ============================================================
        // RDP events
        // ============================================================

        [ComImport]
        [Guid(
            "336D5562-EFA8-482E-8CB3-C5C0FC7A7DB6")]
        [InterfaceType(
            ComInterfaceType.InterfaceIsIDispatch)]
        internal interface IMsTscAxEvents
        {
            [DispId(1)]
            void OnConnecting();

            [DispId(2)]
            void OnConnected();

            [DispId(3)]
            void OnLoginComplete();

            [DispId(4)]
            void OnDisconnected(
                int discReason
            );

            [DispId(10)]
            void OnFatalError(
                int errorCode
            );
        }

        internal sealed class RdpEventSink
            : IMsTscAxEvents
        {
            public event EventHandler Connecting;
            public event EventHandler Connected;
            public event EventHandler LoginComplete;

            public event EventHandler<RdpDisconnectedEventArgs>
                Disconnected;

            public event EventHandler<RdpFatalErrorEventArgs>
                FatalError;

            public void OnConnecting()
            {
                Connecting?.Invoke(
                    this,
                    EventArgs.Empty
                );
            }

            public void OnConnected()
            {
                Connected?.Invoke(
                    this,
                    EventArgs.Empty
                );
            }

            public void OnLoginComplete()
            {
                LoginComplete?.Invoke(
                    this,
                    EventArgs.Empty
                );
            }

            public void OnDisconnected(
                int discReason)
            {
                Disconnected?.Invoke(
                    this,
                    new RdpDisconnectedEventArgs(
                        discReason
                    )
                );
            }

            public void OnFatalError(
                int errorCode)
            {
                FatalError?.Invoke(
                    this,
                    new RdpFatalErrorEventArgs(
                        errorCode
                    )
                );
            }
        }

        internal sealed class RdpDisconnectedEventArgs
            : EventArgs
        {
            public int Reason {
                get;
            }

            public RdpDisconnectedEventArgs(
                int reason)
            {
                Reason =
                    reason;
            }
        }

        internal sealed class RdpFatalErrorEventArgs
            : EventArgs
        {
            public int Code {
                get;
            }

            public RdpFatalErrorEventArgs(
                int code)
            {
                Code =
                    code;
            }
        }

        // ============================================================
        // Child session APIs
        // ============================================================

        [DllImport(
            "wtsapi32.dll",
            SetLastError = true)]
        [return: MarshalAs(
            UnmanagedType.Bool)]
        private static extern bool
            WTSIsChildSessionsEnabled(
                [MarshalAs(
                    UnmanagedType.Bool)]
                out bool enabled
            );

        [DllImport(
            "wtsapi32.dll",
            SetLastError = true)]
        [return: MarshalAs(
            UnmanagedType.Bool)]
        private static extern bool
            WTSGetChildSessionId(
                out uint sessionId
            );
    }
}