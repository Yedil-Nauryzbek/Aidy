// WpfApp1/Views/MainWindow.xaml.cs
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Navigation;
using WpfApp1.Models;
using WpfApp1.Services;
using WpfApp1.ViewModels;

namespace WpfApp1.Views
{
    public partial class MainWindow : Window
    {
        private readonly MainViewModel _vm;
        private readonly PythonBridge _bridge;

        private DoubleAnimation _rotateSlow = null!;
        private DoubleAnimation _rotateFast = null!;
        private DoubleAnimation _glowIdle = null!;
        private DoubleAnimation _glowActive = null!;
        private DoubleAnimation _waveOff = null!;
        private DoubleAnimation _waveSpeaking = null!;
        private DoubleAnimation _waveProcessing = null!;

        // ===== Ring storyboard controller =====
        private Storyboard? _ringSb;

        public MainWindow()
        {
            InitializeComponent();

            AutoStart.Enable();

            _vm = new MainViewModel();
            DataContext = _vm;

            _vm.PropertyChanged += VmOnPropertyChanged;

            var baseDir = AppDomain.CurrentDomain.BaseDirectory;
            var scriptPath = Path.Combine(baseDir, "PythonCore", "main.py");

            _bridge = new PythonBridge(
                pythonExe: "python",
                scriptPath: scriptPath,
                workingDir: baseDir
            );

            _bridge.StateChanged += s => Dispatcher.Invoke(() => { _vm.CurrentState = s; Console.WriteLine($"[UI] State changed to {s}"); });
            _bridge.LogLine += line => Dispatcher.Invoke(() => _vm.LogText += line + "\n");

            // Show last command, but hide internal/system keywords (exit, etc.)
            _bridge.CommandHeard += t => Dispatcher.Invoke(() =>
            {
                _vm.LastCommand = FormatUserFacingCommand(t);
            });

            Loaded += (_, __) =>
            {
                ShowPage("AIDY");
                BuildAnimations();
                _vm.CurrentState = AidyState.Starting;
                ApplyState(_vm.CurrentState);
                _bridge.Start();
            };

            Closing += (_, __) => _bridge.Dispose();
        }

        private void VmOnPropertyChanged(object? sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(MainViewModel.CurrentState))
                ApplyState(_vm.CurrentState);
        }

        // =========================
        // EMAIL LINK (mailto)
        // =========================
        private void Hyperlink_RequestNavigate(object sender, RequestNavigateEventArgs e)
        {
            // Opens default mail client / browser handler for mailto:
            Process.Start(new ProcessStartInfo(e.Uri.AbsoluteUri)
            {
                UseShellExecute = true
            });
            e.Handled = true;
        }

        // =========================
        // LAST COMMAND FILTER
        // =========================
        private string FormatUserFacingCommand(string? raw)
        {
            if (string.IsNullOrWhiteSpace(raw))
                return "";

            var t = raw.Trim().Trim('"').Trim();

            // Hide internal/system commands from UI
            // Add more if needed
            if (IsHiddenCommand(t))
                return "";

            return $"\"{t}\"";
        }

        private bool IsHiddenCommand(string t)
        {
            var s = t.Trim().ToLowerInvariant();

            // core internal words to hide
            if (s == "exit") return true;
            if (s == "cancel") return true;

            // window-switch internal controls (optional)
            if (s == "left") return true;
            if (s == "right") return true;
            if (s == "done") return true;

            // empty / noise
            if (s.Length == 0) return true;

            return false;
        }

        // =========================
        // ANIMATIONS
        // =========================
        private void BuildAnimations()
        {
            _rotateSlow = new DoubleAnimation(0, 360, new Duration(TimeSpan.FromSeconds(18)))
            { RepeatBehavior = RepeatBehavior.Forever };

            _rotateFast = new DoubleAnimation(0, 360, new Duration(TimeSpan.FromSeconds(6)))
            { RepeatBehavior = RepeatBehavior.Forever };

            _glowIdle = new DoubleAnimation
            {
                From = 0.995,
                To = 1.015,
                Duration = new Duration(TimeSpan.FromSeconds(3.0)),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever,
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut }
            };

            _glowActive = new DoubleAnimation
            {
                From = 0.98,
                To = 1.05,
                Duration = new Duration(TimeSpan.FromSeconds(1.6)),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever,
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut }
            };

            _waveOff = new DoubleAnimation { To = 0, Duration = new Duration(TimeSpan.FromMilliseconds(180)) };

            _waveSpeaking = new DoubleAnimation
            {
                From = 0.98,
                To = 1.10,
                Duration = new Duration(TimeSpan.FromSeconds(0.9)),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever,
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut }
            };

            _waveProcessing = new DoubleAnimation
            {
                From = 0.98,
                To = 1.06,
                Duration = new Duration(TimeSpan.FromSeconds(0.5)),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever
            };
        }

        // ===== Ring storyboard runner =====
        private void StartRing(string key)
        {
            _ringSb?.Stop(this);

            if (Resources[key] is Storyboard sb)
            {
                _ringSb = sb;

                if (key == "SB_Ring_Success")
                {
                    sb.Completed -= RingSuccessCompleted;
                    sb.Completed += RingSuccessCompleted;
                }
                else
                {
                    sb.Completed -= RingSuccessCompleted;
                }

                sb.Begin(this, true);
            }
        }

        private void RingSuccessCompleted(object? sender, EventArgs e)
        {
            if (sender is Storyboard sb) sb.Completed -= RingSuccessCompleted;
            StartRing("SB_Ring_Idle");
        }

        private void ApplyState(AidyState state)
        {
            // stop previous (wave/rotate/glow)
            RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, null);
            OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
            OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);
            WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
            WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);

            // ===== Ring storyboard by state =====
            switch (state)
            {
                case AidyState.Starting:
                    StartRing("SB_Ring_Idle");
                    break;
                case AidyState.Idle:
                    StartRing("SB_Ring_Idle");
                    break;
                case AidyState.Listening:
                    StartRing("SB_Ring_Listening");
                    break;
                case AidyState.Processing:
                    StartRing("SB_Ring_Processing");
                    break;
                case AidyState.Speaking:
                    StartRing("SB_Ring_Speaking");
                    break;

                case AidyState.Executing:
                    StartRing("SB_Ring_Executing");
                    break;
                case AidyState.Success:
                    StartRing("SB_Ring_Success");
                    break;
                case AidyState.Warning:
                    StartRing("SB_Ring_Warning");
                    break;
                case AidyState.Error:
                    StartRing("SB_Ring_Error");
                    break;
                case AidyState.Offline:
                    StartRing("SB_Ring_Offline");
                    break;

                default:
                    StartRing("SB_Ring_Idle");
                    break;
            }

            // ===== Wave / OuterGlow / Rotate behavior =====
            switch (state)
            {
                case AidyState.Starting:
                    Wave.BeginAnimation(OpacityProperty, _waveOff);
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                    break;
                case AidyState.Idle:
                    Wave.BeginAnimation(OpacityProperty, _waveOff);
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                    break;

                case AidyState.Listening:
                    Wave.Opacity = 1;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;

                case AidyState.Processing:
                    Wave.Opacity = 1;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateFast);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;

                case AidyState.Speaking:
                    Wave.Opacity = 1;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveSpeaking);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveSpeaking);
                    break;

                case AidyState.Executing:
                    Wave.Opacity = 1;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateFast);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;

                case AidyState.Success:
                    // Python держит SUCCESS ~0.18s и вернёт IDLE
                    Wave.BeginAnimation(OpacityProperty, _waveOff);
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                    break;

                case AidyState.Warning:
                    Wave.Opacity = 1;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;

                case AidyState.Error:
                case AidyState.Offline:
                    Wave.BeginAnimation(OpacityProperty, _waveOff);
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                    break;

                default:
                    Wave.BeginAnimation(OpacityProperty, _waveOff);
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                    break;
            }
        }

        // =========================
        // WINDOW CHROME
        // =========================
        private void TitleBar_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            if (e.ClickCount == 2) { ToggleMaximize(); return; }
            if (e.ButtonState == MouseButtonState.Pressed) DragMove();
        }

        private void Close_Click(object sender, RoutedEventArgs e) => Close();
        private void Minimize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
        private void Maximize_Click(object sender, RoutedEventArgs e) => ToggleMaximize();

        // =========================
        // NAVIGATION (Sidebar)
        // =========================
        private void NavAidy_Click(object sender, RoutedEventArgs e) => ShowPage("AIDY");
        private void NavCommands_Click(object sender, RoutedEventArgs e) => ShowPage("COMMANDS");
        private void NavContacts_Click(object sender, RoutedEventArgs e) => ShowPage("CONTACTS");
        private void NavSettings_Click(object sender, RoutedEventArgs e) => ShowPage("SETTINGS");

        private void ShowPage(string page)
        {
            // pages (x:Name должны совпадать с XAML)
            AidyPage.Visibility = (page == "AIDY") ? Visibility.Visible : Visibility.Collapsed;
            CommandsPage.Visibility = (page == "COMMANDS") ? Visibility.Visible : Visibility.Collapsed;
            ContactsPage.Visibility = (page == "CONTACTS") ? Visibility.Visible : Visibility.Collapsed;
            SettingsPage.Visibility = (page == "SETTINGS") ? Visibility.Visible : Visibility.Collapsed;

            // highlight active button
            SetActiveMenuButton(BtnAidy, page == "AIDY");
            SetActiveMenuButton(BtnCommands, page == "COMMANDS");
            SetActiveMenuButton(BtnContacts, page == "CONTACTS");
            SetActiveMenuButton(BtnSettings, page == "SETTINGS");
        }

        private void SetActiveMenuButton(Button btn, bool active)
        {
            if (active)
            {
                btn.Background = new SolidColorBrush((Color)ColorConverter.ConvertFromString("#8C2B3C8A"));
                btn.BorderBrush = new SolidColorBrush((Color)ColorConverter.ConvertFromString("#2EA9C7FF"));
                btn.BorderThickness = new Thickness(1);
            }
            else
            {
                // вернём управление стилю MenuItemButton
                btn.ClearValue(BackgroundProperty);
                btn.ClearValue(BorderBrushProperty);
                btn.ClearValue(BorderThicknessProperty);
            }
        }

        private void ToggleMaximize()
            => WindowState = (WindowState == WindowState.Maximized) ? WindowState.Normal : WindowState.Maximized;
    }
}
