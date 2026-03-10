// WpfApp1/Views/MainWindow.xaml.cs
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using Microsoft.Win32;
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
        private readonly AppConfigService _configService;
        private readonly AudioDeviceService _audioDeviceService;
        private readonly GlobalHotkeyService _pushToTalkHotkey;

        private DoubleAnimation _rotateSlow = null!;
        private DoubleAnimation _rotateFast = null!;
        private DoubleAnimation _glowIdle = null!;
        private DoubleAnimation _glowActive = null!;
        private DoubleAnimation _waveOff = null!;
        private DoubleAnimation _waveSpeaking = null!;
        private DoubleAnimation _waveProcessing = null!;
        private readonly DispatcherTimer _waveformTimer;
        private readonly DispatcherTimer _studyTimerUiTimer;
        private readonly Random _waveformRandom = new();
        private bool _waveformAnimating;
        private int _studyTimerTotalSeconds;
        private double _studyTimerRemainingPreciseSec;
        private DateTime _studyTimerLastFrameUtc = DateTime.UtcNow;
        private readonly SolidColorBrush _studyTimerArcBrush = new(Color.FromRgb(0x57, 0xF2, 0x87));
        private const int WM_NCHITTEST = 0x0084;
        private const int HTTRANSPARENT = -1;
        private bool _isCompactMode;
        private string _pageBeforeCompact = "AIDY";
        private readonly double _normalShellWidth = 1413;
        private readonly double _normalShellHeight = 743;
        private readonly double _compactShellWidth = 340;
        private readonly double _compactShellHeight = 385;
        private readonly double _normalDesignWidth = 1536;
        private readonly double _normalDesignHeight = 864;
        private readonly double _compactDesignWidth = 390;
        private readonly double _compactDesignHeight = 460;
        private readonly double _normalWindowMinWidth = 900;
        private readonly double _normalWindowMinHeight = 560;
        private readonly double _compactWindowMinWidth = 330;
        private readonly double _compactWindowMinHeight = 380;
        private readonly double _compactWindowWidth = 390;
        private readonly double _compactWindowHeight = 460;
        private double _restoreWindowWidth;
        private double _restoreWindowHeight;
        private double _restoreWindowLeft;
        private double _restoreWindowTop;
        private bool _restoreWindowWasMaximized;
        private readonly GridLength _normalSidebarWidth = new(290);
        private readonly GridLength _normalTitleRowHeight = new(64);
        private readonly GridLength _compactTitleRowHeight = new(46);
        private readonly Thickness _normalOrbMargin = new(0, 140, 0, 0);
        private readonly Thickness _compactOrbMargin = new(0, -20, 0, 0);
        private readonly double _normalOrbScale = 1.0;
        private readonly double _compactOrbScale = 0.45;
        private bool _bridgeStarted;
        private bool _isPushToTalkPressed;
        private bool _isApplyingAutoStartSetting;
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
        private double _smoothScrollTargetOffset;
        public static readonly DependencyProperty CurrentScrollOffsetProperty =
            DependencyProperty.Register(
                nameof(CurrentScrollOffset),
                typeof(double),
                typeof(MainWindow),
                new PropertyMetadata(0d, OnCurrentScrollOffsetChanged));

        public double CurrentScrollOffset
        {
            get => (double)GetValue(CurrentScrollOffsetProperty);
            set => SetValue(CurrentScrollOffsetProperty, value);
        }

        private static void OnCurrentScrollOffsetChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
        {
            if (d is MainWindow window && window.SettingsScrollViewerElement is not null)
            {
                window.SettingsScrollViewerElement.ScrollToVerticalOffset((double)e.NewValue);
            }
        }

        public MainWindow()
        {
            InitializeComponent();
            SourceInitialized += (_, __) => AttachOuterAreaClickThrough();
            _restoreWindowWidth = Width;
            _restoreWindowHeight = Height;
            _restoreWindowLeft = Left;
            _restoreWindowTop = Top;
            _restoreWindowWasMaximized = false;

            _vm = new MainViewModel();
            DataContext = _vm;

            var baseDir = AppDomain.CurrentDomain.BaseDirectory;
            var scriptPath = ResolvePythonScriptPath(baseDir);
            _audioDeviceService = new AudioDeviceService();
            _configService = new AppConfigService(Path.Combine(baseDir, "config.json"));
            var appConfig = _configService.Load();
            var syncedAutoStartEnabled = SyncAutoStartWithConfig(appConfig.AutoStartEnabled);
            var inputDevices = _audioDeviceService.GetInputDevices();
            var outputDevices = _audioDeviceService.GetOutputDevices();

            _vm.ApplyPushToTalkConfig(appConfig.PushToTalkEnabled, appConfig.PushToTalkKey);
            _vm.AutoStartEnabled = syncedAutoStartEnabled;
            _vm.SetAudioDevices(inputDevices, outputDevices, appConfig.Audio.Microphone, appConfig.Audio.OutputDevice);
            _vm.GreetingOnStartupEnabled = appConfig.Startup.GreetingEnabled;
            _vm.AidiFilePath = appConfig.Aidi.FilePath;
            _vm.AidiVolume = appConfig.Aidi.Volume;
            _vm.VoiceIdEnabled = appConfig.VoiceIdEnabled;

            appConfig.AutoStartEnabled = syncedAutoStartEnabled;
            appConfig.Audio.Microphone = _vm.SelectedMicrophoneDevice;
            appConfig.Audio.OutputDevice = _vm.SelectedOutputDevice;
            appConfig.Startup.GreetingEnabled = _vm.GreetingOnStartupEnabled;
            appConfig.Aidi.FilePath = _vm.AidiFilePath;
            appConfig.Aidi.Volume = _vm.AidiVolume;
            SafeSaveConfig(appConfig);

            _bridge = new PythonBridge(
                pythonExe: "python",
                scriptPath: scriptPath,
                workingDir: baseDir
            );
            _pushToTalkHotkey = new GlobalHotkeyService();
            _pushToTalkHotkey.HotkeyDown += OnPushToTalkHotkeyDown;
            _pushToTalkHotkey.HotkeyUp += OnPushToTalkHotkeyUp;
            PreviewKeyDown += MainWindowOnPreviewKeyDown;

            _vm.PropertyChanged += VmOnPropertyChanged;

            _waveformTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(70)
            };
            _waveformTimer.Tick += WaveformTimerOnTick;
            _studyTimerUiTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(33),
            };
            _studyTimerUiTimer.Tick += StudyTimerUiTimerOnTick;

            _bridge.StateChanged += s => Dispatcher.Invoke(() => { _vm.CurrentState = s; Console.WriteLine($"[UI] State changed to {s}"); });
            // Logs are intentionally hidden from UI.
            // _bridge.LogLine += line => Dispatcher.Invoke(() => AppendBridgeLogLine(line));

            // Show last command, but hide internal/system keywords (exit, etc.)
            _bridge.CommandHeard += t => Dispatcher.Invoke(() =>
            {
                _vm.LastCommand = FormatUserFacingCommand(t);
            });
            _bridge.TimerChanged += (eventName, remaining, total) => Dispatcher.Invoke(() =>
            {
                var e = (eventName ?? "").Trim().ToLowerInvariant();
                UpdateTimerBadge(e, remaining, total);
            });
            _bridge.StudyModeChanged += active => Dispatcher.Invoke(() =>
            {
                StudyTipsPanel.Visibility = active ? Visibility.Visible : Visibility.Collapsed;
            });

            Loaded += (_, __) =>
            {
                // --- ЗАПУСК ЗАСТАВКИ ---
                var videoPath = Path.Combine(baseDir, "Assets", "SplashOverlay.mp4");
                if (File.Exists(videoPath))
                {
                    SplashVideo.Source = new Uri(videoPath, UriKind.Absolute);
                    SplashVideo.Play();
                }
                else
                {
                    // ВЫВОДИМ ОШИБКУ:
                    MessageBox.Show($"Видео не найдено!\nПуть, где искала программа:\n{videoPath}", "Ошибка заставки");
                    SplashOverlay.Visibility = Visibility.Collapsed;
                }
                // -----------------------

                ShowPage("AIDY");
                BuildAnimations();
                _vm.CurrentState = AidyState.Starting;
                ApplyState(_vm.CurrentState);
                _bridge.Start();
                _bridgeStarted = true;
                _pushToTalkHotkey.Start();
                ApplyPushToTalkSettings(persistConfig: false);
                _bridge.SendControlCommand($"set_volume:{_vm.AidiVolume}");
            };

            Closing += (_, __) =>
            {
                try
                {
                    _pushToTalkHotkey.Dispose();
                    _bridge.Dispose();
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"Ошибка при закрытии: {ex.Message}");
                }
                finally
                {
                    // Гарантированно убиваем процесс со всеми его потоками
                    Environment.Exit(0);
                }
            };
        }

        // =========================
        // ЛОГИКА ЗАСТАВКИ
        // =========================
        private void SplashVideo_MediaEnded(object sender, RoutedEventArgs e)
        {
            // Создаем плавное затухание (Fade-out эффект) на 400 миллисекунд
            var fadeOut = new DoubleAnimation
            {
                From = 1.0,
                To = 0.0,
                Duration = TimeSpan.FromMilliseconds(400)
            };

            fadeOut.Completed += (s, args) =>
            {
                SplashOverlay.Visibility = Visibility.Collapsed; // Убираем Grid полностью
                SplashVideo.Close(); // Освобождаем ресурсы плеера
            };

            SplashOverlay.BeginAnimation(UIElement.OpacityProperty, fadeOut);
        }

        private void StudyTimerUiTimerOnTick(object? sender, EventArgs e)
        {
            if (_studyTimerTotalSeconds <= 0 || _studyTimerRemainingPreciseSec <= 0)
            {
                _studyTimerUiTimer.Stop();
                return;
            }

            var now = DateTime.UtcNow;
            var delta = (now - _studyTimerLastFrameUtc).TotalSeconds;
            if (delta < 0)
            {
                delta = 0;
            }
            _studyTimerLastFrameUtc = now;
            _studyTimerRemainingPreciseSec = Math.Max(0.0, _studyTimerRemainingPreciseSec - delta);
            RenderStudyTimer(_studyTimerRemainingPreciseSec, _studyTimerTotalSeconds);

            if (_studyTimerRemainingPreciseSec <= 0.0)
            {
                _studyTimerUiTimer.Stop();
            }
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
            {
                RefreshPushToTalkWaitingState();
                ApplyState(_vm.CurrentState);
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.PushToTalkEnabled) ||
                e.PropertyName == nameof(MainViewModel.PushToTalkKey))
            {
                ApplyPushToTalkSettings(persistConfig: true);
                RefreshPushToTalkWaitingState();
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.AutoStartEnabled))
            {
                ApplyAutoStartSettings(persistConfig: true);
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.SelectedMicrophoneDevice) ||
                e.PropertyName == nameof(MainViewModel.SelectedOutputDevice) ||
                e.PropertyName == nameof(MainViewModel.GreetingOnStartupEnabled) ||
                e.PropertyName == nameof(MainViewModel.AidiFilePath))
            {
                SaveCurrentConfig();
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.AidiVolume))
            {
                SaveCurrentConfig();
                if (_bridgeStarted)
                {
                    _bridge.SendControlCommand($"set_volume:{_vm.AidiVolume}");
                }
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.VoiceIdEnabled))
            {
                SaveCurrentConfig();
                if (_bridgeStarted)
                {
                    _bridge.SendControlCommand($"set_voice_id:{(_vm.VoiceIdEnabled ? "1" : "0")}");
                }
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.IsPushToTalkKeyCaptureActive))
            {
                ApplyPushToTalkSettings(persistConfig: false);
                RefreshPushToTalkWaitingState();
            }
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

        private void ChangePushToTalkKey_Click(object sender, RoutedEventArgs e)
        {
            if (!_vm.PushToTalkEnabled)
            {
                return;
            }

            _vm.IsPushToTalkKeyCaptureActive = !_vm.IsPushToTalkKeyCaptureActive;
        }

        private void EnrollAdminVoice_Click(object sender, RoutedEventArgs e)
        {
            if (!_bridgeStarted)
            {
                return;
            }

            _bridge.SendControlCommand("enroll_admin");
        }

        private void BrowseAidiFile_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = "Select AIDI File",
                CheckFileExists = true,
                CheckPathExists = true,
                DereferenceLinks = true,
                Multiselect = false,
                Filter = "All files (*.*)|*.*"
            };

            if (dialog.ShowDialog(this) == true)
            {
                _vm.AidiFilePath = dialog.FileName;
            }
        }

        private void MainWindowOnPreviewKeyDown(object sender, KeyEventArgs e)
        {
            if (!_vm.IsPushToTalkKeyCaptureActive)
            {
                return;
            }

            var key = e.Key == Key.System ? e.SystemKey : e.Key;
            if (key == Key.Escape)
            {
                _vm.IsPushToTalkKeyCaptureActive = false;
                e.Handled = true;
                return;
            }

            if (_vm.TrySetPushToTalkKey(key))
            {
                _vm.IsPushToTalkKeyCaptureActive = false;
                e.Handled = true;
            }
        }

        private void OnPushToTalkHotkeyDown()
        {
            if (!_bridgeStarted || !_vm.PushToTalkEnabled || _vm.IsPushToTalkKeyCaptureActive)
            {
                return;
            }

            Dispatcher.BeginInvoke(() =>
            {
                _isPushToTalkPressed = true;
                RefreshPushToTalkWaitingState();
                _bridge.SendControlCommand("start_listening");
            });
        }

        private void OnPushToTalkHotkeyUp()
        {
            if (!_bridgeStarted || !_vm.PushToTalkEnabled)
            {
                return;
            }

            Dispatcher.BeginInvoke(() =>
            {
                _isPushToTalkPressed = false;
                RefreshPushToTalkWaitingState();
                _bridge.SendControlCommand("stop_listening");
            });
        }

        private void ApplyPushToTalkSettings(bool persistConfig)
        {
            var hotkey = ParsePushToTalkKey(_vm.PushToTalkKey);
            _pushToTalkHotkey.UpdateKey(hotkey);
            _pushToTalkHotkey.Enabled = _vm.PushToTalkEnabled && !_vm.IsPushToTalkKeyCaptureActive;
            if (!_vm.PushToTalkEnabled)
            {
                _isPushToTalkPressed = false;
            }

            if (persistConfig)
            {
                SaveCurrentConfig();
            }

            if (!_bridgeStarted)
            {
                return;
            }

            _bridge.SendControlCommand($"set_push_to_talk:{(_vm.PushToTalkEnabled ? "1" : "0")}:{hotkey}");
            if (_vm.PushToTalkEnabled)
            {
                _bridge.SendControlCommand("stop_listening");
            }

            RefreshPushToTalkWaitingState();
        }

        private void ApplyAutoStartSettings(bool persistConfig)
        {
            if (_isApplyingAutoStartSetting)
            {
                return;
            }

            var desired = _vm.AutoStartEnabled;
            var actual = desired;
            try
            {
                actual = AutoStart.SetEnabled(desired);
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[AutoStart] apply failed: {ex}");
                actual = AutoStart.IsEnabled();
            }

            if (actual != desired)
            {
                _isApplyingAutoStartSetting = true;
                try
                {
                    _vm.AutoStartEnabled = actual;
                }
                finally
                {
                    _isApplyingAutoStartSetting = false;
                }
            }

            if (persistConfig)
            {
                SaveCurrentConfig();
            }
        }

        private bool SyncAutoStartWithConfig(bool configuredEnabled)
        {
            try
            {
                return AutoStart.SetEnabled(configuredEnabled);
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[AutoStart] sync failed: {ex}");
                return AutoStart.IsEnabled();
            }
        }

        private void SaveCurrentConfig()
        {
            var hotkey = ParsePushToTalkKey(_vm.PushToTalkKey);
            var normalizedAidiPath = NormalizeAbsolutePath(_vm.AidiFilePath);
            SafeSaveConfig(new AppConfig
            {
                PushToTalkEnabled = _vm.PushToTalkEnabled,
                PushToTalkKey = hotkey.ToString(),
                AutoStartEnabled = _vm.AutoStartEnabled,
                VoiceIdEnabled = _vm.VoiceIdEnabled,
                Audio = new AudioConfig
                {
                    Microphone = _vm.SelectedMicrophoneDevice,
                    OutputDevice = _vm.SelectedOutputDevice,
                },
                Startup = new StartupConfig
                {
                    GreetingEnabled = _vm.GreetingOnStartupEnabled,
                },
                Aidi = new AidiConfig
                {
                    FilePath = normalizedAidiPath,
                    Volume = _vm.AidiVolume,
                },
            });
        }

        private void SafeSaveConfig(AppConfig config)
        {
            try
            {
                _configService.Save(config);
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[Config] save failed: {ex}");
            }
        }

        private static Key ParsePushToTalkKey(string? raw)
        {
            var normalized = MainViewModel.NormalizePushToTalkKeyName(raw);
            if (Enum.TryParse<Key>(normalized, ignoreCase: true, out var key) &&
                key != Key.None &&
                key != Key.System)
            {
                return key;
            }

            return Key.LeftCtrl;
        }

        private static string NormalizeAbsolutePath(string? rawPath)
        {
            if (string.IsNullOrWhiteSpace(rawPath))
            {
                return string.Empty;
            }

            try
            {
                return Path.GetFullPath(rawPath.Trim());
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string ResolvePythonScriptPath(string baseDir)
        {
            var primary = Path.Combine(baseDir, "PythonCore", "main.py");
            if (File.Exists(primary))
            {
                return primary;
            }

            // Dev-time fallback when content files were not copied to output yet.
            var fallback = Path.GetFullPath(Path.Combine(baseDir, "..", "..", "..", "PythonCore", "main.py"));
            if (File.Exists(fallback))
            {
                return fallback;
            }

            return primary;
        }

        private void RefreshPushToTalkWaitingState()
        {
            _vm.IsWaitingForHotkey =
                _vm.PushToTalkEnabled &&
                !_vm.IsPushToTalkKeyCaptureActive &&
                !_isPushToTalkPressed &&
                _vm.CurrentState == AidyState.Idle;
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

        private static string FormatTimerText(int seconds)
        {
            var s = Math.Max(0, seconds);
            var ts = TimeSpan.FromSeconds(s);
            if (ts.TotalHours >= 1)
            {
                return $"{(int)ts.TotalHours:00}:{ts.Minutes:00}:{ts.Seconds:00}";
            }
            return $"{ts.Minutes:00}:{ts.Seconds:00}";
        }

        private static Point CirclePoint(double centerX, double centerY, double radius, double angleDeg)
        {
            var rad = angleDeg * Math.PI / 180.0;
            return new Point(centerX + radius * Math.Cos(rad), centerY + radius * Math.Sin(rad));
        }

        private static Geometry BuildTimerArcGeometry(double ratio)
        {
            var r = Math.Clamp(ratio, 0.0, 1.0);
            if (r <= 0.0)
            {
                return Geometry.Empty;
            }

            // ArcSegment cannot draw a true 360-degree arc in one segment.
            if (r >= 1.0)
            {
                r = 0.9999;
            }

            const double size = 132.0;
            const double stroke = 9.0;
            var radius = (size - stroke) / 2.0;
            var cx = size / 2.0;
            var cy = size / 2.0;
            var start = CirclePoint(cx, cy, radius, -90.0);
            var sweep = 360.0 * r;
            var end = CirclePoint(cx, cy, radius, -90.0 + sweep);

            var figure = new PathFigure
            {
                StartPoint = start,
                IsClosed = false,
                IsFilled = false,
            };
            figure.Segments.Add(
                new ArcSegment
                {
                    Point = end,
                    Size = new Size(radius, radius),
                    SweepDirection = SweepDirection.Clockwise,
                    IsLargeArc = sweep >= 180.0,
                }
            );
            return new PathGeometry(new[] { figure });
        }

        private static Color LerpColor(Color from, Color to, double t)
        {
            var k = Math.Clamp(t, 0.0, 1.0);
            byte ch(byte a, byte b) => (byte)(a + (b - a) * k);
            return Color.FromRgb(
                ch(from.R, to.R),
                ch(from.G, to.G),
                ch(from.B, to.B)
            );
        }

        private static Color TimerColorForRatio(double ratio)
        {
            var r = Math.Clamp(ratio, 0.0, 1.0);
            var green = Color.FromRgb(0x57, 0xF2, 0x87);
            var amber = Color.FromRgb(0xFF, 0xC2, 0x4D);
            var red = Color.FromRgb(0xFF, 0x63, 0x6E);

            if (r >= 0.5)
            {
                var t = (1.0 - r) / 0.5;
                return LerpColor(green, amber, t);
            }
            else
            {
                var t = (0.5 - r) / 0.5;
                return LerpColor(amber, red, t);
            }
        }

        private void RenderStudyTimer(double remainingSec, int total)
        {
            var safeTotal = Math.Max(1, total);
            var safeRemaining = Math.Max(0.0, remainingSec);
            var ratio = safeRemaining / safeTotal;
            var shownSeconds = Math.Max(0, (int)Math.Ceiling(safeRemaining));

            StudyTimerText.Text = FormatTimerText(shownSeconds);
            StudyTimerArc.Data = BuildTimerArcGeometry(ratio);
            _studyTimerArcBrush.Color = TimerColorForRatio(ratio);
            StudyTimerArc.Stroke = _studyTimerArcBrush;
            StudyTimerWidget.Visibility = Visibility.Visible;
        }

        private void UpdateTimerBadge(string eventName, int remaining, int total)
        {
            var e = (eventName ?? "").Trim().ToLowerInvariant();
            if (e == "start")
            {
                _studyTimerTotalSeconds = Math.Max(1, (total > 0 ? total : remaining));
                _vm.TimerBadgeText = FormatTimerText(_studyTimerTotalSeconds);
                _studyTimerRemainingPreciseSec = _studyTimerTotalSeconds;
                _studyTimerLastFrameUtc = DateTime.UtcNow;
                RenderStudyTimer(_studyTimerRemainingPreciseSec, _studyTimerTotalSeconds);
                if (!_studyTimerUiTimer.IsEnabled)
                {
                    _studyTimerUiTimer.Start();
                }
                return;
            }
            if (e == "tick")
            {
                if (total > 0)
                {
                    _studyTimerTotalSeconds = Math.Max(1, total);
                }
                var effectiveTotal = Math.Max(1, _studyTimerTotalSeconds);
                _vm.TimerBadgeText = FormatTimerText(remaining);
                _studyTimerRemainingPreciseSec = Math.Max(0.0, remaining);
                _studyTimerLastFrameUtc = DateTime.UtcNow;
                RenderStudyTimer(_studyTimerRemainingPreciseSec, effectiveTotal);
                if (!_studyTimerUiTimer.IsEnabled)
                {
                    _studyTimerUiTimer.Start();
                }
                return;
            }
            if (e is "stop" or "done")
            {
                _vm.TimerBadgeText = "";
                _studyTimerTotalSeconds = 0;
                _studyTimerRemainingPreciseSec = 0.0;
                _studyTimerUiTimer.Stop();
                StudyTimerArc.Data = Geometry.Empty;
                StudyTimerWidget.Visibility = Visibility.Collapsed;
            }
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
            WaitingHotkeySleep.Visibility = Visibility.Collapsed;
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
                    if (_vm.IsWaitingForHotkey)
                    {
                        Wave.Opacity = 1;
                        Wave.Visibility = Visibility.Collapsed;
                        WaitingHotkeySleep.Visibility = Visibility.Visible;
                        RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                        OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                        OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                        WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                        WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    }
                    else
                    {
                        Wave.BeginAnimation(OpacityProperty, _waveOff);
                        RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                        OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowIdle);
                        OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowIdle);
                    }
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

                _restoreWindowWasMaximized = WindowState == WindowState.Maximized;
                if (_restoreWindowWasMaximized)
                {
                    WindowState = WindowState.Normal;
                }

                _restoreWindowWidth = Width;
                _restoreWindowHeight = Height;
                _restoreWindowLeft = Left;
                _restoreWindowTop = Top;

                SidebarPanel.Visibility = Visibility.Collapsed;
                SidebarColumn.Width = new GridLength(0);
                MainColumn.Width = new GridLength(1, GridUnitType.Star);

                AppDesignRoot.Width = _compactDesignWidth;
                AppDesignRoot.Height = _compactDesignHeight;
                MainShell.Width = _compactShellWidth;
                MainShell.Height = _compactShellHeight;
                TitleBarRow.Height = _compactTitleRowHeight;

                MinWidth = _compactWindowMinWidth;
                MinHeight = _compactWindowMinHeight;

                var targetWidth = Math.Max(_compactWindowWidth, MinWidth);
                var targetHeight = Math.Max(_compactWindowHeight, MinHeight);
                var safeLeft = double.IsNaN(_restoreWindowLeft) ? Left : _restoreWindowLeft;
                var safeTop = double.IsNaN(_restoreWindowTop) ? Top : _restoreWindowTop;
                var centerX = safeLeft + (_restoreWindowWidth / 2.0);
                var centerY = safeTop + (_restoreWindowHeight / 2.0);

                Width = targetWidth;
                Height = targetHeight;
                Left = centerX - (targetWidth / 2.0);
                Top = centerY - (targetHeight / 2.0);

                AidyHeader.Visibility = Visibility.Collapsed;
                CompactStateHost.Visibility = Visibility.Visible;
                OrbHost.VerticalAlignment = VerticalAlignment.Center;
                OrbHost.Margin = _compactOrbMargin;
                OrbScale.ScaleX = _compactOrbScale;
                OrbScale.ScaleY = _compactOrbScale;

                MinimizeButton.Visibility = Visibility.Visible;
                MaximizeButton.Visibility = Visibility.Collapsed;
                MaximizeToCloseSpacer.Visibility = Visibility.Collapsed;

                CompactIconInward.Visibility = Visibility.Collapsed;
                CompactIconOutward.Visibility = Visibility.Visible;
                return;
            }

            SidebarPanel.Visibility = Visibility.Visible;
            SidebarColumn.Width = _normalSidebarWidth;
            MainColumn.Width = new GridLength(1, GridUnitType.Star);

            AppDesignRoot.Width = _normalDesignWidth;
            AppDesignRoot.Height = _normalDesignHeight;
            MainShell.Width = _normalShellWidth;
            MainShell.Height = _normalShellHeight;
            TitleBarRow.Height = _normalTitleRowHeight;

            MinWidth = _normalWindowMinWidth;
            MinHeight = _normalWindowMinHeight;

            Width = Math.Max(_restoreWindowWidth, MinWidth);
            Height = Math.Max(_restoreWindowHeight, MinHeight);
            if (!double.IsNaN(_restoreWindowLeft))
            {
                Left = _restoreWindowLeft;
            }

            if (!double.IsNaN(_restoreWindowTop))
            {
                Top = _restoreWindowTop;
            }

            AidyHeader.Visibility = Visibility.Visible;
            CompactStateHost.Visibility = Visibility.Collapsed;
            OrbHost.VerticalAlignment = VerticalAlignment.Top;
            OrbHost.Margin = _normalOrbMargin;
            OrbScale.ScaleX = _normalOrbScale;
            OrbScale.ScaleY = _normalOrbScale;

            MinimizeButton.Visibility = Visibility.Visible;
            MaximizeButton.Visibility = Visibility.Visible;
            MaximizeToCloseSpacer.Visibility = Visibility.Visible;

            CompactIconInward.Visibility = Visibility.Visible;
            CompactIconOutward.Visibility = Visibility.Collapsed;

            ShowPage(_pageBeforeCompact);
            if (_restoreWindowWasMaximized)
            {
                WindowState = WindowState.Maximized;
            }
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

        private void Close_Click(object sender, RoutedEventArgs e)
        {
            // 1. Мгновенно прячем окно (оно пропадет с экрана и не будет мозолить глаза)
            this.Hide();

            // 2. Убиваем Питон и всё остальное в фоновом режиме, чтобы не вешать интерфейс
            System.Threading.Tasks.Task.Run(() =>
            {
                try
                {
                    _pushToTalkHotkey?.Dispose();
                    _bridge?.Dispose();
                }
                catch { }
                finally
                {
                    // 3. Жестко завершаем процесс в Windows
                    Environment.Exit(0);
                }
            });
        }
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

        // =========================
        // ПЛАВНАЯ ПРОКРУТКА НАСТРОЕК
        // =========================
        private void SettingsScrollViewer_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
        {
            if (sender is not ScrollViewer scv)
            {
                return;
            }

            e.Handled = true;

            // Precision touchpad дает частые мелкие delta (<120), мышь обычно кратна 120.
            var isTouchpadLikeInput = Math.Abs(e.Delta) < 120;

            const double touchpadPixelsPerDeltaUnit = 1.2;
            const double mousePixelsPerDeltaUnit = 1.2;

            if (_smoothScrollTargetOffset < 0d || _smoothScrollTargetOffset > scv.ScrollableHeight)
            {
                _smoothScrollTargetOffset = scv.VerticalOffset;
            }

            var pixelsPerDeltaUnit = isTouchpadLikeInput
                ? touchpadPixelsPerDeltaUnit
                : mousePixelsPerDeltaUnit;
            var deltaPixels = -(e.Delta * pixelsPerDeltaUnit);
            _smoothScrollTargetOffset = Math.Clamp(
                _smoothScrollTargetOffset + deltaPixels,
                0d,
                scv.ScrollableHeight);

            // Для тачпада не анимируем: иначе из-за частых событий появляется "вязкость"/подлагивание.
            if (isTouchpadLikeInput)
            {
                BeginAnimation(CurrentScrollOffsetProperty, null);
                CurrentScrollOffset = _smoothScrollTargetOffset;
                scv.ScrollToVerticalOffset(_smoothScrollTargetOffset);
                return;
            }

            CurrentScrollOffset = scv.VerticalOffset;

            var animation = new DoubleAnimation
            {
                To = _smoothScrollTargetOffset,
                Duration = TimeSpan.FromMilliseconds(140),
                EasingFunction = new QuarticEase { EasingMode = EasingMode.EaseOut }
            };

            animation.Completed += (_, __) => CurrentScrollOffset = _smoothScrollTargetOffset;
            BeginAnimation(CurrentScrollOffsetProperty, animation, HandoffBehavior.SnapshotAndReplace);
        }

    }
}
