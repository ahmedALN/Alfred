using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

using Vortice.Direct3D;
using Vortice.Direct3D11;
using Vortice.DXGI;

using WinRT;

internal static class Program
{
private const string PipeName =
"Alfred.ChildInput.v1";


// ================================================================
// Input constants
// ================================================================

private const uint InputMouse = 0;
private const uint InputKeyboard = 1;

private const uint MouseLeftDown = 0x0002;
private const uint MouseLeftUp = 0x0004;

private const uint MouseRightDown = 0x0008;
private const uint MouseRightUp = 0x0010;

private const uint MouseMiddleDown = 0x0020;
private const uint MouseMiddleUp = 0x0040;

private const uint KeyUp = 0x0002;
private const uint Unicode = 0x0004;

// ================================================================
// Window constants
// ================================================================

private const int SwRestore = 9;
private const int SwShow = 5;

// ================================================================
// Windows Graphics Capture GUIDs
// ================================================================

private static readonly Guid GraphicsCaptureItemGuid =
    new Guid(
        "79C3F95B-31F7-4EC2-A464-632EF5D30760"
    );

private static readonly Guid
    GraphicsCaptureItemInteropGuid =
        new Guid(
            "3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"
        );

private static readonly Guid
    ActivationFactoryGuid =
        new Guid(
            "00000035-0000-0000-C000-000000000046"
        );

// ================================================================
// Global capture state
// ================================================================

private static CaptureController? _capture;

// ================================================================
// Entry point
// ================================================================

private static int Main(
    string[] args)
{
    Console.WriteLine(
        "Alfred ChildInputAgent"
    );

    Console.WriteLine(
        "====================="
    );

    Console.WriteLine();

    int currentSession =
        GetCurrentSessionId();

    Console.WriteLine(
        $"Current session: {currentSession}"
    );

    Console.WriteLine(
        $"Pipe: {PipeName}"
    );

    Console.WriteLine();

    if (!ValidateExpectedSession(
            args,
            currentSession))
    {
        return 2;
    }

    Console.WriteLine(
        "ChildInputAgent is ready."
    );

    Console.WriteLine(
        "Waiting for Alfred..."
    );

    Console.WriteLine();

    try
    {
        RunServer();

        return 0;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine();

        Console.Error.WriteLine(
            $"FATAL: {ex.GetType().Name}"
        );

        Console.Error.WriteLine(
            ex.Message
        );

        Console.Error.WriteLine(
            ex.StackTrace
        );

        return 10;
    }
    finally
    {
        _capture?.Dispose();
        _capture = null;
    }
}

// ================================================================
// Session helpers
// ================================================================

private static int GetCurrentSessionId()
{
    using Process process =
        Process.GetCurrentProcess();

    return process.SessionId;
}

private static bool ValidateExpectedSession(
    string[] args,
    int currentSession)
{
    if (args.Length == 0)
    {
        return true;
    }

    if (
        args.Length != 2 ||
        !string.Equals(
            args[0],
            "--session",
            StringComparison.OrdinalIgnoreCase))
    {
        Console.Error.WriteLine(
            "Usage: ChildInputAgent.exe --session <session-id>"
        );

        return false;
    }

    if (!int.TryParse(
            args[1],
            out int expectedSession))
    {
        Console.Error.WriteLine(
            "Invalid session ID."
        );

        return false;
    }

    Console.WriteLine(
        $"Expected session: {expectedSession}"
    );

    if (
        expectedSession !=
        currentSession)
    {
        Console.Error.WriteLine(
            $"Session mismatch. Expected "
            + $"{expectedSession}, running in "
            + $"{currentSession}."
        );

        return false;
    }

    Console.WriteLine(
        "Session check: PASS"
    );

    Console.WriteLine();

    return true;
}

// ================================================================
// Named pipe server
// ================================================================

private static void RunServer()
{
    while (true)
    {
        using var pipe =
            new NamedPipeServerStream(
                PipeName,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.CurrentUserOnly,
                512 * 1024,
                512 * 1024
            );

        Console.WriteLine(
            "Waiting for pipe client..."
        );

        pipe.WaitForConnection();

        Console.WriteLine(
            "Alfred connected."
        );

        try
        {
            using var reader =
                new StreamReader(
                    pipe,
                    new UTF8Encoding(false),
                    false,
                    512 * 1024,
                    true
                );

            using var writer =
                new StreamWriter(
                    pipe,
                    new UTF8Encoding(false),
                    512 * 1024,
                    true
                )
                {
                    AutoFlush = true
                };

            while (pipe.IsConnected)
            {
                string? request =
                    reader.ReadLine();

                if (request is null)
                {
                    break;
                }

                Console.WriteLine(
                    $"Request: {request}"
                );

                string response =
                    HandleRequest(
                        request
                    );

                writer.WriteLine(
                    response
                );

                Console.WriteLine(
                    $"Response length: {response.Length}"
                );

                if (
                    IsShutdown(
                        request))
                {
                    Console.WriteLine(
                        "Shutdown requested."
                    );

                    return;
                }
            }
        }
        catch (IOException)
        {
            Console.WriteLine(
                "Pipe client disconnected."
            );
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(
                $"Pipe error: "
                + $"{ex.GetType().Name}: "
                + $"{ex.Message}"
            );
        }

        Console.WriteLine();
    }
}

// ================================================================
// Request dispatcher
// ================================================================

private static string HandleRequest(
    string json)
{
    try
    {
        using JsonDocument document =
            JsonDocument.Parse(
                json
            );

        JsonElement root =
            document.RootElement;

        if (
            !root.TryGetProperty(
                "op",
                out JsonElement opElement))
        {
            return Error(
                "missing_op",
                "Request must contain 'op'."
            );
        }

        string? operation =
            opElement.GetString();

        if (
            string.IsNullOrWhiteSpace(
                operation))
        {
            return Error(
                "invalid_op",
                "'op' must be a non-empty string."
            );
        }

        switch (
            operation.ToLowerInvariant())
        {
            case "ping":
                return Success(
                    new
                    {
                        pong = true,
                        session =
                            GetCurrentSessionId()
                    }
                );

            case "session":
                return Success(
                    new
                    {
                        session =
                            GetCurrentSessionId()
                    }
                );

            case "activate":
                return HandleActivate(
                    root
                );

            case "mouse_move":
                return HandleMouseMove(
                    root
                );

            case "click":
                return HandleClick(
                    root
                );

            case "type":
                return HandleType(
                    root
                );

            case "capture_start":
                return HandleCaptureStart();

            case "screenshot":
                return HandleScreenshot();

            case "capture_stop":
                return HandleCaptureStop();

            case "shutdown":
                return Success(
                    new
                    {
                        shutting_down = true
                    }
                );

            default:
                return Error(
                    "unknown_op",
                    $"Unknown operation '{operation}'."
                );
        }
    }
    catch (JsonException ex)
    {
        return Error(
            "invalid_json",
            ex.Message
        );
    }
    catch (Exception ex)
    {
        return Error(
            ex.GetType().Name,
            ex.Message
        );
    }
}

// ================================================================
// Persistent capture commands
// ================================================================

private static string HandleCaptureStart()
{
    try
    {
        if (_capture is not null)
        {
            return Success(
                new
                {
                    started = true,
                    already_running = true,
                    width = _capture.Width,
                    height = _capture.Height,
                    session =
                        GetCurrentSessionId()
                }
            );
        }

        Console.WriteLine(
            "Starting child-session desktop capture..."
        );

        _capture =
            CaptureController
                .CreateForCurrentSession();

        _capture.Start();

        Console.WriteLine(
            $"Capture started: "
            + $"{_capture.Width}x"
            + $"{_capture.Height}"
        );

        return Success(
            new
            {
                started = true,
                already_running = false,
                width = _capture.Width,
                height = _capture.Height,
                session =
                    GetCurrentSessionId()
            }
        );
    }
    catch (Exception ex)
    {
        _capture?.Dispose();
        _capture = null;

        return Error(
            ex.GetType().Name,
            ex.Message
        );
    }
}

private static string HandleCaptureStop()
{
    if (_capture is null)
    {
        return Success(
            new
            {
                stopped = true,
                was_running = false
            }
        );
    }

    try
    {
        _capture.Dispose();
        _capture = null;
    }
    catch (Exception ex)
    {
        _capture = null;

        return Error(
            ex.GetType().Name,
            ex.Message
        );
    }

    Console.WriteLine(
        "Child-session desktop capture stopped."
    );

    return Success(
        new
        {
            stopped = true,
            was_running = true
        }
    );
}

private static string HandleScreenshot()
{
    try
    {
        if (_capture is null)
        {
            string startResponse =
                HandleCaptureStart();

            using JsonDocument startDocument =
                JsonDocument.Parse(
                    startResponse
                );

            JsonElement startRoot =
                startDocument.RootElement;

            if (
                !startRoot.TryGetProperty(
                    "ok",
                    out JsonElement okElement) ||
                !okElement.GetBoolean())
            {
                return startResponse;
            }
        }

        CaptureResult result =
            _capture!
                .CaptureLatestPng(
                    TimeSpan.FromSeconds(5)
                );

        return Success(
            new
            {
                operation = "screenshot",
                mime_type = "image/png",
                width = result.Width,
                height = result.Height,
                bytes = result.Bytes.Length,
                image_base64 =
                    Convert.ToBase64String(
                        result.Bytes
                    ),
                session =
                    GetCurrentSessionId()
            }
        );
    }
    catch (Exception ex)
    {
        return Error(
            ex.GetType().Name,
            ex.Message
        );
    }
}

// ================================================================
// Window activation
// ================================================================

private static string HandleActivate(
    JsonElement root)
{
    if (!TryGetInt64(
            root,
            "hwnd",
            out long hwndValue,
            out string error))
    {
        return Error(
            "invalid_hwnd",
            error
        );
    }

    IntPtr hwnd =
        new IntPtr(
            hwndValue
        );

    if (!IsWindow(hwnd))
    {
        return Error(
            "window_not_found",
            "The specified HWND does not exist."
        );
    }

    uint targetSession =
        GetWindowSessionId(
            hwnd
        );

    int currentSession =
        GetCurrentSessionId();

    if (
        targetSession !=
        (uint)currentSession)
    {
        return Error(
            "wrong_session",
            $"Window belongs to session "
            + $"{targetSession}; agent is in "
            + $"session {currentSession}."
        );
    }

    if (!ActivateWindow(
            hwnd))
    {
        return Error(
            "activation_failed",
            "Could not make the target window foreground."
        );
    }

    return Success(
        new
        {
            operation = "activate",
            hwnd = hwndValue,
            session = currentSession,
            foreground =
                GetForegroundWindow() ==
                hwnd
        }
    );
}

// ================================================================
// Mouse
// ================================================================

private static string HandleMouseMove(
    JsonElement root)
{
    if (!TryGetInt(
            root,
            "x",
            out int x,
            out string xError))
    {
        return Error(
            "invalid_x",
            xError
        );
    }

    if (!TryGetInt(
            root,
            "y",
            out int y,
            out string yError))
    {
        return Error(
            "invalid_y",
            yError
        );
    }

    if (!SetCursorPos(
            x,
            y))
    {
        int win32Error =
            Marshal.GetLastWin32Error();

        return Error(
            "mouse_move_failed",
            $"SetCursorPos failed with Win32 error {win32Error}."
        );
    }

    return Success(
        new
        {
            operation = "mouse_move",
            x,
            y,
            session =
                GetCurrentSessionId()
        }
    );
}

private static string HandleClick(
    JsonElement root)
{
    string button =
        "left";

    if (
        root.TryGetProperty(
            "button",
            out JsonElement buttonElement))
    {
        button =
            buttonElement.GetString()
            ?? "left";
    }

    bool clicked =
        button.ToLowerInvariant()
            switch
        {
            "left" =>
                SendMouseButton(
                    MouseLeftDown,
                    MouseLeftUp
                ),

            "right" =>
                SendMouseButton(
                    MouseRightDown,
                    MouseRightUp
                ),

            "middle" =>
                SendMouseButton(
                    MouseMiddleDown,
                    MouseMiddleUp
                ),

            _ => false
        };

    if (!clicked)
    {
        return Error(
            "click_failed",
            $"Unsupported mouse button '{button}'."
        );
    }

    return Success(
        new
        {
            operation = "click",
            button,
            session =
                GetCurrentSessionId()
        }
    );
}

private static bool SendMouseButton(
    uint downFlag,
    uint upFlag)
{
    INPUT[] inputs =
    {
        new INPUT
        {
            Type = InputMouse,

            Union =
                new InputUnion
                {
                    Mouse =
                        new MOUSEINPUT
                        {
                            Dx = 0,
                            Dy = 0,
                            MouseData = 0,
                            Flags = downFlag,
                            Time = 0,
                            ExtraInfo =
                                IntPtr.Zero
                        }
                }
        },

        new INPUT
        {
            Type = InputMouse,

            Union =
                new InputUnion
                {
                    Mouse =
                        new MOUSEINPUT
                        {
                            Dx = 0,
                            Dy = 0,
                            MouseData = 0,
                            Flags = upFlag,
                            Time = 0,
                            ExtraInfo =
                                IntPtr.Zero
                        }
                }
        }
    };

    uint sent =
        SendInput(
            (uint)inputs.Length,
            inputs,
            Marshal.SizeOf(
                typeof(INPUT)
            )
        );

    return sent ==
        inputs.Length;
}

// ================================================================
// Keyboard
// ================================================================

private static string HandleType(
    JsonElement root)
{
    if (
        !root.TryGetProperty(
            "text",
            out JsonElement textElement))
    {
        return Error(
            "missing_text",
            "Request must contain 'text'."
        );
    }

    string? text =
        textElement.GetString();

    if (text is null)
    {
        return Error(
            "invalid_text",
            "'text' must be a string."
        );
    }

    if (!SendUnicodeText(
            text))
    {
        return Error(
            "type_failed",
            "SendInput failed."
        );
    }

    return Success(
        new
        {
            operation = "type",
            characters = text.Length,
            session =
                GetCurrentSessionId()
        }
    );
}

private static bool SendUnicodeText(
    string text)
{
    foreach (char character in text)
    {
        INPUT[] inputs =
        {
            CreateUnicodeInput(
                character,
                false
            ),

            CreateUnicodeInput(
                character,
                true
            )
        };

        uint sent =
            SendInput(
                (uint)inputs.Length,
                inputs,
                Marshal.SizeOf(
                    typeof(INPUT)
                )
            );

        if (
            sent !=
            inputs.Length)
        {
            return false;
        }
    }

    return true;
}

private static INPUT CreateUnicodeInput(
    char character,
    bool keyUp)
{
    return new INPUT
    {
        Type =
            InputKeyboard,

        Union =
            new InputUnion
            {
                Keyboard =
                    new KEYBDINPUT
                    {
                        Vk = 0,
                        Scan = character,
                        Flags =
                            Unicode
                            |
                            (
                                keyUp
                                    ? KeyUp
                                    : 0
                            ),
                        Time = 0,
                        ExtraInfo =
                            IntPtr.Zero
                    }
            }
    };
}

// ================================================================
// Window activation
// ================================================================

private static bool ActivateWindow(
    IntPtr hwnd)
{
    if (!IsWindow(hwnd))
    {
        return false;
    }

    if (IsIconic(hwnd))
    {
        ShowWindow(
            hwnd,
            SwRestore
        );

        Thread.Sleep(100);
    }

    uint targetThread =
        GetWindowThreadProcessId(
            hwnd,
            out _
        );

    if (targetThread == 0)
    {
        return false;
    }

    uint currentThread =
        GetCurrentThreadId();

    IntPtr foreground =
        GetForegroundWindow();

    uint foregroundThread =
        foreground != IntPtr.Zero
            ? GetWindowThreadProcessId(
                foreground,
                out _
            )
            : 0;

    ShowWindow(
        hwnd,
        SwShow
    );

    BringWindowToTop(
        hwnd
    );

    SetForegroundWindow(
        hwnd
    );

    Thread.Sleep(100);

    if (
        GetForegroundWindow() ==
        hwnd)
    {
        return true;
    }

    bool attachedTarget =
        false;

    bool attachedForeground =
        false;

    try
    {
        if (
            targetThread !=
            currentThread)
        {
            attachedTarget =
                AttachThreadInput(
                    currentThread,
                    targetThread,
                    true
                );
        }

        if (
            foregroundThread != 0 &&
            foregroundThread != currentThread &&
            foregroundThread != targetThread)
        {
            attachedForeground =
                AttachThreadInput(
                    currentThread,
                    foregroundThread,
                    true
                );
        }

        ShowWindow(
            hwnd,
            SwShow
        );

        BringWindowToTop(
            hwnd
        );

        SetActiveWindow(
            hwnd
        );

        SetFocus(
            hwnd
        );

        SetForegroundWindow(
            hwnd
        );

        Thread.Sleep(100);

        return
            GetForegroundWindow() ==
            hwnd;
    }
    finally
    {
        if (attachedForeground)
        {
            AttachThreadInput(
                currentThread,
                foregroundThread,
                false
            );
        }

        if (attachedTarget)
        {
            AttachThreadInput(
                currentThread,
                targetThread,
                false
            );
        }
    }
}

// ================================================================
// Process/window helpers
// ================================================================

private static uint GetWindowProcessId(
    IntPtr hwnd)
{
    GetWindowThreadProcessId(
        hwnd,
        out uint pid
    );

    return pid;
}

private static uint GetWindowSessionId(
    IntPtr hwnd)
{
    uint pid =
        GetWindowProcessId(
            hwnd
        );

    if (pid == 0)
    {
        return uint.MaxValue;
    }

    try
    {
        using Process process =
            Process.GetProcessById(
                unchecked((int)pid)
            );

        return unchecked(
            (uint)process.SessionId
        );
    }
    catch
    {
        return uint.MaxValue;
    }
}

// ================================================================
// JSON helpers
// ================================================================

private static bool TryGetInt(
    JsonElement root,
    string name,
    out int value,
    out string error)
{
    value = 0;
    error = "";

    if (!root.TryGetProperty(
            name,
            out JsonElement element))
    {
        error =
            $"Missing '{name}'.";

        return false;
    }

    if (!element.TryGetInt32(
            out value))
    {
        error =
            $"'{name}' must be an integer.";

        return false;
    }

    return true;
}

private static bool TryGetInt64(
    JsonElement root,
    string name,
    out long value,
    out string error)
{
    value = 0;
    error = "";

    if (!root.TryGetProperty(
            name,
            out JsonElement element))
    {
        error =
            $"Missing '{name}'.";

        return false;
    }

    if (!element.TryGetInt64(
            out value))
    {
        error =
            $"'{name}' must be an integer.";

        return false;
    }

    if (value <= 0)
    {
        error =
            $"'{name}' must be greater than zero.";

        return false;
    }

    return true;
}

private static string Success(
    object data)
{
    return JsonSerializer.Serialize(
        new
        {
            ok = true,
            data
        }
    );
}

private static string Error(
    string error,
    string message)
{
    return JsonSerializer.Serialize(
        new
        {
            ok = false,
            error,
            message
        }
    );
}

private static bool IsShutdown(
    string json)
{
    try
    {
        using JsonDocument document =
            JsonDocument.Parse(
                json
            );

        if (
            !document.RootElement.TryGetProperty(
                "op",
                out JsonElement op))
        {
            return false;
        }

        return string.Equals(
            op.GetString(),
            "shutdown",
            StringComparison.OrdinalIgnoreCase
        );
    }
    catch
    {
        return false;
    }
}

// ================================================================
// Capture controller
// ================================================================

private sealed class CaptureController :
    IDisposable
{
    private readonly
        GraphicsCaptureItem _item;

    private readonly
        ID3D11Device _device;

    private readonly
        ID3D11DeviceContext _context;

    private readonly
        IDirect3DDevice _winrtDevice;

    private readonly
        Direct3D11CaptureFramePool _framePool;

    private readonly
        GraphicsCaptureSession _captureSession;

    private readonly
        object _sync =
            new object();

    private readonly
        AutoResetEvent _frameSignal =
            new AutoResetEvent(false);

    private bool _started;
    private bool _disposed;

    public int Width {
        get;
    }

    public int Height {
        get;
    }

    private CaptureController(
        GraphicsCaptureItem item)
    {
        _item =
            item;

        Width =
            item.Size.Width;

        Height =
            item.Size.Height;

        if (
            Width <= 0 ||
            Height <= 0)
        {
            throw new InvalidOperationException(
                "Capture item has invalid dimensions."
            );
        }

        Console.WriteLine(
            "Creating D3D11 device..."
        );

        var result =
            D3D11.D3D11CreateDevice(
                null,
                DriverType.Hardware,
                DeviceCreationFlags.BgraSupport,
                null,
                out ID3D11Device? device,
                out FeatureLevel featureLevel,
                out ID3D11DeviceContext? context
            );

        result.CheckError();

        _device =
            device
            ?? throw new InvalidOperationException(
                "D3D11 device creation returned null."
            );

        _context =
            context
            ?? throw new InvalidOperationException(
                "D3D11 context creation returned null."
            );

        Console.WriteLine(
            $"D3D11 feature level: {featureLevel}"
        );

        Console.WriteLine(
            "Creating WinRT IDirect3DDevice..."
        );

        _winrtDevice =
            CreateWinRtDevice(
                _device
            );

        Console.WriteLine(
            "WinRT Direct3D device: PASS"
        );

        Console.WriteLine(
            "Creating Direct3D11CaptureFramePool..."
        );

        _framePool =
            Direct3D11CaptureFramePool
                .CreateFreeThreaded(
                    _winrtDevice,
                    DirectXPixelFormat
                        .B8G8R8A8UIntNormalized,
                    3,
                    _item.Size
                );

        Console.WriteLine(
            "Frame pool: PASS"
        );

        _framePool.FrameArrived +=
            OnFrameArrived;

        _captureSession =
            _framePool.CreateCaptureSession(
                _item
            );

        Console.WriteLine(
            "Capture session: PASS"
        );
    }

    public static CaptureController
        CreateForCurrentSession()
    {
        List<IntPtr> monitors =
            EnumerateMonitors();

        if (monitors.Count == 0)
        {
            throw new InvalidOperationException(
                "No monitor is visible in this session."
            );
        }

        if (monitors.Count > 1)
        {
            Console.WriteLine(
                $"Session exposes {monitors.Count} monitors. "
                + "Using the first monitor."
            );
        }

        IntPtr hMonitor =
            monitors[0];

        Console.WriteLine(
            $"Selected HMONITOR: "
            + $"{hMonitor.ToInt64()}"
        );

        GraphicsCaptureItem item =
            CreateMonitorCaptureItem(
                hMonitor
            );

        return new CaptureController(
            item
        );
    }

    public void Start()
    {
        ThrowIfDisposed();

        if (_started)
        {
            return;
        }

        _captureSession.StartCapture();

        _started =
            true;

        Console.WriteLine(
            "Persistent child-session capture started."
        );
    }

    public CaptureResult
        CaptureLatestPng(
            TimeSpan timeout)
    {
        ThrowIfDisposed();

        if (!_started)
        {
            Start();
        }

        Direct3D11CaptureFrame? frame =
            null;

        lock (_sync)
        {
            frame =
                DrainToLatestFrameLocked();
        }

        if (frame is null)
        {
            bool signaled =
                _frameSignal.WaitOne(
                    timeout
                );

            if (!signaled)
            {
                throw new TimeoutException(
                    "Timed out waiting for a "
                    + "child-session desktop frame."
                );
            }

            lock (_sync)
            {
                frame =
                    DrainToLatestFrameLocked();
            }
        }

        if (frame is null)
        {
            throw new InvalidOperationException(
                "A frame signal was received, but no "
                + "capture frame was available."
            );
        }

        using (frame)
        {
            return EncodePngInMemory(
                frame
            );
        }
    }

    private Direct3D11CaptureFrame?
        DrainToLatestFrameLocked()
    {
        Direct3D11CaptureFrame? latest =
            null;

        while (true)
        {
            Direct3D11CaptureFrame? candidate =
                _framePool.TryGetNextFrame();

            if (candidate is null)
            {
                break;
            }

            latest?.Dispose();

            latest =
                candidate;
        }

        return latest;
    }

    private void OnFrameArrived(
        Direct3D11CaptureFramePool sender,
        object args)
    {
        try
        {
            _frameSignal.Set();
        }
        catch
        {
        }
    }

    private static CaptureResult
        EncodePngInMemory(
            Direct3D11CaptureFrame frame)
    {
        SoftwareBitmap bitmap =
            SoftwareBitmap
                .CreateCopyFromSurfaceAsync(
                    frame.Surface,
                    BitmapAlphaMode.Premultiplied
                )
                .AsTask()
                .GetAwaiter()
                .GetResult();

        using (bitmap)
        {
            SoftwareBitmap converted =
                SoftwareBitmap.Convert(
                    bitmap,
                    BitmapPixelFormat.Bgra8,
                    BitmapAlphaMode.Premultiplied
                );

            using (converted)
            {
                using var stream =
                    new InMemoryRandomAccessStream();

                BitmapEncoder encoder =
                    BitmapEncoder
                        .CreateAsync(
                            BitmapEncoder.PngEncoderId,
                            stream
                        )
                        .AsTask()
                        .GetAwaiter()
                        .GetResult();

                encoder.SetSoftwareBitmap(
                    converted
                );

                encoder.FlushAsync()
                    .AsTask()
                    .GetAwaiter()
                    .GetResult();

                if (
                    stream.Size >
                    int.MaxValue)
                {
                    throw new InvalidOperationException(
                        "Encoded screenshot is too large."
                    );
                }

                stream.Seek(
                    0
                );

                using var reader =
                    new DataReader(
                        stream.GetInputStreamAt(0)
                    );

                uint byteCount =
                    checked(
                        (uint)stream.Size
                    );

                reader.LoadAsync(
                        byteCount
                    )
                    .AsTask()
                    .GetAwaiter()
                    .GetResult();

                byte[] bytes =
                    new byte[
                        byteCount
                    ];

                reader.ReadBytes(
                    bytes
                );

                return new CaptureResult(
                    bytes,
                    converted.PixelWidth,
                    converted.PixelHeight
                );
            }
        }
    }

    private static IDirect3DDevice
        CreateWinRtDevice(
            ID3D11Device device)
    {
        using IDXGIDevice dxgiDevice =
            device.QueryInterface<
                IDXGIDevice
            >();

        uint hr =
            CreateDirect3D11DeviceFromDXGIDevice(
                dxgiDevice.NativePointer,
                out IntPtr graphicsDevice
            );

        if (hr != 0)
        {
            Marshal.ThrowExceptionForHR(
                unchecked(
                    (int)hr
                )
            );
        }

        if (
            graphicsDevice ==
            IntPtr.Zero)
        {
            throw new InvalidOperationException(
                "D3D11-to-WinRT device bridge returned null."
            );
        }

        try
        {
            return
                MarshalInterface<
                    IDirect3DDevice
                >
                .FromAbi(
                    graphicsDevice
                );
        }
        finally
        {
            Marshal.Release(
                graphicsDevice
            );
        }
    }

    private static List<IntPtr>
        EnumerateMonitors()
    {
        var result =
            new List<IntPtr>();

        MonitorEnumProc callback =
            (
                IntPtr hMonitor,
                IntPtr hdcMonitor,
                ref RECT rect,
                IntPtr data
            ) =>
            {
                result.Add(
                    hMonitor
                );

                return true;
            };

        if (!EnumDisplayMonitors(
                IntPtr.Zero,
                IntPtr.Zero,
                callback,
                IntPtr.Zero))
        {
            throw new InvalidOperationException(
                $"EnumDisplayMonitors failed with "
                + $"Win32 error "
                + $"{Marshal.GetLastWin32Error()}."
            );
        }

        return result;
    }

    private static GraphicsCaptureItem
        CreateMonitorCaptureItem(
            IntPtr hMonitor)
    {
        IntPtr factory =
            GetActivationFactory(
                "Windows.Graphics.Capture.GraphicsCaptureItem"
            );

        try
        {
            Guid interopGuid =
                GraphicsCaptureItemInteropGuid;

            int hr =
                Marshal.QueryInterface(
                    factory,
                    ref interopGuid,
                    out IntPtr interop
                );

            if (hr < 0)
            {
                Marshal.ThrowExceptionForHR(
                    hr
                );
            }

            if (interop == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "IGraphicsCaptureItemInterop is null."
                );
            }

            try
            {
                IntPtr vtable =
                    Marshal.ReadIntPtr(
                        interop
                    );

                IntPtr createForMonitorPointer =
                    Marshal.ReadIntPtr(
                        vtable,
                        IntPtr.Size * 4
                    );

                if (
                    createForMonitorPointer ==
                    IntPtr.Zero)
                {
                    throw new InvalidOperationException(
                        "CreateForMonitor pointer is null."
                    );
                }

                var createForMonitor =
                    Marshal
                        .GetDelegateForFunctionPointer<
                            CreateForMonitorDelegate
                        >(
                            createForMonitorPointer
                        );

                Guid itemGuid =
                    GraphicsCaptureItemGuid;

                int createHr =
                    createForMonitor(
                        interop,
                        hMonitor,
                        ref itemGuid,
                        out IntPtr itemPointer
                    );

                if (createHr < 0)
                {
                    Marshal.ThrowExceptionForHR(
                        createHr
                    );
                }

                if (
                    itemPointer ==
                    IntPtr.Zero)
                {
                    throw new InvalidOperationException(
                        "CreateForMonitor returned null."
                    );
                }

                try
                {
                    return
                        MarshalInterface<
                            GraphicsCaptureItem
                        >
                        .FromAbi(
                            itemPointer
                        );
                }
                finally
                {
                    Marshal.Release(
                        itemPointer
                    );
                }
            }
            finally
            {
                Marshal.Release(
                    interop
                );
            }
        }
        finally
        {
            Marshal.Release(
                factory
            );
        }
    }

    private static IntPtr
        GetActivationFactory(
            string runtimeClassName)
    {
        IntPtr hstring =
            IntPtr.Zero;

        int hr =
            WindowsCreateString(
                runtimeClassName,
                runtimeClassName.Length,
                out hstring
            );

        if (hr < 0)
        {
            Marshal.ThrowExceptionForHR(
                hr
            );
        }

        try
        {
            Guid iid =
                ActivationFactoryGuid;

            hr =
                RoGetActivationFactory(
                    hstring,
                    ref iid,
                    out IntPtr factory
                );

            if (hr < 0)
            {
                Marshal.ThrowExceptionForHR(
                    hr
                );
            }

            if (
                factory ==
                IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "RoGetActivationFactory returned null."
                );
            }

            return factory;
        }
        finally
        {
            WindowsDeleteString(
                hstring
            );
        }
    }

    private void ThrowIfDisposed()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(
                nameof(CaptureController)
            );
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed =
            true;

        try
        {
            _framePool.FrameArrived -=
                OnFrameArrived;
        }
        catch
        {
        }

        try
        {
            _captureSession.Dispose();
        }
        catch
        {
        }

        try
        {
            _framePool.Dispose();
        }
        catch
        {
        }

        try
        {
            _winrtDevice.Dispose();
        }
        catch
        {
        }

        try
        {
            _context.Dispose();
        }
        catch
        {
        }

        try
        {
            _device.Dispose();
        }
        catch
        {
        }

        try
        {
            _frameSignal.Dispose();
        }
        catch
        {
        }
    }
}

// ================================================================
// Capture result
// ================================================================

private readonly record struct
    CaptureResult(
        byte[] Bytes,
        int Width,
        int Height
    );

// ================================================================
// COM delegate
// ================================================================

[UnmanagedFunctionPointer(
    CallingConvention.StdCall)]
private delegate int
    CreateForMonitorDelegate(
        IntPtr @this,
        IntPtr hMonitor,
        ref Guid iid,
        out IntPtr result
    );

// ================================================================
// Monitor / input structures
// ================================================================

[StructLayout(
    LayoutKind.Sequential)]
private struct RECT
{
    public int Left;
    public int Top;
    public int Right;
    public int Bottom;
}

[StructLayout(
    LayoutKind.Sequential)]
private struct INPUT
{
    public uint Type;
    public InputUnion Union;
}

[StructLayout(
    LayoutKind.Explicit)]
private struct InputUnion
{
    [FieldOffset(0)]
    public MOUSEINPUT Mouse;

    [FieldOffset(0)]
    public KEYBDINPUT Keyboard;

    [FieldOffset(0)]
    public HARDWAREINPUT Hardware;
}

[StructLayout(
    LayoutKind.Sequential)]
private struct MOUSEINPUT
{
    public int Dx;
    public int Dy;
    public uint MouseData;
    public uint Flags;
    public uint Time;
    public IntPtr ExtraInfo;
}

[StructLayout(
    LayoutKind.Sequential)]
private struct KEYBDINPUT
{
    public ushort Vk;
    public ushort Scan;
    public uint Flags;
    public uint Time;
    public IntPtr ExtraInfo;
}

[StructLayout(
    LayoutKind.Sequential)]
private struct HARDWAREINPUT
{
    public uint Msg;
    public ushort ParamL;
    public ushort ParamH;
}

private delegate bool
    MonitorEnumProc(
        IntPtr hMonitor,
        IntPtr hdcMonitor,
        ref RECT monitorRect,
        IntPtr dwData
    );

// ================================================================
// Win32
// ================================================================

[DllImport(
    "user32.dll")]
private static extern bool
    IsWindow(
        IntPtr hwnd
    );

[DllImport(
    "user32.dll")]
private static extern bool
    IsIconic(
        IntPtr hwnd
    );

[DllImport(
    "user32.dll")]
private static extern bool
    ShowWindow(
        IntPtr hwnd,
        int command
    );

[DllImport(
    "user32.dll")]
private static extern bool
    BringWindowToTop(
        IntPtr hwnd
    );

[DllImport(
    "user32.dll")]
private static extern bool
    SetForegroundWindow(
        IntPtr hwnd
    );

[DllImport(
    "user32.dll")]
private static extern IntPtr
    SetActiveWindow(
        IntPtr hwnd
    );

[DllImport(
    "user32.dll")]
private static extern IntPtr
    SetFocus(
        IntPtr hwnd
    );

[DllImport(
    "user32.dll")]
private static extern IntPtr
    GetForegroundWindow();

[DllImport(
    "user32.dll")]
private static extern uint
    GetWindowThreadProcessId(
        IntPtr hwnd,
        out uint processId
    );

[DllImport(
    "user32.dll",
    SetLastError = true)]
private static extern bool
    AttachThreadInput(
        uint idAttach,
        uint idAttachTo,
        bool attach
    );

[DllImport(
    "kernel32.dll")]
private static extern uint
    GetCurrentThreadId();

[DllImport(
    "user32.dll",
    SetLastError = true)]
private static extern bool
    SetCursorPos(
        int x,
        int y
    );

[DllImport(
    "user32.dll",
    SetLastError = true)]
private static extern uint
    SendInput(
        uint numberOfInputs,
        INPUT[] inputs,
        int size
    );

[DllImport(
    "user32.dll")]
private static extern bool
    EnumDisplayMonitors(
        IntPtr hdc,
        IntPtr lprcClip,
        MonitorEnumProc callback,
        IntPtr dwData
    );

// ================================================================
// WinRT / D3D11
// ================================================================

[DllImport(
    "combase.dll",
    ExactSpelling = true)]
private static extern int
    WindowsCreateString(
        [MarshalAs(
            UnmanagedType.LPWStr)]
        string sourceString,
        int length,
        out IntPtr hstring
    );

[DllImport(
    "combase.dll",
    ExactSpelling = true)]
private static extern int
    WindowsDeleteString(
        IntPtr hstring
    );

[DllImport(
    "combase.dll",
    ExactSpelling = true)]
private static extern int
    RoGetActivationFactory(
        IntPtr activatableClassId,
        ref Guid iid,
        out IntPtr factory
    );

[DllImport(
    "d3d11.dll",
    EntryPoint =
        "CreateDirect3D11DeviceFromDXGIDevice",
    ExactSpelling = true,
    CallingConvention =
        CallingConvention.StdCall)]
private static extern uint
    CreateDirect3D11DeviceFromDXGIDevice(
        IntPtr dxgiDevice,
        out IntPtr graphicsDevice
    );


}
