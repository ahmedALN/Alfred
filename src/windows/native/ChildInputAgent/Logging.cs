using System;
using System.IO;
using System.Text;

// ====================================================================
// The agent writes its own log.
//
// It used to be the scheduled task's job, via cmd's '>>'. That breaks
// as soon as two agents exist - and two is the normal case, one in the
// user's session and one in Alfred's. Whichever started first held the
// file, the second failed to redirect, cmd exited 1, and the session
// silently had no agent at all. The symptom (isolation "not ready in
// time") pointed nowhere near the cause.
//
// One file per session, capped, owned by the process that writes it.
// ====================================================================

internal static class AgentLog
{
    private const long MaxBytes = 2 * 1024 * 1024;

    private static StreamWriter? _writer;

    public static string? Path { get; private set; }

    public static void Start(int sessionId)
    {
        try
        {
            string? root = FindRoot();

            if (root is null)
            {
                return;
            }

            string directory = System.IO.Path.Combine(root, "logs");
            Directory.CreateDirectory(directory);

            string path = System.IO.Path.Combine(
                directory,
                $"child-agent.s{sessionId}.log"
            );

            Roll(path);

            // Shared so a tail, an editor, or a second reader cannot
            // stop the agent from starting.
            var stream = new FileStream(
                path,
                FileMode.Append,
                FileAccess.Write,
                FileShare.ReadWrite | FileShare.Delete
            );

            _writer = new StreamWriter(stream, new UTF8Encoding(false))
            {
                AutoFlush = true
            };

            Path = path;

            Console.SetOut(new Tee(Console.Out, _writer));
            Console.SetError(new Tee(Console.Error, _writer));

            Console.WriteLine();
            Console.WriteLine(
                $"--- started {DateTime.Now:yyyy-MM-dd HH:mm:ss} ---"
            );
        }
        catch
        {
            // Logging is never worth failing to start over.
        }
    }

    /// <summary>Keeps one previous run instead of growing for ever.</summary>
    private static void Roll(string path)
    {
        try
        {
            var file = new FileInfo(path);

            if (!file.Exists || file.Length < MaxBytes)
            {
                return;
            }

            string previous = path + ".1";

            if (File.Exists(previous))
            {
                File.Delete(previous);
            }

            File.Move(path, previous);
        }
        catch
        {
            // Still in use somewhere; appending is fine.
        }
    }

    /// <summary>
    /// Walks up from the binary to the checkout that contains it, so the
    /// log lands beside Alfred rather than deep inside bin/Release.
    /// </summary>
    private static string? FindRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);

        for (int hop = 0; hop < 10 && directory is not null; hop++)
        {
            bool looksLikeRoot =
                Directory.Exists(System.IO.Path.Combine(directory.FullName, "src"))
                && Directory.Exists(
                    System.IO.Path.Combine(directory.FullName, "scripts"));

            if (looksLikeRoot)
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        return null;
    }

    private sealed class Tee : TextWriter
    {
        private readonly TextWriter _first;
        private readonly TextWriter _second;

        public Tee(TextWriter first, TextWriter second)
        {
            _first = first;
            _second = second;
        }

        public override Encoding Encoding => _second.Encoding;

        public override void Write(char value)
        {
            Safe(() => _first.Write(value));
            Safe(() => _second.Write(value));
        }

        public override void Write(string? value)
        {
            Safe(() => _first.Write(value));
            Safe(() => _second.Write(value));
        }

        public override void WriteLine(string? value)
        {
            Safe(() => _first.WriteLine(value));
            Safe(() => _second.WriteLine(value));
        }

        public override void Flush()
        {
            Safe(_first.Flush);
            Safe(_second.Flush);
        }

        private static void Safe(Action action)
        {
            try
            {
                action();
            }
            catch
            {
                // A closed console must not take the agent with it.
            }
        }
    }
}
