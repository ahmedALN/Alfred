using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;

internal static class Program
{
private const string PipeName =
"Alfred.ChildInput.v1";


private static int Main()
{
    Console.WriteLine(
        "Alfred ChildInputClient"
    );

    Console.WriteLine(
        "======================"
    );

    Console.WriteLine();

    int clientSession =
        GetCurrentSessionId();

    Console.WriteLine(
        $"Client session: {clientSession}"
    );

    Console.WriteLine(
        $"Pipe: {PipeName}"
    );

    Console.WriteLine();

    try
    {
        using var pipe =
            new NamedPipeClientStream(
                ".",
                PipeName,
                PipeDirection.InOut,
                PipeOptions.None
            );

        Console.WriteLine(
            "Connecting to ChildInputAgent..."
        );

        pipe.Connect(
            5000
        );

        Console.WriteLine(
            "Connected."
        );

        Console.WriteLine();

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

        string pingResponse =
            Send(
                writer,
                reader,
                new
                {
                    op = "ping"
                }
            );

        Console.WriteLine(
            "Ping response:"
        );

        Console.WriteLine(
            pingResponse
        );

        Console.WriteLine();

        string sessionResponse =
            Send(
                writer,
                reader,
                new
                {
                    op = "session"
                }
            );

        Console.WriteLine(
            "Session response:"
        );

        Console.WriteLine(
            sessionResponse
        );

        Console.WriteLine();

        if (!TryReadSession(
                sessionResponse,
                out int agentSession))
        {
            Console.Error.WriteLine(
                "Could not read agent session."
            );

            return 4;
        }

        Console.WriteLine(
            $"Agent session: {agentSession}"
        );

        if (
            agentSession ==
            clientSession)
        {
            Console.Error.WriteLine(
                "FAIL: Client and agent are "
                + "running in the same session."
            );

            return 5;
        }

        Console.WriteLine();

        Console.WriteLine(
            "IPC TEST: PASS"
        );

        Console.WriteLine(
            "Session 1 client successfully "
            + "communicated with the Session 2 agent."
        );

        Console.WriteLine();

        PrintHelp();

        RunInteractiveLoop(
            writer,
            reader
        );

        return 0;
    }
    catch (TimeoutException)
    {
        Console.Error.WriteLine();

        Console.Error.WriteLine(
            "Could not connect to ChildInputAgent "
            + "within 5 seconds."
        );

        return 1;
    }
    catch (IOException ex)
    {
        Console.Error.WriteLine();

        Console.Error.WriteLine(
            $"Pipe error: {ex.Message}"
        );

        return 2;
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

        return 3;
    }
}

private static int GetCurrentSessionId()
{
    using Process process =
        Process.GetCurrentProcess();

    return process.SessionId;
}

private static void RunInteractiveLoop(
    StreamWriter writer,
    StreamReader reader)
{
    while (true)
    {
        Console.Write(
            "> "
        );

        string? command =
            Console.ReadLine();

        if (command is null)
        {
            return;
        }

        command =
            command.Trim();

        if (command.Length == 0)
        {
            continue;
        }

        if (
            command.Equals(
                "exit",
                StringComparison.OrdinalIgnoreCase
            ) ||
            command.Equals(
                "quit",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            return;
        }

        if (
            command.Equals(
                "help",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            PrintHelp();
            continue;
        }

        if (
            command.Equals(
                "capture_start",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            string response =
                Send(
                    writer,
                    reader,
                    new
                    {
                        op = "capture_start"
                    }
                );

            PrintCaptureStartResponse(
                response
            );

            continue;
        }

        if (
            command.Equals(
                "capture_stop",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            string response =
                Send(
                    writer,
                    reader,
                    new
                    {
                        op = "capture_stop"
                    }
                );

            Console.WriteLine(
                response
            );

            continue;
        }

        if (
            command.Equals(
                "screenshot",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            string response =
                Send(
                    writer,
                    reader,
                    new
                    {
                        op = "screenshot"
                    }
                );

            PrintScreenshotResponse(
                response
            );

            continue;
        }

        if (
            command.Equals(
                "ping",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            string response =
                Send(
                    writer,
                    reader,
                    new
                    {
                        op = "ping"
                    }
                );

            Console.WriteLine(
                response
            );

            continue;
        }

        if (
            command.Equals(
                "session",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            string response =
                Send(
                    writer,
                    reader,
                    new
                    {
                        op = "session"
                    }
                );

            Console.WriteLine(
                response
            );

            continue;
        }

        if (
            command.Equals(
                "shutdown",
                StringComparison.OrdinalIgnoreCase
            ))
        {
            string response =
                Send(
                    writer,
                    reader,
                    new
                    {
                        op = "shutdown"
                    }
                );

            Console.WriteLine(
                response
            );

            return;
        }

        string request;

        try
        {
            request =
                ConvertCommandToJson(
                    command
                );
        }
        catch (Exception ex)
        {
            Console.WriteLine(
                $"Command error: {ex.Message}"
            );

            continue;
        }

        string result =
            Send(
                writer,
                reader,
                request
            );

        Console.WriteLine(
            result
        );
    }
}

private static void PrintHelp()
{
    Console.WriteLine(
        "Commands:"
    );

    Console.WriteLine(
        "  ping"
    );

    Console.WriteLine(
        "  session"
    );

    Console.WriteLine(
        "  activate <hwnd>"
    );

    Console.WriteLine(
        "  move <x> <y>"
    );

    Console.WriteLine(
        "  click [left|right|middle]"
    );

    Console.WriteLine(
        "  type <text>"
    );

    Console.WriteLine(
        "  capture_start"
    );

    Console.WriteLine(
        "  screenshot"
    );

    Console.WriteLine(
        "  capture_stop"
    );

    Console.WriteLine(
        "  shutdown"
    );

    Console.WriteLine(
        "  help"
    );

    Console.WriteLine(
        "  exit"
    );

    Console.WriteLine();
}

private static string ConvertCommandToJson(
    string command)
{
    if (
        command.StartsWith(
            "{",
            StringComparison.Ordinal
        ))
    {
        using JsonDocument document =
            JsonDocument.Parse(
                command
            );

        return document.RootElement.GetRawText();
    }

    string[] parts =
        SplitCommandLine(
            command
        );

    if (parts.Length == 0)
    {
        throw new ArgumentException(
            "Command is empty."
        );
    }

    string operation =
        parts[0].ToLowerInvariant();

    switch (operation)
    {
        case "activate":
        {
            if (parts.Length != 2)
            {
                throw new ArgumentException(
                    "Usage: activate <hwnd>"
                );
            }

            if (!long.TryParse(
                    parts[1],
                    out long hwnd))
            {
                throw new ArgumentException(
                    "HWND must be an integer."
                );
            }

            return JsonSerializer.Serialize(
                new
                {
                    op = "activate",
                    hwnd
                }
            );
        }

        case "move":
        {
            if (parts.Length != 3)
            {
                throw new ArgumentException(
                    "Usage: move <x> <y>"
                );
            }

            if (!int.TryParse(
                    parts[1],
                    out int x))
            {
                throw new ArgumentException(
                    "X must be an integer."
                );
            }

            if (!int.TryParse(
                    parts[2],
                    out int y))
            {
                throw new ArgumentException(
                    "Y must be an integer."
                );
            }

            return JsonSerializer.Serialize(
                new
                {
                    op = "mouse_move",
                    x,
                    y
                }
            );
        }

        case "click":
        {
            string button =
                parts.Length >= 2
                    ? parts[1]
                    : "left";

            return JsonSerializer.Serialize(
                new
                {
                    op = "click",
                    button
                }
            );
        }

        case "type":
        {
            if (parts.Length < 2)
            {
                throw new ArgumentException(
                    "Usage: type <text>"
                );
            }

            int start =
                command.IndexOf(
                    parts[1],
                    StringComparison.Ordinal
                );

            if (start < 0)
            {
                throw new ArgumentException(
                    "Could not parse text."
                );
            }

            string text =
                command.Substring(
                    start
                );

            return JsonSerializer.Serialize(
                new
                {
                    op = "type",
                    text
                }
            );
        }

        case "capture_start":
            return JsonSerializer.Serialize(
                new
                {
                    op = "capture_start"
                }
            );

        case "screenshot":
            return JsonSerializer.Serialize(
                new
                {
                    op = "screenshot"
                }
            );

        case "capture_stop":
            return JsonSerializer.Serialize(
                new
                {
                    op = "capture_stop"
                }
            );

        default:
            throw new ArgumentException(
                $"Unknown command '{parts[0]}'."
            );
    }
}

private static void PrintCaptureStartResponse(
    string response)
{
    try
    {
        using JsonDocument document =
            JsonDocument.Parse(
                response
            );

        JsonElement root =
            document.RootElement;

        Console.WriteLine(
            response
        );

        if (
            root.TryGetProperty(
                "ok",
                out JsonElement ok) &&
            ok.GetBoolean() &&
            root.TryGetProperty(
                "data",
                out JsonElement data))
        {
            if (
                data.TryGetProperty(
                    "width",
                    out JsonElement width) &&
                data.TryGetProperty(
                    "height",
                    out JsonElement height))
            {
                Console.WriteLine();

                Console.WriteLine(
                    $"Capture resolution: "
                    + $"{width.GetInt32()}x"
                    + $"{height.GetInt32()}"
                );
            }
        }
    }
    catch
    {
        Console.WriteLine(
            response
        );
    }
}

private static void PrintScreenshotResponse(
    string response)
{
    try
    {
        using JsonDocument document =
            JsonDocument.Parse(
                response
            );

        JsonElement root =
            document.RootElement;

        if (
            !root.TryGetProperty(
                "ok",
                out JsonElement ok) ||
            !ok.GetBoolean())
        {
            Console.WriteLine(
                response
            );

            return;
        }

        if (
            !root.TryGetProperty(
                "data",
                out JsonElement data))
        {
            Console.WriteLine(
                response
            );

            return;
        }

        int width =
            data.GetProperty(
                "width"
            ).GetInt32();

        int height =
            data.GetProperty(
                "height"
            ).GetInt32();

        int bytes =
            data.GetProperty(
                "bytes"
            ).GetInt32();

        string? base64 =
            data.GetProperty(
                "image_base64"
            ).GetString();

        Console.WriteLine(
            "Screenshot received."
        );

        Console.WriteLine(
            $"Resolution: {width}x{height}"
        );

        Console.WriteLine(
            $"PNG bytes: {bytes}"
        );

        Console.WriteLine(
            $"Base64 characters: "
            + $"{base64?.Length ?? 0}"
        );

        Console.WriteLine(
            "Image received through IPC: PASS"
        );
    }
    catch (Exception ex)
    {
        Console.WriteLine(
            $"Could not parse screenshot response: "
            + $"{ex.Message}"
        );

        Console.WriteLine(
            response
        );
    }
}

private static string Send(
    StreamWriter writer,
    StreamReader reader,
    object request)
{
    return Send(
        writer,
        reader,
        JsonSerializer.Serialize(
            request
        )
    );
}

private static string Send(
    StreamWriter writer,
    StreamReader reader,
    string request)
{
    writer.WriteLine(
        request
    );

    string? response =
        reader.ReadLine();

    if (response is null)
    {
        throw new IOException(
            "ChildInputAgent disconnected."
        );
    }

    return response;
}

private static bool TryReadSession(
    string response,
    out int session)
{
    session =
        -1;

    using JsonDocument document =
        JsonDocument.Parse(
            response
        );

    JsonElement root =
        document.RootElement;

    if (
        !root.TryGetProperty(
            "ok",
            out JsonElement ok) ||
        !ok.GetBoolean())
    {
        return false;
    }

    if (
        !root.TryGetProperty(
            "data",
            out JsonElement data))
    {
        return false;
    }

    if (
        !data.TryGetProperty(
            "session",
            out JsonElement sessionElement))
    {
        return false;
    }

    return sessionElement.TryGetInt32(
        out session
    );
}

private static string[] SplitCommandLine(
    string input)
{
    var parts =
        new List<string>();

    var current =
        new StringBuilder();

    bool quoted =
        false;

    char quote =
        '\0';

    foreach (char c in input)
    {
        if (
            quoted &&
            c == quote)
        {
            quoted =
                false;

            continue;
        }

        if (
            !quoted &&
            (c == '"' || c == '\''))
        {
            quoted =
                true;

            quote =
                c;

            continue;
        }

        if (
            !quoted &&
            char.IsWhiteSpace(c))
        {
            if (current.Length > 0)
            {
                parts.Add(
                    current.ToString()
                );

                current.Clear();
            }

            continue;
        }

        current.Append(
            c
        );
    }

    if (quoted)
    {
        throw new ArgumentException(
            "Unclosed quote."
        );
    }

    if (current.Length > 0)
    {
        parts.Add(
            current.ToString()
        );
    }

    return parts.ToArray();
}


}
