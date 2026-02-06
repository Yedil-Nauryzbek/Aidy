// WpfApp1/ViewModels/MainViewModel.cs
using System.ComponentModel;
using System.Runtime.CompilerServices;
using WpfApp1.Models;

namespace WpfApp1.ViewModels
{
    public class MainViewModel : INotifyPropertyChanged
    {
        private string _statusText = "STARTING...";
        private string _logText = "";
        private string _lastCommand = "";
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

        public string LastCommand
        {
            get => _lastCommand;
            set { _lastCommand = value; OnPropertyChanged(); }
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
                    AidyState.Processing => "PROCESSING...",
                    AidyState.Speaking => "SPEAKING...",
                    AidyState.Confirming => "CONFIRM / CANCEL",
                    AidyState.Executing => "EXECUTING...",
                    AidyState.Success => "FINISHED",
                    AidyState.Warning => "WARNING",
                    AidyState.Error => "ERROR",
                    AidyState.Offline => "OFFLINE",
                    _ => "IDLE"
                };
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected virtual void OnPropertyChanged([CallerMemberName] string? propertyName = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

