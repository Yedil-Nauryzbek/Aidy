// WpfApp1/ViewModels/MainViewModel.cs
using System;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using WpfApp1.Models;

namespace WpfApp1.ViewModels
{
    public class MainViewModel : INotifyPropertyChanged
    {
        private string _statusText = "STARTING...";
        private string _logText = "";
        private string _wakeDebugLog = "";
        private string _lastCommand = "";
        private string _timerBadgeText = "";
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

        public AidyState CurrentState
        {
            get => _currentState;
            set
            {
                if (_currentState == value) return;
                _currentState = value;
                OnPropertyChanged();

                StatusText = value switch
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

        public event PropertyChangedEventHandler? PropertyChanged;
        protected virtual void OnPropertyChanged([CallerMemberName] string? propertyName = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

