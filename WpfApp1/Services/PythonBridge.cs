// WpfApp1/Services/PythonBridge.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using WpfApp1.Models;

namespace WpfApp1.Services
{
    public sealed class PythonBridge : IDisposable
    {
        private readonly string _pythonExe;
        private readonly string _scriptPath;
        private readonly string _workingDir; // kept for backward compatibility, but scriptDir wins
        private Process? _proc;
        private AidyState _lastState = AidyState.Starting;

        public event Action<AidyState>? StateChanged;
        public event Action<string>? CommandHeard;
        public event Action<string>? LogLine;

        public PythonBridge(string pythonExe, string scriptPath, string workingDir)
        {
            _pythonExe = pythonExe ?? throw new ArgumentNullException(nameof(pythonExe));
            _scriptPath = scriptPath ?? throw new ArgumentNullException(nameof(scriptPath));
            _workingDir = workingDir ?? throw new ArgumentNullException(nameof(workingDir));
        }

        public void Start()
        {
            if (_proc != null) return;

            var pythonExe = ResolveExe(_pythonExe);
            if (pythonExe == null)
            {
                LogLine?.Invoke($"[Bridge] Python exe not found: {_pythonExe}");
                StateChanged?.Invoke(AidyState.Error);
                return;
            }

            if (!File.Exists(_scriptPath))
            {
                LogLine?.Invoke($"[Bridge] Python script not found: {_scriptPath}");
                StateChanged?.Invoke(AidyState.Error);
                return;
            }

            // IMPORTANT:
            // PythonCore directory is the directory that contains main.py and the 'aidy' package.
            // Since you moved PythonCore under WpfApp1/PythonCore, scriptPath should point to:
            // ...\WpfApp1\PythonCore\main.py
            // Therefore pythonCoreDir = directory of scriptPath.
            var pythonCoreDir = Path.GetDirectoryName(_scriptPath) ?? _workingDir;

            var psi = new ProcessStartInfo
            {
                FileName = pythonExe,
                Arguments = $"-X utf8 \"{_scriptPath}\" --ui",
                WorkingDirectory = pythonCoreDir,

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

            // Make sure imports work (aidy/*).
            // Setting PYTHONPATH to pythonCoreDir ensures: import aidy...
            psi.Environment["PYTHONPATH"] = pythonCoreDir;

            LogLine?.Invoke($"[Bridge] Starting python:");
            LogLine?.Invoke($"         EXE : {psi.FileName}");
            LogLine?.Invoke($"         ARGS: {psi.Arguments}");
            LogLine?.Invoke($"         CWD : {psi.WorkingDirectory}");
            LogLine?.Invoke($"         PYTHONPATH: {psi.Environment["PYTHONPATH"]}");

            _lastState = AidyState.Starting;
            StateChanged?.Invoke(AidyState.Starting);

            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };

            _proc.OutputDataReceived += (_, e) =>
            {
                var line = e.Data ?? "";
                if (!string.IsNullOrWhiteSpace(line))
                    LogLine?.Invoke(line);

                ParseLine(line);
            };

            _proc.ErrorDataReceived += (_, e) =>
            {
                var line = e.Data ?? "";
                if (!string.IsNullOrWhiteSpace(line))
                {
                    LogLine?.Invoke($"ERROR: {line}");

                    // If Python is failing, don't let UI stay in IDLE.
                    // Mark Error on obvious traceback/fatal/module issues.
                    if (line.Contains("Traceback", StringComparison.OrdinalIgnoreCase) ||
                        line.Contains("ModuleNotFoundError", StringComparison.OrdinalIgnoreCase) ||
                        line.Contains("FileNotFoundError", StringComparison.OrdinalIgnoreCase) ||
                        line.Contains("ImportError", StringComparison.OrdinalIgnoreCase) ||
                        line.Contains("Fatal", StringComparison.OrdinalIgnoreCase))
                    {
                        _lastState = AidyState.Error;
                        StateChanged?.Invoke(AidyState.Error);
                    }
                }
            };

            _proc.Exited += (_, __) =>
            {
                try
                {
                    var exitCode = _proc?.ExitCode ?? -1;
                    LogLine?.Invoke($"[Bridge] Python exited with code {exitCode}");

                    if (_lastState == AidyState.Error || exitCode != 0)
                        StateChanged?.Invoke(AidyState.Error);
                    else
                        StateChanged?.Invoke(AidyState.Offline);
                }
                catch
                {
                    StateChanged?.Invoke(AidyState.Error);
                }
            };

            try
            {
                _proc.Start();
                _proc.BeginOutputReadLine();
                _proc.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                LogLine?.Invoke($"[Bridge] Failed to start python: {ex.Message}");
                StateChanged?.Invoke(AidyState.Error);
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
                    "STARTING" => AidyState.Starting,
                    "IDLE" => AidyState.Idle,
                    "LISTENING" => AidyState.Listening,
                    "PROCESSING" => AidyState.Processing,
                    "SPEAKING" => AidyState.Speaking,
                    "CONFIRM" => AidyState.Confirming,
                    "FOLLOWUP" => AidyState.FollowUp,
                    "EXECUTING" => AidyState.Executing,
                    "SUCCESS" => AidyState.Success,
                    "WARNING" => AidyState.Warning,
                    "ERROR" => AidyState.Error,
                    "OFFLINE" => AidyState.Offline,
                    _ => null
                };

                if (s != null)
                {
                    _lastState = s.Value;
                    LogLine?.Invoke($"[Bridge] Parsed state: {s.Value}");
                    StateChanged?.Invoke(s.Value);
                }

                return;
            }

            if (line.StartsWith("COMMAND:", StringComparison.OrdinalIgnoreCase))
            {
                var t = line.Substring("COMMAND:".Length).Trim();
                if (!string.IsNullOrWhiteSpace(t))
                    CommandHeard?.Invoke(t);

                return;
            }

            // Optional crash detection in stdout too
            if (line.Contains("Traceback (most recent call last)", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("ModuleNotFoundError", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("Fatal", StringComparison.OrdinalIgnoreCase))
            {
                _lastState = AidyState.Error;
                StateChanged?.Invoke(AidyState.Error);
            }
        }

        public void Dispose()
        {
            try
            {
                if (_proc != null && !_proc.HasExited)
                    _proc.Kill(entireProcessTree: true);
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

        private static string? ResolveExe(string exe)
        {
            if (string.IsNullOrWhiteSpace(exe))
                return null;

            // If a full path was provided, trust it.
            if (Path.IsPathRooted(exe))
                return File.Exists(exe) ? exe : null;

            // Try relative to working directory first.
            var local = Path.GetFullPath(exe);
            if (File.Exists(local))
                return local;

            // Search PATH for exe / exe.exe
            var name = exe.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? exe : exe + ".exe";
            var path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (var dir in path.Split(';'))
            {
                if (string.IsNullOrWhiteSpace(dir)) continue;
                try
                {
                    var candidate = Path.Combine(dir.Trim(), name);
                    if (File.Exists(candidate))
                        return candidate;
                }
                catch
                {
                    // ignore invalid PATH entries
                }
            }

            return null;
        }
    }
}
