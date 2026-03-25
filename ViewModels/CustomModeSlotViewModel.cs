// WpfApp1/ViewModels/CustomModeSlotViewModel.cs
using System.ComponentModel;
using System.Runtime.CompilerServices;
using WpfApp1.Models;

namespace WpfApp1.ViewModels
{
    public class CustomModeSlotViewModel : INotifyPropertyChanged
    {
        public int Index { get; }
        public string SlotNumber => (Index + 1).ToString();

        private string _actionType = string.Empty;
        private string _target = string.Empty;
        private string _displayName = string.Empty;

        public CustomModeSlotViewModel(int index) => Index = index;

        public string ActionType
        {
            get => _actionType;
            set
            {
                if (_actionType == value) return;
                _actionType = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(ActionTypeDisplay));
                OnPropertyChanged(nameof(IsEmpty));
            }
        }

        public string Target
        {
            get => _target;
            set
            {
                if (_target == value) return;
                _target = value;
                OnPropertyChanged();
                OnPropertyChanged(nameof(IsEmpty));
            }
        }

        public string DisplayName
        {
            get => _displayName;
            set
            {
                if (_displayName == value) return;
                _displayName = value;
                OnPropertyChanged();
            }
        }

        public bool IsEmpty => string.IsNullOrEmpty(_target);

        public string ActionTypeDisplay => _actionType switch
        {
            "open_app"  => "Open App",
            "close_app" => "Close App",
            "open_url"  => "Website",
            "function"  => "Function",
            _ => string.Empty,
        };

        public void Apply(CustomModeSlot slot)
        {
            ActionType  = slot.ActionType;
            Target      = slot.Target;
            DisplayName = slot.DisplayName;
        }

        public void Clear()
        {
            ActionType  = string.Empty;
            Target      = string.Empty;
            DisplayName = string.Empty;
        }

        public CustomModeSlot ToModel() => new CustomModeSlot
        {
            ActionType  = _actionType,
            Target      = _target,
            DisplayName = _displayName,
        };

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
