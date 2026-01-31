// WpfApp1/Services/PythonBridge.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using WpfApp1.Models;

namespace WpfApp1.Services
{
    public sealed class PythonBridge : IDisposable
    {
        private readonly string _pythonExe;
        private readonly string _scriptPath;
        private readonly string _workingDir;
        private Process? _proc;

        public event Action<AidyState>? StateChanged;
        public event Action<string>? CommandHeard;
        public event Action<string>? LogLine;

        public PythonBridge(string pythonExe, string scriptPath, string workingDir)
        {
            _pythonExe = pythonExe;
            _scriptPath = scriptPath;
            _workingDir = workingDir;
        }

        public void Start()
        {
            if (_proc != null) return;

            var psi = new ProcessStartInfo
            {
                FileName = _pythonExe,
                Arguments = $"-X utf8 \"{_scriptPath}\" --ui",
                WorkingDirectory = _workingDir,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };

            // UTF-8 safety
            psi.Environment["PYTHONIOENCODING"] = "utf-8";
            psi.Environment["PYTHONUTF8"] = "1";

            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };

            try
            {
                _proc.Start();
            }
            catch (Exception ex)
            {
                LogLine?.Invoke($"[Bridge] Failed to start python: {ex.Message}");
                StateChanged?.Invoke(AidyState.Offline);
                return;
            }

            Task.Run(() => Pump(_proc.StandardOutput));
            Task.Run(() => Pump(_proc.StandardError));

            // optional: mark online once started
            StateChanged?.Invoke(AidyState.Idle);
        }

        private void Pump(StreamReader reader)
        {
            string? line;
            while ((line = reader.ReadLine()) != null)
            {
                // Raw line for debug panel
                LogLine?.Invoke(line);

                // Parse only protocol lines
                ParseLine(line);
            }
        }

        private void ParseLine(string line)
        {
            if (string.IsNullOrWhiteSpace(line)) return;

            if (line.StartsWith("STATE:", StringComparison.OrdinalIgnoreCase))
            {
                var v = line.Substring("STATE:".Length).Trim().ToUpperInvariant();

                AidyState? s = v switch
                {
                    "IDLE" => AidyState.Idle,
                    "LISTENING" => AidyState.Listening,
                    "PROCESSING" => AidyState.Processing,
                    "SPEAKING" => AidyState.Speaking,

                    "EXECUTING" => AidyState.Executing,
                    "SUCCESS" => AidyState.Success,
                    "WARNING" => AidyState.Warning,
                    "ERROR" => AidyState.Error,
                    "OFFLINE" => AidyState.Offline,

                    _ => null
                };

                if (s != null)
                    StateChanged?.Invoke(s.Value);

                return;
            }

            if (line.StartsWith("COMMAND:", StringComparison.OrdinalIgnoreCase))
            {
                var t = line.Substring("COMMAND:".Length).Trim();
                if (!string.IsNullOrWhiteSpace(t))
                    CommandHeard?.Invoke(t);

                return;
            }

            // Optional: detect python crash/fatal patterns and map to Offline/Error
            // You can keep this minimal to avoid false positives.
            if (line.Contains("Traceback (most recent call last)", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("Fatal:", StringComparison.OrdinalIgnoreCase))
            {
                StateChanged?.Invoke(AidyState.Error);
            }
        }

        public void Dispose()
        {
            try
            {
                if (_proc != null)
                {
                    if (!_proc.HasExited)
                    {
                        // Try graceful close first (optional)
                        // _proc.CloseMainWindow();  // usually no window since CreateNoWindow=true

                        // Force kill
                        _proc.Kill(entireProcessTree: true);
                    }
                }
            }
            catch
            {
                // ignore
            }
            finally
            {
                try { _proc?.Dispose(); } catch { }
                _proc = null;
            }
        }
    }
}
