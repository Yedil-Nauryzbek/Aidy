// WpfApp1/ViewModels/MainViewModel.cs
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Windows.Input;
using WpfApp1.Models;

namespace WpfApp1.ViewModels
{
    public class MainViewModel : INotifyPropertyChanged
    {
        public const string DefaultPushToTalkKey = "LeftCtrl";

        private string _statusText = "STARTING...";
        private string _logText = "";
        private string _wakeDebugLog = "";
        private string _lastCommand = "";
        private string _timerBadgeText = "";
        private bool _pushToTalkEnabled;
        private string _pushToTalkKey = DefaultPushToTalkKey;
        private bool _autoStartEnabled;
        private bool _voiceIdEnabled;
        private string _selectedMicrophoneDevice = string.Empty;
        private string _selectedOutputDevice = string.Empty;
        private bool _greetingOnStartupEnabled = true;
        private bool _customModeEnabled;
        private string _appDirPath = string.Empty;
        private string _aidiFilePath = string.Empty;
        private string _aidiFileStatus = "No file selected";
        private bool _isAidiFilePathValid = true;
        private int _aidiVolume = AppConfig.DefaultAidiVolume;
        private bool _isPushToTalkKeyCaptureActive;
        private bool _isWaitingForHotkey;
        private AidyState _currentState = AidyState.Starting;

        // Voice sensitivity
        private int _vadThreshold = VoiceSensitivityConfig.DefaultVadThreshold;
        private int _silenceMs = VoiceSensitivityConfig.DefaultSilenceMs;
        private int _minSpeechMs = VoiceSensitivityConfig.DefaultMinSpeechMs;

        // Mouse control
        private int _mouseMovePx = MouseControlConfig.DefaultMovePx;
        private int _mouseScrollClicks = MouseControlConfig.DefaultScrollClicks;
        private int _mouseScrollStep = MouseControlConfig.DefaultScrollStep;

        // Preferred apps
        private string _preferredBrowser = string.Empty;
        private string _preferredMusicApp = string.Empty;

        public string StatusText
        {
            get => _statusText;
            set { _statusText = value; OnPropertyChanged(); }
        }

        public string LogText
        {
            get => _logText;
            set { _logText = value; OnPropertyChanged(); }
        }

        public string WakeDebugLog
        {
            get => _wakeDebugLog;
            set { _wakeDebugLog = value; OnPropertyChanged(); }
        }

        public string LastCommand
        {
            get => _lastCommand;
            set { _lastCommand = value; OnPropertyChanged(); }
        }

        public string TimerBadgeText
        {
            get => _timerBadgeText;
            set { _timerBadgeText = value; OnPropertyChanged(); }
        }

        public bool PushToTalkEnabled
        {
            get => _pushToTalkEnabled;
            set
            {
                if (_pushToTalkEnabled == value) return;
                _pushToTalkEnabled = value;
                if (!value)
                {
                    IsPushToTalkKeyCaptureActive = false;
                    IsWaitingForHotkey = false;
                }

                OnPropertyChanged();
                OnPropertyChanged(nameof(IsPushToTalkHotkeySelectorEnabled));
                UpdateStatusText();
            }
        }

        public string PushToTalkKey
        {
            get => _pushToTalkKey;
            set
            {
                var normalized = NormalizePushToTalkKeyName(value);
                if (string.Equals(_pushToTalkKey, normalized, StringComparison.Ordinal))
                {
                    return;
                }

                _pushToTalkKey = normalized;
                OnPropertyChanged();
                OnPropertyChanged(nameof(PushToTalkKeyDisplay));
            }
        }

        public string PushToTalkKeyDisplay => FormatPushToTalkKeyDisplay(_pushToTalkKey);

        public bool AutoStartEnabled
        {
            get => _autoStartEnabled;
            set
            {
                if (_autoStartEnabled == value) return;
                _autoStartEnabled = value;
                OnPropertyChanged();
            }
        }

        public bool VoiceIdEnabled
        {
            get => _voiceIdEnabled;
            set
            {
                if (_voiceIdEnabled == value) return;
                _voiceIdEnabled = value;
                OnPropertyChanged();
            }
        }

        public ObservableCollection<VoiceUserEntry> VoiceUsers { get; } = new();

        public ObservableCollection<string> MicrophoneDevices { get; } = new();
        public ObservableCollection<string> OutputDevices { get; } = new();

        public string SelectedMicrophoneDevice
        {
            get => _selectedMicrophoneDevice;
            set
            {
                var normalized = value?.Trim() ?? string.Empty;
                if (string.Equals(_selectedMicrophoneDevice, normalized, StringComparison.Ordinal))
                {
                    return;
                }

                _selectedMicrophoneDevice = normalized;
                OnPropertyChanged();
            }
        }

        public string SelectedOutputDevice
        {
            get => _selectedOutputDevice;
            set
            {
                var normalized = value?.Trim() ?? string.Empty;
                if (string.Equals(_selectedOutputDevice, normalized, StringComparison.Ordinal))
                {
                    return;
                }

                _selectedOutputDevice = normalized;
                OnPropertyChanged();
            }
        }

        public bool GreetingOnStartupEnabled
        {
            get => _greetingOnStartupEnabled;
            set
            {
                if (_greetingOnStartupEnabled == value) return;
                _greetingOnStartupEnabled = value;
                OnPropertyChanged();
            }
        }

        public bool CustomModeEnabled
        {
            get => _customModeEnabled;
            set
            {
                if (_customModeEnabled == value) return;
                _customModeEnabled = value;
                OnPropertyChanged();
            }
        }

        public const int CustomModeSlotsMin = 3;
        public const int CustomModeSlotsMax = 5;

        public ObservableCollection<CustomModeSlotViewModel> CustomModeSlots { get; } = new()
        {
            new CustomModeSlotViewModel(0),
            new CustomModeSlotViewModel(1),
            new CustomModeSlotViewModel(2),
        };

        private bool _canAddCustomSlot = true;
        public bool CanAddCustomSlot
        {
            get => _canAddCustomSlot;
            set
            {
                if (_canAddCustomSlot == value) return;
                _canAddCustomSlot = value;
                OnPropertyChanged();
            }
        }

        public void RefreshCanAddCustomSlot()
            => CanAddCustomSlot = CustomModeSlots.Count < CustomModeSlotsMax;

        public string AidiFilePath
        {
            get => _aidiFilePath;
            set
            {
                var normalized = NormalizeAbsolutePath(value);
                if (string.Equals(_aidiFilePath, normalized, StringComparison.OrdinalIgnoreCase))
                {
                    UpdateAidiFileStatus(normalized);
                    return;
                }

                _aidiFilePath = normalized;
                OnPropertyChanged();
                UpdateAidiFileStatus(normalized);
            }
        }

        public string AidiFileStatus
        {
            get => _aidiFileStatus;
            private set
            {
                if (string.Equals(_aidiFileStatus, value, StringComparison.Ordinal))
                {
                    return;
                }

                _aidiFileStatus = value;
                OnPropertyChanged();
            }
        }

        public bool IsAidiFilePathValid
        {
            get => _isAidiFilePathValid;
            private set
            {
                if (_isAidiFilePathValid == value)
                {
                    return;
                }

                _isAidiFilePathValid = value;
                OnPropertyChanged();
            }
        }

        public int AidiVolume
        {
            get => _aidiVolume;
            set
            {
                var normalized = Math.Clamp(value, 0, 100);
                if (_aidiVolume == normalized)
                {
                    return;
                }

                _aidiVolume = normalized;
                OnPropertyChanged();
                OnPropertyChanged(nameof(AidiVolumeDisplay));
            }
        }

        public string AidiVolumeDisplay => AidiVolume == AppConfig.DefaultAidiVolume
            ? $"Normal ({AidiVolume}%)"
            : $"{AidiVolume}%";

        // ── Voice Sensitivity ──────────────────────────────────────

        public int VadThreshold
        {
            get => _vadThreshold;
            set
            {
                var clamped = Math.Clamp(value, 50, 500);
                if (_vadThreshold == clamped) return;
                _vadThreshold = clamped;
                OnPropertyChanged();
                OnPropertyChanged(nameof(VadThresholdDisplay));
            }
        }

        public string VadThresholdDisplay => VadThreshold == VoiceSensitivityConfig.DefaultVadThreshold
            ? $"Default ({VadThreshold})"
            : $"{VadThreshold}";

        public int SilenceMs
        {
            get => _silenceMs;
            set
            {
                var clamped = Math.Clamp(value, 200, 2000);
                if (_silenceMs == clamped) return;
                _silenceMs = clamped;
                OnPropertyChanged();
                OnPropertyChanged(nameof(SilenceMsDisplay));
            }
        }

        public string SilenceMsDisplay => SilenceMs == VoiceSensitivityConfig.DefaultSilenceMs
            ? $"Default ({SilenceMs} ms)"
            : $"{SilenceMs} ms";

        public int MinSpeechMs
        {
            get => _minSpeechMs;
            set
            {
                var clamped = Math.Clamp(value, 50, 500);
                if (_minSpeechMs == clamped) return;
                _minSpeechMs = clamped;
                OnPropertyChanged();
                OnPropertyChanged(nameof(MinSpeechMsDisplay));
            }
        }

        public string MinSpeechMsDisplay => MinSpeechMs == VoiceSensitivityConfig.DefaultMinSpeechMs
            ? $"Default ({MinSpeechMs} ms)"
            : $"{MinSpeechMs} ms";

        // ── Mouse Control ────────────────────────────────────────

        public int MouseMovePx
        {
            get => _mouseMovePx;
            set
            {
                var clamped = Math.Clamp(value, 50, 2000);
                if (_mouseMovePx == clamped) return;
                _mouseMovePx = clamped;
                OnPropertyChanged();
                OnPropertyChanged(nameof(MouseMovePxDisplay));
            }
        }

        public string MouseMovePxDisplay => MouseMovePx == MouseControlConfig.DefaultMovePx
            ? $"Default ({MouseMovePx} px)"
            : $"{MouseMovePx} px";

        public int MouseScrollClicks
        {
            get => _mouseScrollClicks;
            set
            {
                var clamped = Math.Clamp(value, 10, 1000);
                if (_mouseScrollClicks == clamped) return;
                _mouseScrollClicks = clamped;
                OnPropertyChanged();
                OnPropertyChanged(nameof(MouseScrollClicksDisplay));
            }
        }

        public string MouseScrollClicksDisplay => MouseScrollClicks == MouseControlConfig.DefaultScrollClicks
            ? $"Default ({MouseScrollClicks})"
            : $"{MouseScrollClicks}";

        public int MouseScrollStep
        {
            get => _mouseScrollStep;
            set
            {
                var clamped = Math.Clamp(value, 1, 100);
                if (_mouseScrollStep == clamped) return;
                _mouseScrollStep = clamped;
                OnPropertyChanged();
                OnPropertyChanged(nameof(MouseScrollStepDisplay));
            }
        }

        public string MouseScrollStepDisplay => MouseScrollStep == MouseControlConfig.DefaultScrollStep
            ? $"Default ({MouseScrollStep})"
            : $"{MouseScrollStep}";

        public void ResetVoiceSensitivity()
        {
            VadThreshold = VoiceSensitivityConfig.DefaultVadThreshold;
            SilenceMs = VoiceSensitivityConfig.DefaultSilenceMs;
            MinSpeechMs = VoiceSensitivityConfig.DefaultMinSpeechMs;
        }

        public void ResetMouseControl()
        {
            MouseMovePx = MouseControlConfig.DefaultMovePx;
            MouseScrollClicks = MouseControlConfig.DefaultScrollClicks;
            MouseScrollStep = MouseControlConfig.DefaultScrollStep;
        }

        // ── Preferred Apps ──────────────────────────────────────────

        public ObservableCollection<string> BrowserOptions { get; } = new();
        public ObservableCollection<string> MusicAppOptions { get; } = new();

        public string PreferredBrowser
        {
            get => _preferredBrowser;
            set
            {
                if (_preferredBrowser == value) return;
                _preferredBrowser = value;
                OnPropertyChanged();
            }
        }

        public string PreferredMusicApp
        {
            get => _preferredMusicApp;
            set
            {
                if (_preferredMusicApp == value) return;
                _preferredMusicApp = value;
                OnPropertyChanged();
            }
        }

        public string AppDirPath
        {
            get => _appDirPath;
            set { _appDirPath = value; OnPropertyChanged(); }
        }

        public bool IsPushToTalkHotkeySelectorEnabled => PushToTalkEnabled;

        public bool IsPushToTalkKeyCaptureActive
        {
            get => _isPushToTalkKeyCaptureActive;
            set
            {
                if (_isPushToTalkKeyCaptureActive == value) return;
                _isPushToTalkKeyCaptureActive = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(ChangePushToTalkKeyButtonText));
            }
        }

        public string ChangePushToTalkKeyButtonText =>
            IsPushToTalkKeyCaptureActive ? "Press Any Key..." : "Change Key";

        public bool IsWaitingForHotkey
        {
            get => _isWaitingForHotkey;
            set
            {
                if (_isWaitingForHotkey == value) return;
                _isWaitingForHotkey = value;
                OnPropertyChanged();
                UpdateStatusText();
            }
        }

        public AidyState CurrentState
        {
            get => _currentState;
            set
            {
                if (_currentState == value) return;
                _currentState = value;
                OnPropertyChanged();
                UpdateStatusText();
            }
        }

        public void AppendLogLine(string line, int maxLines = 1200)
        {
            LogText = AppendBounded(LogText, line, maxLines, ref _logLineCount);
        }

        public void AppendWakeDebugLine(string line, int maxLines = 320)
        {
            WakeDebugLog = AppendBounded(WakeDebugLog, line, maxLines, ref _wakeDebugLineCount);
        }

        public void ClearWakeDebugLog()
        {
            WakeDebugLog = "";
            _wakeDebugLineCount = 0;
        }

        public void ApplyPushToTalkConfig(bool enabled, string? keyName)
        {
            PushToTalkKey = keyName ?? DefaultPushToTalkKey;
            PushToTalkEnabled = enabled;
            IsPushToTalkKeyCaptureActive = false;
            IsWaitingForHotkey = false;
        }

        public void SetAudioDevices(
            IEnumerable<string> microphoneDevices,
            IEnumerable<string> outputDevices,
            string? selectedMicrophone,
            string? selectedOutputDevice)
        {
            ReplaceCollection(MicrophoneDevices, microphoneDevices);
            ReplaceCollection(OutputDevices, outputDevices);

            SelectedMicrophoneDevice = ResolveSelection(MicrophoneDevices, selectedMicrophone);
            SelectedOutputDevice = ResolveSelection(OutputDevices, selectedOutputDevice);
        }

        public bool TrySetPushToTalkKey(Key key)
        {
            if (key == Key.None || key == Key.System)
            {
                return false;
            }

            PushToTalkKey = NormalizePushToTalkKeyName(key.ToString());
            return true;
        }

        public static string NormalizePushToTalkKeyName(string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return DefaultPushToTalkKey;
            }

            if (Enum.TryParse(value.Trim(), ignoreCase: true, out Key parsed) &&
                parsed != Key.None &&
                parsed != Key.System)
            {
                return parsed.ToString();
            }

            return DefaultPushToTalkKey;
        }

        public static string FormatPushToTalkKeyDisplay(string? keyName)
        {
            if (!Enum.TryParse(NormalizePushToTalkKeyName(keyName), ignoreCase: true, out Key key))
            {
                return "Left Ctrl";
            }

            return key switch
            {
                Key.LeftCtrl => "Left Ctrl",
                Key.RightCtrl => "Right Ctrl",
                Key.LeftAlt => "Left Alt",
                Key.RightAlt => "Right Alt",
                Key.LeftShift => "Left Shift",
                Key.RightShift => "Right Shift",
                Key.LWin => "Left Win",
                Key.RWin => "Right Win",
                _ => key.ToString()
            };
        }

        private void UpdateAidiFileStatus(string filePath)
        {
            if (string.IsNullOrWhiteSpace(filePath))
            {
                IsAidiFilePathValid = true;
                AidiFileStatus = "No file selected";
                return;
            }

            if (File.Exists(filePath))
            {
                IsAidiFilePathValid = true;
                AidiFileStatus = "File exists";
                return;
            }

            IsAidiFilePathValid = false;
            AidiFileStatus = "File does not exist";
        }

        private static string NormalizeAbsolutePath(string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return string.Empty;
            }

            try
            {
                return Path.GetFullPath(value.Trim());
            }
            catch
            {
                return string.Empty;
            }
        }

        private static void ReplaceCollection(ObservableCollection<string> collection, IEnumerable<string> source)
        {
            collection.Clear();
            foreach (var item in source
                .Where(v => !string.IsNullOrWhiteSpace(v))
                .Select(v => v.Trim())
                .Distinct(StringComparer.OrdinalIgnoreCase))
            {
                collection.Add(item);
            }
        }

        private static string ResolveSelection(IEnumerable<string> options, string? preferred)
        {
            var preferredNormalized = preferred?.Trim() ?? string.Empty;
            if (!string.IsNullOrWhiteSpace(preferredNormalized))
            {
                var match = options.FirstOrDefault(v => string.Equals(v, preferredNormalized, StringComparison.OrdinalIgnoreCase));
                if (!string.IsNullOrWhiteSpace(match))
                {
                    return match;
                }
            }

            return options.FirstOrDefault() ?? string.Empty;
        }

        // Backing line counts to avoid re-splitting the entire string every append.
        private int _logLineCount;
        private int _wakeDebugLineCount;

        private static string AppendBounded(string existing, string line, int maxLines, ref int lineCount)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                return existing;
            }

            var normalized = line.Replace("\r", "").TrimEnd();
            if (string.IsNullOrWhiteSpace(normalized))
            {
                return existing;
            }

            if (string.IsNullOrEmpty(existing))
            {
                lineCount = 1;
                return normalized;
            }

            lineCount++;
            var combined = existing + "\n" + normalized;

            if (lineCount <= maxLines)
            {
                return combined;
            }

            // Only trim when we exceed the limit — find the first newline and chop.
            var idx = combined.IndexOf('\n');
            if (idx >= 0)
            {
                // lineCount stays at maxLines after trim (we added 1, removed 1).
                lineCount = maxLines;
                return combined.Substring(idx + 1);
            }

            lineCount = 1;
            return combined;
        }

        private void UpdateStatusText()
        {
            if (IsWaitingForHotkey && CurrentState == AidyState.Idle)
            {
                StatusText = "WAITING FOR HOTKEY";
                return;
            }

            StatusText = CurrentState switch
            {
                AidyState.Starting => "STARTING...",
                AidyState.Idle => "IDLE",
                AidyState.Listening => "LISTENING...",
                AidyState.CommandListening => "SAY YOUR COMMAND...",
                AidyState.Processing => "PROCESSING...",
                AidyState.Speaking => "SPEAKING...",
                AidyState.Confirming => "YES OR NO",
                AidyState.FollowUp => "HOW MUCH? (1-10)",
                AidyState.GrantRole => "USER OR ADMIN?",
                AidyState.GrantDuration => "BY HOW MUCH? (1-60)",
                AidyState.Executing => "EXECUTING...",
                AidyState.Success => "FINISHED",
                AidyState.Warning => "WARNING",
                AidyState.AccessDenied => "ACCESS DENIED",
                AidyState.Error => "ERROR",
                AidyState.Offline => "OFFLINE",
                _ => "IDLE"
            };
        }

        public void UpdateVoiceUsers(string[] entries)
        {
            VoiceUsers.Clear();
            foreach (var entry in entries)
            {
                var parts = entry.Split('|');
                if (parts.Length >= 4 && int.TryParse(parts[0].Trim(), out int id))
                {
                    VoiceUsers.Add(new VoiceUserEntry
                    {
                        Id = id,
                        Label = parts[1].Trim(),
                        Role = parts[2].Trim(),
                        Expires = parts[3].Trim(),
                    });
                }
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected virtual void OnPropertyChanged([CallerMemberName] string? propertyName = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    public class VoiceUserEntry
    {
        public int Id { get; set; }
        public string Label { get; set; } = "";
        public string Role { get; set; } = "";
        public string Expires { get; set; } = "";

        public string RoleIcon => Role switch
        {
            "Admin" => "\U0001F451",
            "User" => "\U0001F464",
            "Guest" => "\u231B",
            _ => "\u2753"
        };
    }
}
