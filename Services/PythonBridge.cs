// WpfApp1/Services/PythonBridge.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using WpfApp1.Models;

namespace WpfApp1.Services
{
    public sealed class PythonBridge : IDisposable
    {
        private readonly string _pythonExe;
        private readonly string _scriptPath;
        private readonly string _workingDir; // kept for backward compatibility, but scriptDir wins
        private readonly object _stdinSync = new();
        private Process? _proc;
        private CancellationTokenSource? _ioCts;
        private Task? _stdoutTask;
        private Task? _stderrTask;
        private AidyState _lastState = AidyState.Starting;
        private bool _disposed;
        private int _restartCount;
        private const int MaxAutoRestarts = 5;
        private DateTime _lastRestartTime = DateTime.MinValue;

        public event Action<AidyState>? StateChanged;
        public event Action<string>? CommandHeard;
        public event Action<string, int, int>? TimerChanged;
        public event Action<bool>? StudyModeChanged;
        public event Action<bool>? CustomModeChanged;
        public event Action<bool>? VoiceActivityChanged;           // from repo
        public event Action? EnrollmentFinished;                    // from yours
        public event Action? EnrollmentStarted;                     // from yours
        public event Action<string, string>? EnrollmentTextChanged; // from yours (statusText, progressText)
        public event Action<string[]>? VoiceUsersChanged;           // label|role|expires entries
        public event Action<string>? LogLine;

        public PythonBridge(string pythonExe, string scriptPath, string workingDir)
        {
            _pythonExe = pythonExe ?? throw new ArgumentNullException(nameof(pythonExe));
            _scriptPath = scriptPath ?? throw new ArgumentNullException(nameof(scriptPath));
            _workingDir = workingDir ?? throw new ArgumentNullException(nameof(workingDir));
        }

        private void CleanupProcess()
        {
            try { _ioCts?.Cancel(); } catch { }
            try
            {
                Task.WaitAll(new[]
                {
                    _stdoutTask ?? Task.CompletedTask,
                    _stderrTask ?? Task.CompletedTask,
                }, 500);
            }
            catch { }
            try { _ioCts?.Dispose(); } catch { }
            _ioCts = null;
            _stdoutTask = null;
            _stderrTask = null;
            try { _proc?.Dispose(); } catch { }
            _proc = null;
        }

        public void Start()
        {
            if (_proc != null) return;
            _lastRestartTime = DateTime.UtcNow;

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

            // PythonCore directory is the directory that contains main.py and the 'aidy' package.
            var pythonCoreDir = Path.GetDirectoryName(_scriptPath) ?? _workingDir;

            var psi = new ProcessStartInfo
            {
                FileName = pythonExe,
                Arguments = $"-u -X utf8 \"{_scriptPath}\" --ui",
                WorkingDirectory = pythonCoreDir,

                RedirectStandardOutput = true,
                RedirectStandardError = true,
                RedirectStandardInput = true,
                UseShellExecute = false,
                CreateNoWindow = true,

                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                StandardInputEncoding = Encoding.UTF8,
            };

            // UTF-8 safety
            psi.Environment["PYTHONIOENCODING"] = "utf-8";
            psi.Environment["PYTHONUTF8"] = "1";
            // Force fully unbuffered stdout/stderr at C runtime level
            psi.Environment["PYTHONUNBUFFERED"] = "1";

            // Make sure imports work (aidy/*).
            psi.Environment["PYTHONPATH"] = pythonCoreDir;

            LogLine?.Invoke("[Bridge] Starting python:");
            LogLine?.Invoke($"         EXE : {psi.FileName}");
            LogLine?.Invoke($"         ARGS: {psi.Arguments}");
            LogLine?.Invoke($"         CWD : {psi.WorkingDirectory}");
            LogLine?.Invoke($"         PYTHONPATH: {psi.Environment["PYTHONPATH"]}");

            _lastState = AidyState.Starting;
            StateChanged?.Invoke(AidyState.Starting);

            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };

            _proc.Exited += (_, __) =>
            {
                try
                {
                    var exitCode = _proc?.ExitCode ?? -1;
                    LogLine?.Invoke($"[Bridge] Python exited with code {exitCode}");

                    if (_disposed)
                    {
                        StateChanged?.Invoke(AidyState.Offline);
                        return;
                    }

                    // Reset restart counter if the process ran for more than 30 seconds
                    if ((DateTime.UtcNow - _lastRestartTime).TotalSeconds > 30)
                        _restartCount = 0;

                    if (_restartCount < MaxAutoRestarts)
                    {
                        _restartCount++;
                        LogLine?.Invoke($"[Bridge] Auto-restarting python (attempt {_restartCount}/{MaxAutoRestarts})...");
                        StateChanged?.Invoke(AidyState.Starting);

                        // Clean up old process resources before restart
                        CleanupProcess();

                        Task.Run(async () =>
                        {
                            await Task.Delay(1500);
                            if (!_disposed)
                                Start();
                        });
                    }
                    else
                    {
                        LogLine?.Invoke("[Bridge] Max auto-restart attempts reached.");
                        if (_lastState == AidyState.Error || exitCode != 0)
                            StateChanged?.Invoke(AidyState.Error);
                        else
                            StateChanged?.Invoke(AidyState.Offline);
                    }
                }
                catch
                {
                    StateChanged?.Invoke(AidyState.Error);
                }
            };

            try
            {
                _proc.Start();
                _ioCts = new CancellationTokenSource();
                _stdoutTask = PumpStreamAsync(_proc.StandardOutput, isError: false, _ioCts.Token);
                _stderrTask = PumpStreamAsync(_proc.StandardError, isError: true, _ioCts.Token);
            }
            catch (Exception ex)
            {
                LogLine?.Invoke($"[Bridge] Failed to start python: {ex.Message}");
                StateChanged?.Invoke(AidyState.Error);
            }
        }

        private async Task PumpStreamAsync(StreamReader reader, bool isError, CancellationToken token)
        {
            try
            {
                while (!token.IsCancellationRequested)
                {
                    string? line;
                    try
                    {
                        line = await reader.ReadLineAsync(token).ConfigureAwait(false);
                    }
                    catch (OperationCanceledException)
                    {
                        break;
                    }
                    catch (ObjectDisposedException)
                    {
                        break;
                    }

                    if (line == null)
                        break;

                    if (string.IsNullOrWhiteSpace(line))
                        continue;

                    if (isError)
                    {
                        LogLine?.Invoke($"ERROR: {line}");
                        if (IsFatalPythonLine(line))
                        {
                            _lastState = AidyState.Error;
                            StateChanged?.Invoke(AidyState.Error);
                        }
                    }
                    else
                    {
                        LogLine?.Invoke(line);
                        ParseLine(line);
                    }
                }
            }
            catch (Exception ex)
            {
                LogLine?.Invoke($"[Bridge] Stream reader error: {ex.Message}");
            }
        }

        private static bool IsFatalPythonLine(string line)
        {
            return
                line.Contains("Traceback", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("ModuleNotFoundError", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("ImportError", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("Fatal", StringComparison.OrdinalIgnoreCase);
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
                    "COMMAND_LISTENING" => AidyState.CommandListening,
                    "PROCESSING" => AidyState.Processing,
                    "SPEAKING" => AidyState.Speaking,
                    "CONFIRM" => AidyState.Confirming,
                    "FOLLOWUP" => AidyState.FollowUp,
                    "GRANT_ROLE" => AidyState.GrantRole,
                    "GRANT_DURATION" => AidyState.GrantDuration,
                    "EXECUTING" => AidyState.Executing,
                    "SUCCESS" => AidyState.Success,
                    "WARNING" => AidyState.Warning,
                    "ACCESS_DENIED" => AidyState.AccessDenied,
                    "ERROR" => AidyState.Error,
                    "OFFLINE" => AidyState.Offline,
                    _ => null
                };

                if (s != null && s.Value != _lastState)
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

            if (line.StartsWith("TIMER:", StringComparison.OrdinalIgnoreCase))
            {
                // Format: TIMER:{event}:{remaining_seconds}:{total_seconds}
                var payload = line.Substring("TIMER:".Length).Trim();
                var parts = payload.Split(':', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 3)
                {
                    var eventName = parts[0].Trim().ToLowerInvariant();
                    _ = int.TryParse(parts[1], out var remaining);
                    _ = int.TryParse(parts[2], out var total);
                    TimerChanged?.Invoke(eventName, remaining, total);
                }
                return;
            }

            if (line.StartsWith("STUDYMODE:", StringComparison.OrdinalIgnoreCase))
            {
                var payload = line.Substring("STUDYMODE:".Length).Trim().ToLowerInvariant();
                if (payload is "on" or "1" or "true")
                {
                    StudyModeChanged?.Invoke(true);
                }
                else if (payload is "off" or "0" or "false")
                {
                    StudyModeChanged?.Invoke(false);
                }
                return;
            }

            if (line.StartsWith("CUSTOMMODE:", StringComparison.OrdinalIgnoreCase))
            {
                var payload = line.Substring("CUSTOMMODE:".Length).Trim().ToLowerInvariant();
                if (payload is "on" or "1" or "true")
                    CustomModeChanged?.Invoke(true);
                else if (payload is "off" or "0" or "false")
                    CustomModeChanged?.Invoke(false);
                return;
            }

            // ── from repo: voice activity ─────────────────────────────────
            if (line.StartsWith("VOICE_ACTIVITY:", StringComparison.OrdinalIgnoreCase))
            {
                var payload = line.Substring("VOICE_ACTIVITY:".Length).Trim().ToLowerInvariant();
                if (payload is "on" or "1" or "true")
                    VoiceActivityChanged?.Invoke(true);
                else if (payload is "off" or "0" or "false")
                    VoiceActivityChanged?.Invoke(false);
                return;
            }

            // ── from yours: enrollment events ─────────────────────────────
            if (line.StartsWith("EVENT:", StringComparison.OrdinalIgnoreCase))
            {
                var evt = line.Substring("EVENT:".Length).Trim().ToUpperInvariant();
                if (evt == "ENROLLMENT_FINISHED")
                    EnrollmentFinished?.Invoke();
                else if (evt == "ENROLL_STARTED")
                    EnrollmentStarted?.Invoke();
                return;
            }

            if (line.StartsWith("CONTROL:enroll_text:", StringComparison.OrdinalIgnoreCase))
            {
                // Format: CONTROL:enroll_text:<status>|<progress>
                var payload = line.Substring("CONTROL:enroll_text:".Length);
                var sep = payload.IndexOf('|');
                var status = sep >= 0 ? payload.Substring(0, sep).Trim() : payload.Trim();
                var progress = sep >= 0 ? payload.Substring(sep + 1).Trim() : string.Empty;
                EnrollmentTextChanged?.Invoke(status, progress);
                return;
            }

            if (line.StartsWith("CONTROL:voice_users:", StringComparison.OrdinalIgnoreCase))
            {
                // Format: CONTROL:voice_users:label|role|expires;label|role|expires;...
                var payload = line.Substring("CONTROL:voice_users:".Length).Trim();
                var entries = string.IsNullOrEmpty(payload)
                    ? Array.Empty<string>()
                    : payload.Split(';', StringSplitOptions.RemoveEmptyEntries);
                VoiceUsersChanged?.Invoke(entries);
                return;
            }

            if (IsFatalPythonLine(line))
            {
                _lastState = AidyState.Error;
                StateChanged?.Invoke(AidyState.Error);
            }
        }

        public void Dispose()
        {
            _disposed = true;
            try
            {
                _ioCts?.Cancel();
                try
                {
                    Task.WaitAll(new[]
                    {
                        _stdoutTask ?? Task.CompletedTask,
                        _stderrTask ?? Task.CompletedTask,
                    }, 500);
                }
                catch
                {
                    // ignore
                }

                if (_proc != null && !_proc.HasExited)
                    _proc.Kill(entireProcessTree: true);
            }
            catch
            {
                // ignore
            }
            finally
            {
                try { _ioCts?.Dispose(); } catch { }
                _ioCts = null;
                _stdoutTask = null;
                _stderrTask = null;
                try { _proc?.Dispose(); } catch { }
                _proc = null;
            }
        }

        public bool SendControlCommand(string command)
        {
            if (string.IsNullOrWhiteSpace(command))
            {
                Debug.WriteLine("[Bridge] SendControlCommand: empty command, skipped.");
                return false;
            }

            try
            {
                var process = _proc;
                if (process == null)
                {
                    Debug.WriteLine($"[Bridge] SendControlCommand '{command}': FAILED — _proc is null.");
                    LogLine?.Invoke($"[Bridge] SendControlCommand '{command}': FAILED — _proc is null.");
                    return false;
                }

                if (process.HasExited)
                {
                    Debug.WriteLine($"[Bridge] SendControlCommand '{command}': FAILED — process has already exited.");
                    LogLine?.Invoke($"[Bridge] SendControlCommand '{command}': FAILED — process has already exited.");
                    return false;
                }

                lock (_stdinSync)
                {
                    Debug.WriteLine($"[Bridge] Writing to stdin: '{command}'");
                    process.StandardInput.WriteLine(command);
                    process.StandardInput.Flush();
                    Debug.WriteLine($"[Bridge] stdin write + flush OK for: '{command}'");
                }

                LogLine?.Invoke($"[Bridge] Sent: {command}");
                return true;
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[Bridge] SendControlCommand '{command}' EXCEPTION: {ex.Message}");
                LogLine?.Invoke($"[Bridge] Control command failed: {ex.Message}");
                return false;
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
