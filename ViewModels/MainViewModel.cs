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
        private bool _localModeEnabled;
        private string _appDirPath = string.Empty;
        private string _aidiFilePath = string.Empty;
        private string _aidiFileStatus = "No file selected";
        private bool _isAidiFilePathValid = true;
        private int _aidiVolume = AppConfig.DefaultAidiVolume;
        private bool _isPushToTalkKeyCaptureActive;
        private bool _isWaitingForHotkey;
        private AidyState _currentState = AidyState.Starting;

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

        public bool LocalModeEnabled
        {
            get => _localModeEnabled;
            set
            {
                if (_localModeEnabled == value) return;
                _localModeEnabled = value;
                OnPropertyChanged();
            }
        }

        public ObservableCollection<LocalModeSlotViewModel> LocalModeSlots { get; } = new()
        {
            new LocalModeSlotViewModel(0),
            new LocalModeSlotViewModel(1),
            new LocalModeSlotViewModel(2),
        };

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
            LogText = AppendBounded(LogText, line, maxLines);
        }

        public void AppendWakeDebugLine(string line, int maxLines = 320)
        {
            WakeDebugLog = AppendBounded(WakeDebugLog, line, maxLines);
        }

        public void ClearWakeDebugLog()
        {
            WakeDebugLog = "";
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

        private static string AppendBounded(string existing, string line, int maxLines)
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

            var combined = string.IsNullOrEmpty(existing)
                ? normalized
                : existing + "\n" + normalized;

            var lines = combined.Split('\n', StringSplitOptions.RemoveEmptyEntries);
            if (lines.Length <= maxLines)
            {
                return string.Join('\n', lines);
            }

            var tail = lines[^maxLines..];
            return string.Join('\n', tail);
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
                AidyState.CommandListening => "LISTENING FOR COMMAND...",
                AidyState.Processing => "PROCESSING...",
                AidyState.Speaking => "SPEAKING...",
                AidyState.Confirming => "YES OR NO",
                AidyState.FollowUp => "HOW MUCH? (1-10)",
                AidyState.Executing => "EXECUTING...",
                AidyState.Success => "FINISHED",
                AidyState.Warning => "WARNING",
                AidyState.Error => "ERROR",
                AidyState.Offline => "OFFLINE",
                _ => "IDLE"
            };
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected virtual void OnPropertyChanged([CallerMemberName] string? propertyName = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
