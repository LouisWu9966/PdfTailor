using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json.Nodes;

namespace PdfTailor.Engine;

/// <summary>An error reported by the engine. <see cref="Code"/> is "user" (show the
/// message), "cancelled", "bad-request", "internal" or "engine-exited".</summary>
public sealed class EngineException : Exception
{
    public string Code { get; }
    public EngineException(string code, string message) : base(message) { Code = code; }
}

/// <summary>
/// Talks to the Python engine over JSON Lines on stdin/stdout (see engine/pdftailor_engine/server.py).
/// One request at a time is processed by the engine; this client can still send several and
/// match the answers by id. Progress events are raised on the reader thread.
/// </summary>
public sealed class EngineClient : IAsyncDisposable
{
    private Process? _proc;
    private int _nextId;
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonNode?>> _pending = new();
    private readonly object _writeLock = new();
    private TaskCompletionSource<JsonObject>? _ready;
    private readonly StringBuilder _stderrTail = new();

    public event Action<int, string, double>? Progress;
    public event Action? Exited;

    public string Version { get; private set; } = "";
    public bool TesseractAvailable { get; private set; }
    public bool IsRunning => _proc is { HasExited: false };
    public string LaunchDescription { get; private set; } = "";

    // ---- locating the engine ------------------------------------------------------------
    /// <summary>Finds the engine: PDFTAILOR_ENGINE, then engine\pdftailor-engine.exe next to the
    /// app, then (development) the source tree, run with the Python launcher.</summary>
    public static ProcessStartInfo? Locate()
    {
        static ProcessStartInfo Exe(string path) => new(path) { WorkingDirectory = Path.GetDirectoryName(path)! };

        var env = Environment.GetEnvironmentVariable("PDFTAILOR_ENGINE");
        if (!string.IsNullOrWhiteSpace(env) && File.Exists(env)) return Exe(env);

        var baseDir = AppContext.BaseDirectory;
        var bundled = Path.Combine(baseDir, "engine", "pdftailor-engine.exe");
        if (File.Exists(bundled)) return Exe(bundled);

        // dev: ...\PdfTailor\app\PdfTailor\bin\Debug\net9...\  ->  ...\PdfTailor\engine
        var dir = new DirectoryInfo(baseDir);
        for (int i = 0; i < 8 && dir != null; i++, dir = dir.Parent)
        {
            var src = Path.Combine(dir.FullName, "engine", "pdftailor_engine", "__init__.py");
            if (!File.Exists(src)) continue;
            var engineDir = Path.Combine(dir.FullName, "engine");
            var venvPy = Path.Combine(engineDir, ".venv-build", "Scripts", "python.exe");
            var psi = File.Exists(venvPy) ? new ProcessStartInfo(venvPy) : new ProcessStartInfo("py") { ArgumentList = { "-3" } };
            psi.ArgumentList.Add("-m");
            psi.ArgumentList.Add("pdftailor_engine");
            psi.WorkingDirectory = engineDir;
            return psi;
        }
        return null;
    }

    // ---- lifecycle ------------------------------------------------------------------------
    public async Task StartAsync(TimeSpan? timeout = null)
    {
        if (IsRunning) return;
        var psi = Locate() ?? throw new EngineException("user",
            "找不到 PdfTailor 引擎（engine\\pdftailor-engine.exe）。請重新執行 release.cmd 發行，或設定 PDFTAILOR_ENGINE。");
        psi.UseShellExecute = false;
        psi.RedirectStandardInput = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.CreateNoWindow = true;
        psi.StandardOutputEncoding = new UTF8Encoding(false);
        psi.StandardErrorEncoding = new UTF8Encoding(false);
        psi.StandardInputEncoding = new UTF8Encoding(false);
        psi.Environment["PYTHONUTF8"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        LaunchDescription = psi.FileName + " " + string.Join(" ", psi.ArgumentList);

        _ready = new TaskCompletionSource<JsonObject>(TaskCreationOptions.RunContinuationsAsynchronously);
        var p = new Process { StartInfo = psi, EnableRaisingEvents = true };
        p.Exited += (_, _) => OnExited();
        try
        {
            if (!p.Start()) throw new EngineException("user", "引擎無法啟動。");
        }
        catch (System.ComponentModel.Win32Exception ex)
        {
            throw new EngineException("user", $"引擎無法啟動：{ex.Message}\n{LaunchDescription}");
        }
        _proc = p;
        _ = Task.Run(() => ReadLoop(p));
        _ = Task.Run(() => ErrLoop(p));

        var wait = Task.Delay(timeout ?? TimeSpan.FromSeconds(90));
        var done = await Task.WhenAny(_ready.Task, wait);
        if (done != _ready.Task)
        {
            try { p.Kill(true); } catch { /* already gone */ }
            throw new EngineException("user", "引擎啟動逾時。" + StderrTail());
        }
        var hello = await _ready.Task;
        Version = hello["version"]?.GetValue<string>() ?? "";
        TesseractAvailable = hello["tesseract"]?.GetValue<bool>() ?? false;
    }

    private void ReadLoop(Process p)
    {
        try
        {
            string? line;
            while ((line = p.StandardOutput.ReadLine()) != null)
            {
                if (line.Length == 0) continue;
                JsonObject? msg;
                try { msg = JsonNode.Parse(line) as JsonObject; }
                catch { continue; }
                if (msg == null) continue;

                var ev = msg["event"]?.GetValue<string>();
                if (ev == "ready") { _ready?.TrySetResult(msg); continue; }
                if (ev == "progress")
                {
                    var id = msg["id"]?.GetValue<int>() ?? -1;
                    Progress?.Invoke(id, msg["stage"]?.GetValue<string>() ?? "", msg["progress"]?.GetValue<double>() ?? 0);
                    continue;
                }
                if (ev != null) continue;

                var idNode = msg["id"];
                if (idNode == null || idNode.GetValueKind() != System.Text.Json.JsonValueKind.Number) continue;
                if (!_pending.TryRemove(idNode.GetValue<int>(), out var tcs)) continue;
                if (msg["ok"]?.GetValue<bool>() == true)
                    tcs.TrySetResult(msg["result"]?.DeepClone());
                else
                {
                    var err = msg["error"];
                    tcs.TrySetException(new EngineException(
                        err?["code"]?.GetValue<string>() ?? "internal",
                        err?["message"]?.GetValue<string>() ?? "未知錯誤"));
                }
            }
        }
        catch { /* pipe closed */ }
    }

    private void ErrLoop(Process p)
    {
        try
        {
            string? line;
            while ((line = p.StandardError.ReadLine()) != null)
            {
                lock (_stderrTail)
                {
                    _stderrTail.AppendLine(line);
                    if (_stderrTail.Length > 8000) _stderrTail.Remove(0, _stderrTail.Length - 8000);
                }
            }
        }
        catch { /* pipe closed */ }
    }

    public string StderrTail()
    {
        lock (_stderrTail)
        {
            var s = _stderrTail.ToString().Trim();
            return s.Length == 0 ? "" : "\n\n" + (s.Length > 1500 ? s[^1500..] : s);
        }
    }

    private void OnExited()
    {
        _ready?.TrySetException(new EngineException("engine-exited", "引擎啟動後立即結束。" + StderrTail()));
        foreach (var kv in _pending)
            if (_pending.TryRemove(kv.Key, out var tcs))
                tcs.TrySetException(new EngineException("engine-exited", "引擎意外結束，請再試一次。" + StderrTail()));
        Exited?.Invoke();
    }

    // ---- requests -------------------------------------------------------------------------
    public async Task<JsonNode?> CallAsync(string cmd, JsonObject? args = null, CancellationToken ct = default)
    {
        if (!IsRunning) await StartAsync();
        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;
        Send(new JsonObject { ["id"] = id, ["cmd"] = cmd, ["args"] = args ?? new JsonObject() });
        using var reg = ct.Register(() =>
        {
            try { Send(new JsonObject { ["id"] = Interlocked.Increment(ref _nextId), ["cmd"] = "cancel", ["args"] = new JsonObject { ["target"] = id } }); }
            catch { /* engine gone */ }
        });
        return await tcs.Task.ConfigureAwait(true);
    }

    private void Send(JsonObject msg)
    {
        var p = _proc ?? throw new EngineException("engine-exited", "引擎未啟動。");
        var line = msg.ToJsonString();
        lock (_writeLock)
        {
            p.StandardInput.WriteLine(line);
            p.StandardInput.Flush();
        }
    }

    public async ValueTask DisposeAsync()
    {
        var p = _proc;
        if (p == null) return;
        try
        {
            if (!p.HasExited)
            {
                Send(new JsonObject { ["id"] = Interlocked.Increment(ref _nextId), ["cmd"] = "shutdown", ["args"] = new JsonObject() });
                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                try { await p.WaitForExitAsync(cts.Token); } catch (OperationCanceledException) { p.Kill(true); }
            }
        }
        catch { /* best effort */ }
        p.Dispose();
        _proc = null;
    }
}
