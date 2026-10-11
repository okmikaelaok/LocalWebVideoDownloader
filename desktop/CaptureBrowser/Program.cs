using System.Text;
using System.Text.Json;

namespace LocalWebVideoDownloader;

internal static class Program
{
    internal const int MaxMessageBytes = 64 * 1024;
    internal const int MaxDesktopMessageBytes = 1024 * 1024;
    internal static bool DesktopMode { get; private set; }
    private static readonly StreamWriter Output = new(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
    internal static void Send(object value)
    {
        var line = JsonSerializer.Serialize(value);
        if (Encoding.UTF8.GetByteCount(line) + 2 > (DesktopMode ? MaxDesktopMessageBytes : MaxMessageBytes))
            line = "{\"version\":1,\"event\":\"error\",\"code\":\"capture_too_large\"}";
        try { lock (Output) Output.WriteLine(line); }
        catch (IOException) { Application.Exit(); }
    }

    internal static string? ReadLine(Stream stream, int maximum = MaxMessageBytes)
    {
        using var buffer = new MemoryStream();
        while (true)
        {
            var next = stream.ReadByte();
            if (next < 0) return buffer.Length == 0 ? null : throw new InvalidDataException();
            if (next == 10) return new UTF8Encoding(false, true).GetString(buffer.ToArray());
            if (buffer.Length >= maximum) throw new InvalidDataException();
            buffer.WriteByte((byte)next);
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        var smoke = args.SequenceEqual(new[] { "--smoke-test" });
        var desktopSmoke = args.SequenceEqual(new[] { "--desktop-ui-smoke-test" });
        var integrationSmoke = args.SequenceEqual(new[] { "--desktop-integration-test" });
        DesktopMode = desktopSmoke || integrationSmoke || args.SequenceEqual(new[] { "--desktop-stdio" });
        if (!smoke && !DesktopMode && !args.SequenceEqual(new[] { "--capture-stdio" }))
        {
            MessageBox.Show("Launch LocalWebVideoDownloader using scripts\\run-desktop.ps1.", "LocalWebVideoDownloader");
            return 2;
        }
        using var input = Console.OpenStandardInput();
        string? initialUrl = null;
        if (!smoke && !desktopSmoke)
        {
            try
            {
                using var json = JsonDocument.Parse(ReadLine(input, DesktopMode ? MaxDesktopMessageBytes : MaxMessageBytes) ?? throw new InvalidDataException());
                if (json.RootElement.GetProperty("version").GetInt32() != 1) throw new InvalidDataException();
                if (json.RootElement.TryGetProperty("initial_url", out var initial) && initial.ValueKind != JsonValueKind.Null)
                {
                    initialUrl = MediaCatalog.NormalizeUrl(initial.GetString());
                    if (initialUrl is null) throw new InvalidDataException();
                }
            }
            catch
            {
                Send(new { version = 1, @event = "error", code = "protocol_error" });
                return 2;
            }
        }
        using var window = new BrowserForm(initialUrl, smoke, DesktopMode, desktopSmoke, integrationSmoke);
        if (!smoke && !desktopSmoke)
        {
            window.Shown += (_, _) => _ = Task.Run(() =>
            {
                try
                {
                    // Only the Python parent owns this anonymous pipe. Pages have no bridge.
                    while (ReadLine(input, DesktopMode ? MaxDesktopMessageBytes : MaxMessageBytes) is { } line)
                    {
                        using var command = JsonDocument.Parse(line);
                        var message = command.RootElement;
                        if (message.GetProperty("version").GetInt32() != 1) throw new InvalidDataException();
                        var name = message.GetProperty("command").GetString();
                        if (name == "close") break;
                        if (!DesktopMode || name is not ("init" or "busy" or "inspection_complete" or "progress" or "download_complete" or "error"))
                            throw new InvalidDataException();
                        var snapshot = message.Clone();
                        window.BeginInvoke(() => window.ReceiveDesktopCommand(snapshot));
                    }
                }
                catch { /* An unavailable or malformed parent is treated as a close request. */ }
                finally
                {
                    try { window.BeginInvoke(() => window.CloseFromParent()); }
                    catch (InvalidOperationException) { }
                }
            });
        }
        Application.Run(window);
        return window.ExitCode;
    }
}
