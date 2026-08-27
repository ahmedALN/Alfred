using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;

using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging;
using Windows.Storage;
using Windows.Storage.Streams;

using Vortice.Direct3D;
using Vortice.Direct3D11;
using Vortice.DXGI;

using WinRT;

internal static class Program
{
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
    IActivationFactoryGuid =
        new Guid(
            "00000035-0000-0000-C000-000000000046"
        );

private static int Main(
    string[] args)
{
    return MainAsync(
        args
    )
    .GetAwaiter()
    .GetResult();
}

private static async Task<int> MainAsync(
    string[] args)
{
    Console.WriteLine(
        "Alfred ChildScreenCapture"
    );

    Console.WriteLine(
        "========================"
    );

    Console.WriteLine();

    int currentSession =
        GetCurrentSessionId();

    Console.WriteLine(
        $"Current session: {currentSession}"
    );

    Console.WriteLine();

    if (!OperatingSystem.IsWindows())
    {
        Console.Error.WriteLine(
            "This program requires Windows."
        );

        return 1;
    }

    if (!GraphicsCaptureSession.IsSupported())
    {
        Console.Error.WriteLine(
            "Windows Graphics Capture is not supported."
        );

        return 2;
    }

    Console.WriteLine(
        "Enumerating monitors in this session..."
    );

    List<MonitorInfo> monitors =
        EnumerateMonitors();

    Console.WriteLine(
        $"Monitor count: {monitors.Count}"
    );

    if (monitors.Count == 0)
    {
        Console.Error.WriteLine(
            "No monitors were found."
        );

        return 3;
    }

    Console.WriteLine();

    for (
        int index = 0;
        index < monitors.Count;
        index++)
    {
        MonitorInfo monitor =
            monitors[index];

        Console.WriteLine(
            $"Monitor {index + 1}:"
        );

        Console.WriteLine(
            $"  HMONITOR: {monitor.Handle.ToInt64()}"
        );

        Console.WriteLine(
            $"  Bounds: "
            + $"{monitor.Left},{monitor.Top} "
            + $"{monitor.Width}x"
            + $"{monitor.Height}"
        );

        Console.WriteLine();
    }

    try
    {
        for (
            int index = 0;
            index < monitors.Count;
            index++)
        {
            MonitorInfo monitor =
                monitors[index];

            Console.WriteLine(
                "=================================================="
            );

            Console.WriteLine(
                $"Capturing monitor {index + 1} "
                + $"of {monitors.Count}"
            );

            Console.WriteLine();

            GraphicsCaptureItem item =
                CreateCaptureItemForMonitor(
                    monitor.Handle
                );

            Console.WriteLine(
                "GraphicsCaptureItem: PASS"
            );

            Console.WriteLine(
                $"Capture size: "
                + $"{item.Size.Width}x"
                + $"{item.Size.Height}"
            );

            Console.WriteLine();

            using var capture =
                new CaptureSession(
                    item
                );

            await capture.StartAsync(
                TimeSpan.FromSeconds(10)
            );

            CaptureFrameInfo frameInfo =
                capture.GetLatestFrameInfo();

            Console.WriteLine(
                "FIRST FRAME: PASS"
            );

            Console.WriteLine(
                $"Frame size: "
                + $"{frameInfo.Width}x"
                + $"{frameInfo.Height}"
            );

            Console.WriteLine(
                $"Frame time: "
                + $"{frameInfo.SystemRelativeTime}"
            );

            string fileName =
                monitors.Count == 1
                    ? "child-desktop.png"
                    : $"child-monitor-{index + 1}.png";

            string outputPath =
                Path.Combine(
                    Directory.GetCurrentDirectory(),
                    fileName
                );

            Console.WriteLine();

            Console.WriteLine(
                $"Saving: {outputPath}"
            );

            await capture.SavePngAsync(
                outputPath
            );

            if (!File.Exists(
                    outputPath))
            {
                throw new IOException(
                    "PNG file was not created."
                );
            }

            FileInfo fileInfo =
                new FileInfo(
                    outputPath
                );

            Console.WriteLine(
                $"PNG size: {fileInfo.Length} bytes"
            );

            if (fileInfo.Length == 0)
            {
                throw new IOException(
                    "PNG file is empty."
                );
            }

            Console.WriteLine(
                "PNG: PASS"
            );

            Console.WriteLine();
        }

        Console.WriteLine(
            "=================================================="
        );

        if (monitors.Count == 1)
        {
            Console.WriteLine(
                "FULL CHILD DESKTOP CAPTURE: PASS"
            );

            Console.WriteLine(
                "Output: child-desktop.png"
            );
        }
        else
        {
            Console.WriteLine(
                "CHILD MONITOR CAPTURE: PASS"
            );

            Console.WriteLine(
                $"Captured {monitors.Count} monitors."
            );
        }

        return 0;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine();

        Console.Error.WriteLine(
            $"ERROR: {ex.GetType().Name}"
        );

        Console.Error.WriteLine(
            ex.Message
        );

        Console.Error.WriteLine();

        if (!string.IsNullOrWhiteSpace(
                ex.StackTrace))
        {
            Console.Error.WriteLine(
                ex.StackTrace
            );
        }

        return 10;
    }
}

// ================================================================
// Session
// ================================================================

private static int GetCurrentSessionId()
{
    using Process process =
        Process.GetCurrentProcess();

    return process.SessionId;
}

// ================================================================
// Monitor enumeration
// ================================================================

private static List<MonitorInfo>
    EnumerateMonitors()
{
    var monitors =
        new List<MonitorInfo>();

    MonitorEnumProc callback =
        (
            IntPtr hMonitor,
            IntPtr hdcMonitor,
            ref RECT monitorRect,
            IntPtr dwData
        ) =>
        {
            MONITORINFO info =
                new MONITORINFO();

            info.CbSize =
                Marshal.SizeOf<MONITORINFO>();

            if (
                GetMonitorInfo(
                    hMonitor,
                    ref info
                ))
            {
                monitors.Add(
                    new MonitorInfo(
                        hMonitor,
                        info.RcMonitor.Left,
                        info.RcMonitor.Top,
                        info.RcMonitor.Right,
                        info.RcMonitor.Bottom
                    )
                );
            }

            return true;
        };

    if (!EnumDisplayMonitors(
            IntPtr.Zero,
            IntPtr.Zero,
            callback,
            IntPtr.Zero))
    {
        throw new Win32Exception(
            Marshal.GetLastWin32Error()
        );
    }

    return monitors;
}

// ================================================================
// GraphicsCaptureItem from HMONITOR
// ================================================================

private static GraphicsCaptureItem
    CreateCaptureItemForMonitor(
        IntPtr hMonitor)
{
    Console.WriteLine(
        "Getting GraphicsCaptureItem activation factory..."
    );

    IntPtr factory =
        GetActivationFactory(
            "Windows.Graphics.Capture.GraphicsCaptureItem"
        );

    try
    {
        Console.WriteLine(
            "Activation factory: PASS"
        );

        Guid interopGuid =
            GraphicsCaptureItemInteropGuid;

        IntPtr interop =
            IntPtr.Zero;

        int hr =
            Marshal.QueryInterface(
                factory,
                ref interopGuid,
                out interop
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
                "IGraphicsCaptureItemInterop "
                + "pointer is null."
            );
        }

        try
        {
            Console.WriteLine(
                "IGraphicsCaptureItemInterop: PASS"
            );

            IntPtr vtable =
                Marshal.ReadIntPtr(
                    interop
                );

            if (vtable == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "IGraphicsCaptureItemInterop "
                    + "vtable is null."
                );
            }

            // IUnknown:
            // 0 = QueryInterface
            // 1 = AddRef
            // 2 = Release
            //
            // IGraphicsCaptureItemInterop:
            // 3 = CreateForWindow
            // 4 = CreateForMonitor

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
                    "CreateForMonitor function "
                    + "pointer is null."
                );
            }

            var createForMonitor =
                Marshal.GetDelegateForFunctionPointer<
                    CreateForMonitorDelegate
                >(
                    createForMonitorPointer
                );

            Guid itemIid =
                GraphicsCaptureItemGuid;

            IntPtr itemPointer =
                IntPtr.Zero;

            Console.WriteLine(
                "Calling CreateForMonitor..."
            );

            hr =
                createForMonitor(
                    interop,
                    hMonitor,
                    ref itemIid,
                    out itemPointer
                );

            Console.WriteLine(
                $"CreateForMonitor HRESULT: "
                + $"0x{hr:X8}"
            );

            if (hr < 0)
            {
                Marshal.ThrowExceptionForHR(
                    hr
                );
            }

            if (
                itemPointer ==
                IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "CreateForMonitor returned a null "
                    + "GraphicsCaptureItem pointer."
                );
            }

            Console.WriteLine(
                "CreateForMonitor: PASS"
            );

            try
            {
                GraphicsCaptureItem item =
                    MarshalInterface<
                        GraphicsCaptureItem
                    >
                    .FromAbi(
                        itemPointer
                    );

                if (item is null)
                {
                    throw new InvalidOperationException(
                        "GraphicsCaptureItem conversion "
                        + "returned null."
                    );
                }

                return item;
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

// ================================================================
// WinRT activation factory
// ================================================================

private static IntPtr GetActivationFactory(
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

    if (hstring == IntPtr.Zero)
    {
        throw new InvalidOperationException(
            "WindowsCreateString returned null."
        );
    }

    try
    {
        Guid iid =
            IActivationFactoryGuid;

        IntPtr factory =
            IntPtr.Zero;

        hr =
            RoGetActivationFactory(
                hstring,
                ref iid,
                out factory
            );

        if (hr < 0)
        {
            Marshal.ThrowExceptionForHR(
                hr
            );
        }

        if (factory == IntPtr.Zero)
        {
            throw new InvalidOperationException(
                "RoGetActivationFactory returned null."
            );
        }

        return factory;
    }
    finally
    {
        int deleteHr =
            WindowsDeleteString(
                hstring
            );

        if (deleteHr < 0)
        {
            // Cleanup failure is intentionally ignored here.
        }
    }
}

// ================================================================
// Capture session
// ================================================================

private sealed class CaptureSession :
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

    private readonly object _sync =
        new object();

    private Direct3D11CaptureFrame?
        _latestFrame;

    private TaskCompletionSource<bool>?
        _firstFrameTcs;

    private bool _disposed;

    public CaptureSession(
        GraphicsCaptureItem item)
    {
        _item =
            item;

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
                "D3D11 device context returned null."
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
                    2,
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

    public async Task StartAsync(
        TimeSpan timeout)
    {
        ThrowIfDisposed();

        _firstFrameTcs =
            new TaskCompletionSource<bool>(
                TaskCreationOptions
                    .RunContinuationsAsynchronously
            );

        Console.WriteLine(
            "Starting capture..."
        );

        _captureSession.StartCapture();

        Task timeoutTask =
            Task.Delay(
                timeout
            );

        Task completed =
            await Task.WhenAny(
                _firstFrameTcs.Task,
                timeoutTask
            );

        if (
            completed ==
            timeoutTask)
        {
            throw new TimeoutException(
                "Timed out waiting for the first "
                + "capture frame."
            );
        }

        await _firstFrameTcs.Task;
    }

    private void OnFrameArrived(
        Direct3D11CaptureFramePool sender,
        object args)
    {
        try
        {
            Direct3D11CaptureFrame? frame =
                sender.TryGetNextFrame();

            if (frame is null)
            {
                return;
            }

            lock (_sync)
            {
                _latestFrame?.Dispose();

                _latestFrame =
                    frame;

                _firstFrameTcs?.TrySetResult(
                    true
                );
            }
        }
        catch (Exception ex)
        {
            lock (_sync)
            {
                _firstFrameTcs?.TrySetException(
                    ex
                );
            }
        }
    }

    public CaptureFrameInfo
        GetLatestFrameInfo()
    {
        ThrowIfDisposed();

        lock (_sync)
        {
            if (_latestFrame is null)
            {
                throw new InvalidOperationException(
                    "No captured frame is available."
                );
            }

            return new CaptureFrameInfo(
                _latestFrame.ContentSize.Width,
                _latestFrame.ContentSize.Height,
                _latestFrame.SystemRelativeTime
            );
        }
    }

    public async Task SavePngAsync(
        string outputPath)
    {
        ThrowIfDisposed();

        Direct3D11CaptureFrame frame;

        lock (_sync)
        {
            if (_latestFrame is null)
            {
                throw new InvalidOperationException(
                    "No captured frame is available."
                );
            }

            frame =
                _latestFrame;
        }

        Console.WriteLine(
            "Converting captured surface..."
        );

        using SoftwareBitmap bitmap =
            await SoftwareBitmap
                .CreateCopyFromSurfaceAsync(
                    frame.Surface,
                    BitmapAlphaMode.Premultiplied
                );

        using SoftwareBitmap converted =
            SoftwareBitmap.Convert(
                bitmap,
                BitmapPixelFormat.Bgra8,
                BitmapAlphaMode.Premultiplied
            );

        string fullPath =
            Path.GetFullPath(
                outputPath
            );

        string directory =
            Path.GetDirectoryName(
                fullPath
            )
            ?? Directory.GetCurrentDirectory();

        Directory.CreateDirectory(
            directory
        );

        string fileName =
            Path.GetFileName(
                fullPath
            );

        if (File.Exists(fullPath))
        {
            File.Delete(
                fullPath
            );
        }

        StorageFolder folder =
            await StorageFolder
                .GetFolderFromPathAsync(
                    directory
                );

        StorageFile file =
            await folder.CreateFileAsync(
                fileName,
                CreationCollisionOption
                    .ReplaceExisting
            );

        using IRandomAccessStream stream =
            await file.OpenAsync(
                FileAccessMode.ReadWrite
            );

        Console.WriteLine(
            "Encoding PNG..."
        );

        BitmapEncoder encoder =
            await BitmapEncoder
                .CreateAsync(
                    BitmapEncoder.PngEncoderId,
                    stream
                );

        encoder.SetSoftwareBitmap(
            converted
        );

        await encoder.FlushAsync();

        Console.WriteLine(
            "PNG encoding: PASS"
        );
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
                "CreateDirect3D11DeviceFromDXGIDevice "
                + "returned null."
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

    private void ThrowIfDisposed()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(
                nameof(CaptureSession)
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

        lock (_sync)
        {
            _latestFrame?.Dispose();

            _latestFrame =
                null;
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
    }
}

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
// Data structures
// ================================================================

private readonly record struct
    MonitorInfo(
        IntPtr Handle,
        int Left,
        int Top,
        int Right,
        int Bottom)
{
    public int Width =>
        Right - Left;

    public int Height =>
        Bottom - Top;
}

private readonly record struct
    CaptureFrameInfo(
        int Width,
        int Height,
        TimeSpan SystemRelativeTime
    );

// ================================================================
// Win32 monitor APIs
// ================================================================

private delegate bool
    MonitorEnumProc(
        IntPtr hMonitor,
        IntPtr hdcMonitor,
        ref RECT monitorRect,
        IntPtr dwData
    );

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
private struct MONITORINFO
{
    public int CbSize;
    public RECT RcMonitor;
    public RECT RcWork;
    public uint DwFlags;
}

[DllImport(
    "user32.dll")]
private static extern bool
    EnumDisplayMonitors(
        IntPtr hdc,
        IntPtr lprcClip,
        MonitorEnumProc callback,
        IntPtr dwData
    );

[DllImport(
    "user32.dll",
    CharSet = CharSet.Unicode)]
private static extern bool
    GetMonitorInfo(
        IntPtr hMonitor,
        ref MONITORINFO lpmi
    );

// ================================================================
// Window/session APIs
// ================================================================

[DllImport(
    "user32.dll")]
private static extern uint
    GetWindowThreadProcessId(
        IntPtr hwnd,
        out uint processId
    );

// ================================================================
// WinRT APIs
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

// ================================================================
// D3D11 bridge
// ================================================================

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
