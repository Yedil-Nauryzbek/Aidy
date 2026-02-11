// WpfApp1/Views/MainWindow.xaml.cs
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Navigation;
using System.Windows.Threading;
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
        private readonly DispatcherTimer _waveformTimer;
        private readonly Random _waveformRandom = new();
        private bool _waveformAnimating;
        private const int WM_NCHITTEST = 0x0084;
        private const int HTTRANSPARENT = -1;
        private bool _isCompactMode;
        private string _pageBeforeCompact = "AIDY";
        private readonly double _normalShellWidth = 1413;
        private readonly double _normalShellHeight = 743;
        private readonly double _compactShellWidth = 460;
        private readonly double _compactShellHeight = 520;
        private readonly GridLength _normalSidebarWidth = new(290);
        private readonly GridLength _normalTitleRowHeight = new(64);
        private readonly GridLength _compactTitleRowHeight = new(56);
        private readonly Thickness _normalOrbMargin = new(0, 140, 0, 0);
        private readonly Thickness _compactOrbMargin = new(-24, 10, 0, 0);
        private readonly double _normalOrbScale = 1.0;
        private readonly double _compactOrbScale = 0.78;
        private static readonly string[] WakeDebugMarkers =
        {
            "[WAKE]",
            "[CMD]",
            "Wake:",
            "Wake Heard:",
            "Wake detected",
            "Command: listening",
            "Heard:",
            "VAD:",
            "audio stream",
            "Failed to start audio stream",
            "API connection error",
            "STATE:LISTENING",
            "STATE:COMMAND_LISTENING",
            "STATE:WARNING",
            "STATE:ERROR",
        };

        // ===== Ring storyboard controller =====
        private Storyboard? _ringSb;

        public MainWindow()
        {
            InitializeComponent();
            SourceInitialized += (_, __) => AttachOuterAreaClickThrough();

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

            _waveformTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(70)
            };
            _waveformTimer.Tick += WaveformTimerOnTick;

            _bridge.StateChanged += s => Dispatcher.Invoke(() => { _vm.CurrentState = s; Console.WriteLine($"[UI] State changed to {s}"); });
            // Logs are intentionally hidden from UI.
            // _bridge.LogLine += line => Dispatcher.Invoke(() => AppendBridgeLogLine(line));

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

        private void AttachOuterAreaClickThrough()
        {
            if (PresentationSource.FromVisual(this) is HwndSource source)
            {
                source.AddHook(WndProc);
            }
        }

        private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (msg != WM_NCHITTEST)
            {
                return IntPtr.Zero;
            }

            if (MainShell.ActualWidth <= 0 || MainShell.ActualHeight <= 0)
            {
                return IntPtr.Zero;
            }

            var screenX = (short)((long)lParam & 0xFFFF);
            var screenY = (short)(((long)lParam >> 16) & 0xFFFF);
            var windowPoint = PointFromScreen(new Point(screenX, screenY));

            Rect shellBounds;
            try
            {
                shellBounds = MainShell.TransformToAncestor(this).TransformBounds(
                    new Rect(0, 0, MainShell.ActualWidth, MainShell.ActualHeight)
                );
            }
            catch
            {
                return IntPtr.Zero;
            }

            if (!shellBounds.Contains(windowPoint))
            {
                handled = true;
                return new IntPtr(HTTRANSPARENT);
            }

            return IntPtr.Zero;
        }

        private void WaveformTimerOnTick(object? sender, EventArgs e)
        {
            if (SpeakingWave.Visibility == Visibility.Visible)
            {
                AnimateBarHeights(
                    SpeakingBars,
                    edgeMinHeight: 10,
                    edgeMaxHeight: 50,
                    centerMinHeight: 24,
                    centerMaxHeight: 88,
                    blend: 0.68);
            }

            if (FollowUpWave.Visibility == Visibility.Visible)
            {
                AnimateBarHeights(
                    FollowUpBars,
                    edgeMinHeight: 10,
                    edgeMaxHeight: 46,
                    centerMinHeight: 22,
                    centerMaxHeight: 82,
                    blend: 0.64);
            }

            if (SpeakingWave.Visibility != Visibility.Visible && FollowUpWave.Visibility != Visibility.Visible)
            {
                StopWaveformAnimation();
            }
        }

        private void AnimateBarHeights(
            Panel barsPanel,
            double edgeMinHeight,
            double edgeMaxHeight,
            double centerMinHeight,
            double centerMaxHeight,
            double blend)
        {
            var count = barsPanel.Children.Count;
            if (count == 0) return;

            var mid = (count - 1) / 2.0;

            for (var i = 0; i < count; i++)
            {
                if (barsPanel.Children[i] is not Border bar) continue;

                var distNorm = mid <= 0 ? 0 : Math.Abs(i - mid) / mid;
                var centerFactor = 1.0 - distNorm;

                var minHeight = edgeMinHeight + (centerMinHeight - edgeMinHeight) * centerFactor;
                var maxHeight = edgeMaxHeight + (centerMaxHeight - edgeMaxHeight) * centerFactor;

                var span = Math.Max(1, maxHeight - minHeight);
                var target = minHeight + _waveformRandom.NextDouble() * span;
                target += (_waveformRandom.NextDouble() - 0.5) * span * 0.22;
                target = Math.Clamp(target, minHeight, maxHeight);

                bar.Height += (target - bar.Height) * blend;
            }
        }

        private void StartWaveformAnimation()
        {
            if (_waveformAnimating) return;
            _waveformAnimating = true;
            _waveformTimer.Start();
        }

        private void StopWaveformAnimation()
        {
            if (!_waveformAnimating) return;
            _waveformAnimating = false;
            _waveformTimer.Stop();
        }

        private void VmOnPropertyChanged(object? sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(MainViewModel.CurrentState))
                ApplyState(_vm.CurrentState);
        }

        private void AppendBridgeLogLine(string line)
        {
            _vm.AppendLogLine(line);
            if (IsWakeDebugLine(line))
            {
                _vm.AppendWakeDebugLine(line);
            }
        }

        private static bool IsWakeDebugLine(string? line)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                return false;
            }

            foreach (var marker in WakeDebugMarkers)
            {
                if (line.IndexOf(marker, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return true;
                }
            }

            return false;
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
            if (s == "confirm") return true;

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
            Wave.Visibility = Visibility.Visible;
            ListeningMic.Visibility = Visibility.Collapsed;
            SpeakingWave.Visibility = Visibility.Collapsed;
            FollowUpWave.Visibility = Visibility.Collapsed;
            StopWaveformAnimation();

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
                case AidyState.CommandListening:
                    StartRing("SB_Ring_Listening");
                    break;
                case AidyState.Processing:
                    StartRing("SB_Ring_Processing");
                    break;
                case AidyState.Speaking:
                    StartRing("SB_Ring_Speaking");
                    break;
                case AidyState.Confirming:
                    StartRing("SB_Ring_Warning");
                    break;
                case AidyState.FollowUp:
                    StartRing("SB_Ring_FollowUp");
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
                case AidyState.CommandListening:
                    Wave.Opacity = 1;
                    Wave.Visibility = Visibility.Collapsed;
                    ListeningMic.Visibility = Visibility.Visible;
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
                    SpeakingWave.Visibility = Visibility.Visible;
                    StartWaveformAnimation();
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveSpeaking);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveSpeaking);
                    break;

                case AidyState.Confirming:
                    Wave.Opacity = 1;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;
                case AidyState.FollowUp:
                    Wave.Opacity = 1;
                    FollowUpWave.Visibility = Visibility.Visible;
                    StartWaveformAnimation();
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
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
        private void CompactToggle_Click(object sender, RoutedEventArgs e)
        {
            SetCompactMode(!_isCompactMode);
        }

        private string GetCurrentPage()
        {
            if (CommandsPage.Visibility == Visibility.Visible) return "COMMANDS";
            if (ContactsPage.Visibility == Visibility.Visible) return "CONTACTS";
            if (SettingsPage.Visibility == Visibility.Visible) return "SETTINGS";
            return "AIDY";
        }

        private void SetCompactMode(bool enabled)
        {
            _isCompactMode = enabled;

            if (enabled)
            {
                _pageBeforeCompact = GetCurrentPage();
                ShowPage("AIDY");

                if (WindowState == WindowState.Maximized)
                {
                    WindowState = WindowState.Normal;
                }

                SidebarPanel.Visibility = Visibility.Collapsed;
                SidebarColumn.Width = new GridLength(0);
                MainColumn.Width = new GridLength(1, GridUnitType.Star);

                MainShell.Width = _compactShellWidth;
                MainShell.Height = _compactShellHeight;
                TitleBarRow.Height = _compactTitleRowHeight;

                AidyHeader.Visibility = Visibility.Collapsed;
                CompactStateHost.Visibility = Visibility.Visible;
                OrbHost.VerticalAlignment = VerticalAlignment.Center;
                OrbHost.Margin = _compactOrbMargin;
                OrbScale.ScaleX = _compactOrbScale;
                OrbScale.ScaleY = _compactOrbScale;

                MinimizeButton.Visibility = Visibility.Visible;
                MaximizeButton.Visibility = Visibility.Visible;
                CloseButton.Visibility = Visibility.Visible;
                CompactToWindowsSpacer.Visibility = Visibility.Visible;
                MinimizeToMaximizeSpacer.Visibility = Visibility.Visible;
                MaximizeToCloseSpacer.Visibility = Visibility.Visible;

                CompactIconInward.Visibility = Visibility.Collapsed;
                CompactIconOutward.Visibility = Visibility.Visible;
                return;
            }

            SidebarPanel.Visibility = Visibility.Visible;
            SidebarColumn.Width = _normalSidebarWidth;
            MainColumn.Width = new GridLength(1, GridUnitType.Star);

            MainShell.Width = _normalShellWidth;
            MainShell.Height = _normalShellHeight;
            TitleBarRow.Height = _normalTitleRowHeight;

            AidyHeader.Visibility = Visibility.Visible;
            CompactStateHost.Visibility = Visibility.Collapsed;
            OrbHost.VerticalAlignment = VerticalAlignment.Top;
            OrbHost.Margin = _normalOrbMargin;
            OrbScale.ScaleX = _normalOrbScale;
            OrbScale.ScaleY = _normalOrbScale;

            MinimizeButton.Visibility = Visibility.Visible;
            MaximizeButton.Visibility = Visibility.Visible;
            CloseButton.Visibility = Visibility.Visible;
            CompactToWindowsSpacer.Visibility = Visibility.Visible;
            MinimizeToMaximizeSpacer.Visibility = Visibility.Visible;
            MaximizeToCloseSpacer.Visibility = Visibility.Visible;

            CompactIconInward.Visibility = Visibility.Visible;
            CompactIconOutward.Visibility = Visibility.Collapsed;

            ShowPage(_pageBeforeCompact);
        }

        private void TitleBar_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            if (_isCompactMode)
            {
                if (e.ButtonState == MouseButtonState.Pressed) DragMove();
                return;
            }
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
        {
            if (_isCompactMode) return;
            WindowState = (WindowState == WindowState.Maximized) ? WindowState.Normal : WindowState.Maximized;
        }

        private void ClearWakeDebug_Click(object sender, RoutedEventArgs e)
        {
            _vm.ClearWakeDebugLog();
        }

        private void WakeDebugTextBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (sender is TextBox tb)
            {
                tb.ScrollToEnd();
            }
        }
    }
}
