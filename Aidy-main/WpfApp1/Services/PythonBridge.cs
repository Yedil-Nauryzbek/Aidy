// WpfApp1/Services/PythonBridge.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Globalization;
using WpfApp1.Models;

namespace WpfApp1.Services
{
    public sealed class PythonBridge : IDisposable
    {
        private readonly string _pythonExe;
        private readonly string _scriptPath;
        private readonly string _workingDir; // kept for backward compatibility, but scriptDir wins
        private readonly object _stdinSync = new();
        private readonly object _lifecycleSync = new();
        private readonly object _logSync = new();
        private readonly string _logPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Aidy", "logs", "aidy-ui.log");
        private Process? _proc;
        private bool _disposed;
        private bool _voiceActive;
        private AidyState _lastState = AidyState.Starting;

        public event Action<AidyState>? StateChanged;
        public event Action<string>? CommandHeard;
        public event Action<string>? AssistantResponse;
        public event Action? ExitRequested;
        public event Action<double>? AudioLevelChanged;
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

        private void WriteLog(string line)
        {
            lock (_logSync)
            {
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
                    File.AppendAllText(_logPath, $"{DateTimeOffset.Now:O} {line}{Environment.NewLine}", Encoding.UTF8);
                }
                catch (IOException ex) { Debug.WriteLine($"Aidy log write failed: {ex.Message}"); }
                catch (UnauthorizedAccessException ex) { Debug.WriteLine($"Aidy log write failed: {ex.Message}"); }
            }
            LogLine?.Invoke(line);
        }

        public bool Start()
        {
            lock (_lifecycleSync)
            {
                if (_disposed) return false;
                if (_proc != null && !_proc.HasExited) return true;
                _proc?.Dispose();
                _proc = null;
                return StartCore();
            }
        }

        private bool StartCore()
        {

            var pythonExe = ResolvePythonExecutable();
            if (pythonExe == null)
            {
                WriteLog($"[Bridge] Python exe not found. Run setup.ps1 or set AIDY_PYTHON to an installed Python executable.");
                StateChanged?.Invoke(AidyState.Error);
                return false;
            }

            if (!File.Exists(_scriptPath))
            {
                WriteLog($"[Bridge] Python script not found: {_scriptPath}");
                StateChanged?.Invoke(AidyState.Error);
                return false;
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
                Arguments = $"-u -X utf8 \"{_scriptPath}\" --ui",
                WorkingDirectory = pythonCoreDir,

                RedirectStandardOutput = true,
                RedirectStandardError = true,
                RedirectStandardInput = true,
                UseShellExecute = false,
                CreateNoWindow = true,

                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };

            // UTF-8 safety
            psi.Environment["PYTHONIOENCODING"] = "utf-8";
            psi.Environment["PYTHONUTF8"] = "1";
            psi.Environment["PYTHONUNBUFFERED"] = "1";

            // Make sure imports work (aidy/*).
            // Setting PYTHONPATH to pythonCoreDir ensures: import aidy...
            psi.Environment["PYTHONPATH"] = pythonCoreDir;

            WriteLog($"[Bridge] Starting Python: {psi.FileName}; script: {_scriptPath}");

            _lastState = AidyState.Starting;
            StateChanged?.Invoke(AidyState.Starting);

            var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
            _proc = process;

            _proc.OutputDataReceived += (_, e) =>
            {
                var line = e.Data ?? "";
                if (!string.IsNullOrWhiteSpace(line) && !line.StartsWith("AUDIO_LEVEL:", StringComparison.Ordinal))
                    WriteLog(line);

                ParseLine(line);
            };

            _proc.ErrorDataReceived += (_, e) =>
            {
                var line = e.Data ?? "";
                if (!string.IsNullOrWhiteSpace(line))
                {
                    WriteLog($"[Python stderr] {line}");

                    // If Python is failing, don't let UI stay in IDLE.
                    // Mark Error only on clearly fatal traceback/import issues.
                    // Some runtime actions may emit non-fatal stderr lines (including file lookup misses).
                    if (line.Contains("Traceback", StringComparison.OrdinalIgnoreCase) ||
                        line.Contains("ModuleNotFoundError", StringComparison.OrdinalIgnoreCase) ||
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
                    var exitCode = process.ExitCode;
                    WriteLog($"[Bridge] Python exited with code {exitCode}");
                    if (_disposed) return;

                    if (_lastState == AidyState.Error || exitCode != 0)
                        StateChanged?.Invoke(AidyState.Error);
                    else
                        StateChanged?.Invoke(AidyState.Offline);
                }
                catch (Exception ex)
                {
                    WriteLog($"[Bridge] Could not read exit status: {ex.Message}");
                    if (!_disposed) StateChanged?.Invoke(AidyState.Error);
                }
            };

            try
            {
                _proc.Start();
                _proc.BeginOutputReadLine();
                _proc.BeginErrorReadLine();
                return true;
            }
            catch (Exception ex)
            {
                WriteLog($"[Bridge] Failed to start Python: {ex.Message}");
                process.Dispose();
                _proc = null;
                StateChanged?.Invoke(AidyState.Error);
                return false;
            }
        }

        private void ParseLine(string line)
        {
            if (string.IsNullOrWhiteSpace(line)) return;
            if (line.Equals("CONTROL:EXIT_APPLICATION", StringComparison.Ordinal))
            {
                ExitRequested?.Invoke();
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

            if (line.StartsWith("AUDIO_LEVEL:", StringComparison.Ordinal))
            {
                if (double.TryParse(line.AsSpan("AUDIO_LEVEL:".Length), NumberStyles.Float,
                    CultureInfo.InvariantCulture, out var level) && double.IsFinite(level))
                {
                    AudioLevelChanged?.Invoke(Math.Clamp(level, 0.0, 1.0));
                    bool active = level > 0.02;
                    if (active != _voiceActive)
                    {
                        _voiceActive = active;
                        VoiceActivityChanged?.Invoke(active);
                    }
                }
                return;
            }

            if (line.StartsWith("STATE:", StringComparison.OrdinalIgnoreCase))
            {
                var v = line.Substring("STATE:".Length).Trim().ToUpperInvariant();

                AidyState? s = v switch
                {
                    "STARTING" => AidyState.Starting,
                    "MODEL_LOADING" => AidyState.ModelLoading,
                    "IDLE" => AidyState.Idle,
                    "LISTENING" => AidyState.Listening,
                    "COMMAND_LISTENING" => AidyState.CommandListening,
                    "PROCESSING" => AidyState.Processing,
                    // Older cores may still emit this for every background phrase.
                    // Keep the current listening label and ring stable.
                    "TRANSCRIBING" => null,
                    "SPEAKING" => AidyState.Speaking,
                    "CONFIRM" => AidyState.Confirming,
                    "FOLLOWUP" => AidyState.FollowUp,
                    "EXECUTING" => AidyState.Executing,
                    "SUCCESS" => AidyState.Success,
                    "WARNING" => AidyState.Warning,
                    "GRANT_ROLE" => AidyState.GrantRole,
                    "GRANT_DURATION" => AidyState.GrantDuration,
                    "ACCESS_DENIED" => AidyState.AccessDenied,
                    "ERROR" => AidyState.Error,
                    "OFFLINE" => AidyState.Offline,
                    "STOPPED" => AidyState.Offline,
                    _ => null
                };

                if (s != null)
                {
                    _lastState = s.Value;
                    WriteLog($"[Bridge] Parsed state: {s.Value}");
                    StateChanged?.Invoke(s.Value);
                }

                return;
            }

            if (line.StartsWith("AI_RESPONSE:", StringComparison.OrdinalIgnoreCase))
            {
                AssistantResponse?.Invoke(line.Substring("AI_RESPONSE:".Length).Trim());
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
            Process? process;
            lock (_lifecycleSync)
            {
                if (_disposed) return;
                _disposed = true;
                process = _proc;
            }
            try
            {
                if (process != null && !process.HasExited)
                {
                    SendControlCommand("shutdown");
                    if (!process.WaitForExit(5000))
                    {
                        WriteLog("[Bridge] Shutdown timed out; terminating the owned backend process tree.");
                        process.Kill(entireProcessTree: true);
                        process.WaitForExit(2000);
                    }
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[Bridge] Backend shutdown failed: {ex.Message}");
            }
            finally
            {
                process?.Dispose();
                lock (_lifecycleSync) { _proc = null; }
            }
        }

        public bool SendControlCommand(string command)
        {
            if (string.IsNullOrWhiteSpace(command))
            {
                return false;
            }

            try
            {
                var process = _proc;
                if (process == null || process.HasExited)
                {
                    return false;
                }

                lock (_stdinSync)
                {
                    process.StandardInput.WriteLine(command);
                    process.StandardInput.Flush();
                }

                return true;
            }
            catch (Exception ex)
            {
                WriteLog($"[Bridge] Control command failed: {ex.Message}");
                return false;
            }
        }

        private string? ResolvePythonExecutable()
        {
            var configured = Environment.GetEnvironmentVariable("AIDY_PYTHON");
            if (!string.IsNullOrWhiteSpace(configured))
                return ResolveExe(configured.Trim().Trim('"'));

            // Walk upward from packaged content to find the development environment.
            // This also supports an installed application with a neighboring .venv.
            for (var directory = new DirectoryInfo(Path.GetDirectoryName(_scriptPath)!);
                 directory != null;
                 directory = directory.Parent)
            {
                var candidate = Path.Combine(directory.FullName, ".venv", "Scripts", "python.exe");
                if (File.Exists(candidate)) return candidate;
            }
            return ResolveExe(_pythonExe);
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
