// WpfApp1/Views/MainWindow.xaml.cs
using System.DirectoryServices.AccountManagement;
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Microsoft.Win32;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Navigation;
using System.Windows.Threading;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Nodes;
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

        // Display name → apps.json id mapping for preferred app ComboBoxes
        private readonly Dictionary<string, string> _browserDisplayToId = new();
        private readonly Dictionary<string, string> _musicDisplayToId = new();
        private const string SystemDefaultLabel = "System Default";

        private DoubleAnimation _rotateSlow = null!;
        private DoubleAnimation _rotateFast = null!;
        private DoubleAnimation _glowIdle = null!;
        private DoubleAnimation _glowActive = null!;
        private DoubleAnimation _glowCommandListening = null!;
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
        private const int WM_DEVICECHANGE = 0x0219;
        private const int DBT_DEVNODES_CHANGED = 0x0007;
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
        private readonly double _compactOrbScale = 0.58;
        private bool _bridgeStarted;
        private bool _isPushToTalkPressed;
        private bool _isApplyingAutoStartSetting;
        private bool _isEnrollmentActive;
        private bool _enrollmentConfirmedByPython;
        private DispatcherTimer? _enrollmentTimeoutTimer;
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
        private Storyboard? _emblemSb;
        private bool _emblemVisible = false;
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
            _vm.AidiFilePath = string.IsNullOrWhiteSpace(appConfig.Aidi.FilePath)
                ? GetProjectRootDirectory(baseDir)
                : appConfig.Aidi.FilePath;
            _vm.AidiVolume = appConfig.Aidi.Volume;
            _vm.VoiceIdEnabled = appConfig.VoiceIdEnabled;
            _vm.CustomModeEnabled = appConfig.CustomMode.Enabled;
            var savedSlots = appConfig.CustomMode.Slots;
            // Load base 3 slots
            for (int i = 0; i < Math.Min(savedSlots.Length, _vm.CustomModeSlots.Count); i++)
                _vm.CustomModeSlots[i].Apply(savedSlots[i]);
            // Add extra saved slots (4th, 5th) if they exist and have content
            for (int i = _vm.CustomModeSlots.Count; i < Math.Min(savedSlots.Length, MainViewModel.CustomModeSlotsMax); i++)
            {
                if (!string.IsNullOrEmpty(savedSlots[i].Target))
                {
                    var extra = new CustomModeSlotViewModel(i);
                    extra.Apply(savedSlots[i]);
                    _vm.CustomModeSlots.Add(extra);
                }
            }
            _vm.RefreshCanAddCustomSlot();

            _vm.VadThreshold = appConfig.VoiceSensitivity.VadThreshold;
            _vm.SilenceMs = appConfig.VoiceSensitivity.SilenceMs;
            _vm.MinSpeechMs = appConfig.VoiceSensitivity.MinSpeechMs;
            _vm.MouseMovePx = appConfig.MouseControl.MovePx;
            _vm.MouseScrollClicks = appConfig.MouseControl.ScrollClicks;
            _vm.MouseScrollStep = appConfig.MouseControl.ScrollStep;

            PopulatePreferredAppOptions(baseDir);
            _vm.PreferredBrowser = ResolvePreferredSelection(_vm.BrowserOptions, _browserDisplayToId, appConfig.PreferredApps.Browser);
            _vm.PreferredMusicApp = ResolvePreferredSelection(_vm.MusicAppOptions, _musicDisplayToId, appConfig.PreferredApps.MusicApp);

            appConfig.AutoStartEnabled = syncedAutoStartEnabled;
            appConfig.Audio.Microphone = _vm.SelectedMicrophoneDevice;
            appConfig.Audio.OutputDevice = _vm.SelectedOutputDevice;
            appConfig.Startup.GreetingEnabled = _vm.GreetingOnStartupEnabled;
            appConfig.Aidi.FilePath = _vm.AidiFilePath;
            appConfig.Aidi.Volume = _vm.AidiVolume;
            SafeSaveConfig(appConfig);

            // Prefer bundled embedded Python; fall back to system "python"
            var bundledPython = System.IO.Path.Combine(baseDir, "python-embed", "python.exe");
            var pythonExe = System.IO.File.Exists(bundledPython) ? bundledPython : "python";

            _bridge = new PythonBridge(
                pythonExe: pythonExe,
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
                Interval = TimeSpan.FromMilliseconds(120)
            };
            _waveformTimer.Tick += WaveformTimerOnTick;
            _studyTimerUiTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(66),
            };
            _studyTimerUiTimer.Tick += StudyTimerUiTimerOnTick;

            // PERF: Use BeginInvoke (async dispatch) instead of Invoke (sync) to
            // decouple the PumpStreamAsync I/O tasks from UI thread latency.
            // Invoke blocks the reader thread until the UI completes, which
            // back-pressures Python's stdout and stalls the voice recognition loop.
            _bridge.StateChanged += s => Dispatcher.BeginInvoke(() => { _vm.CurrentState = s; Console.WriteLine($"[UI] State changed to {s}"); });
            // Logs are intentionally hidden from UI.
            // _bridge.LogLine += line => Dispatcher.BeginInvoke(() => AppendBridgeLogLine(line));

            // Show last command, but hide internal/system keywords (exit, etc.)
            _bridge.CommandHeard += t => Dispatcher.BeginInvoke(() =>
            {
                _vm.LastCommand = FormatUserFacingCommand(t);
            });
            _bridge.TimerChanged += (eventName, remaining, total) => Dispatcher.BeginInvoke(() =>
            {
                var e = (eventName ?? "").Trim().ToLowerInvariant();
                UpdateTimerBadge(e, remaining, total);
            });
            _bridge.StudyModeChanged += active => Dispatcher.BeginInvoke(() =>
            {
                StudyTipsPanel.Visibility = active ? Visibility.Visible : Visibility.Collapsed;
            });
            _bridge.CustomModeChanged += active => Dispatcher.BeginInvoke(() =>
            {
                Debug.WriteLine($"[CustomMode] Bridge reports custom mode changed: {active}");
                _vm.AppendLogLine($"[CustomMode] Bridge event: custom mode = {active}");
                // Slots are already executed by Python directly — do NOT re-send
                // execute_slot commands back, as that causes double execution.
            });
            _bridge.VoiceActivityChanged += active => Dispatcher.BeginInvoke(() => SetEmblemActive(active));
            _bridge.EnrollmentFinished += () => Dispatcher.BeginInvoke(() =>
            {
                // Python explicitly signalled the end of enrollment — safe to close the overlay.
                _enrollmentTimeoutTimer?.Stop();
                _isEnrollmentActive = false;
                _enrollmentConfirmedByPython = false;
                EnrollmentOverlay.Visibility = Visibility.Collapsed;
            });
            _bridge.EnrollmentStarted += () => Dispatcher.BeginInvoke(() =>
            {
                // Python acknowledged the enrollment — cancel button is now safe to use.
                Debug.WriteLine("[Enroll] Python confirmed ENROLL_STARTED.");
                _enrollmentTimeoutTimer?.Stop();
                _enrollmentConfirmedByPython = true;
            });
            _bridge.EnrollmentTextChanged += (status, progress) => Dispatcher.BeginInvoke(() =>
            {
                EnrollmentStatusText.Text = status;
                EnrollmentProgressText.Text = progress;
            });
            _bridge.VoiceUsersChanged += (entries) => Dispatcher.BeginInvoke(() =>
            {
                _vm.UpdateVoiceUsers(entries);
            });

            Loaded += (_, __) =>
            {
                // --- ЗАПУСК ЗАСТАВКИ ---
                if (_vm.GreetingOnStartupEnabled)
                {
                    var videoPath = Path.Combine(baseDir, "Assets", "SplashOverlay.mp4");
                    if (File.Exists(videoPath))
                    {
                        SplashVideo.Source = new Uri(videoPath, UriKind.Absolute);
                        SplashVideo.Play();
                    }
                    else
                    {
                        SplashOverlay.Visibility = Visibility.Collapsed;
                    }
                }
                else
                {
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
                _bridge.SendControlCommand($"set_voice_id:{(_vm.VoiceIdEnabled ? "1" : "0")}");
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
            if (msg == WM_DEVICECHANGE && (int)wParam == DBT_DEVNODES_CHANGED)
            {
                RefreshAudioDevices();
                return IntPtr.Zero;
            }

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
                    blend: 0.82);
            }

            if (FollowUpWave.Visibility == Visibility.Visible)
            {
                AnimateBarHeights(
                    FollowUpBars,
                    edgeMinHeight: 10,
                    edgeMaxHeight: 46,
                    centerMinHeight: 22,
                    centerMaxHeight: 82,
                    blend: 0.78);
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
                // NOTE: overlay lifecycle is now driven entirely by _isEnrollmentActive and the
                // EnrollmentFinished bridge event. Do NOT hide the overlay here based on Idle/Success —
                // those states are emitted between TTS prompts and would close the overlay too early.
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

            if (e.PropertyName == nameof(MainViewModel.CustomModeEnabled))
            {
                Debug.WriteLine($"[CustomMode] Toggle changed: enabled={_vm.CustomModeEnabled}");
                _vm.AppendLogLine($"[CustomMode] Toggle changed: enabled={_vm.CustomModeEnabled}");
                SaveCurrentConfig();
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

            if (e.PropertyName == nameof(MainViewModel.VadThreshold) ||
                e.PropertyName == nameof(MainViewModel.SilenceMs) ||
                e.PropertyName == nameof(MainViewModel.MinSpeechMs) ||
                e.PropertyName == nameof(MainViewModel.MouseMovePx) ||
                e.PropertyName == nameof(MainViewModel.MouseScrollClicks) ||
                e.PropertyName == nameof(MainViewModel.MouseScrollStep))
            {
                SaveCurrentConfig();
                if (_bridgeStarted)
                {
                    _bridge.SendControlCommand("reload_config");
                }
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
                    if (_vm.VoiceIdEnabled)
                        _bridge.SendControlCommand("list_voice_users");
                }
                return;
            }

            if (e.PropertyName == nameof(MainViewModel.PreferredBrowser) ||
                e.PropertyName == nameof(MainViewModel.PreferredMusicApp))
            {
                SaveCurrentConfig();
                if (_bridgeStarted)
                {
                    _bridge.SendControlCommand("reload_config");
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

        private void ResetVoiceSensitivity_Click(object sender, RoutedEventArgs e)
        {
            _vm.ResetVoiceSensitivity();
        }

        private void ResetMouseControl_Click(object sender, RoutedEventArgs e)
        {
            _vm.ResetMouseControl();
        }

        private const int MaxVoiceProfiles = 5;

        // ── Shared dialog helpers ─────────────────────────────────────────
        private static readonly SolidColorBrush _dialogBg =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#111827"));
        private static readonly SolidColorBrush _dialogCardBg =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#1E293B"));
        private static readonly SolidColorBrush _accentBlue =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#3B82F6"));
        private static readonly SolidColorBrush _accentBlueHover =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#2563EB"));
        private static readonly SolidColorBrush _subtleText =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#94A3B8"));
        private static readonly SolidColorBrush _inputBg =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#0F172A"));
        private static readonly SolidColorBrush _inputBorder =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#334155"));
        private static readonly SolidColorBrush _cancelRed =
            new SolidColorBrush((Color)ColorConverter.ConvertFromString("#EF4444"));

        /// <summary>Creates a styled modern button for enrollment dialogs.</summary>
        private static Button MakeDialogButton(string text, SolidColorBrush bg, double width = double.NaN, double height = 44)
        {
            var border = new Border
            {
                Background = bg,
                CornerRadius = new CornerRadius(10),
                Padding = new Thickness(0),
                Child = new TextBlock
                {
                    Text = text,
                    Foreground = Brushes.White,
                    FontSize = 14,
                    FontWeight = FontWeights.SemiBold,
                    HorizontalAlignment = HorizontalAlignment.Center,
                    VerticalAlignment = VerticalAlignment.Center
                }
            };

            var btn = new Button
            {
                Content = border,
                Background = Brushes.Transparent,
                BorderThickness = new Thickness(0),
                Cursor = Cursors.Hand,
                Height = height,
                Padding = new Thickness(0),
            };
            if (!double.IsNaN(width)) btn.Width = width;

            // Remove chrome so only the inner Border is visible
            btn.Template = new ControlTemplate(typeof(Button))
            {
                VisualTree = CreateButtonTemplateTree()
            };

            return btn;
        }

        private static FrameworkElementFactory CreateButtonTemplateTree()
        {
            var cp = new FrameworkElementFactory(typeof(ContentPresenter));
            cp.SetValue(ContentPresenter.HorizontalAlignmentProperty, HorizontalAlignment.Stretch);
            cp.SetValue(ContentPresenter.VerticalAlignmentProperty, VerticalAlignment.Stretch);
            return cp;
        }

        /// <summary>Creates a borderless modern dialog window.</summary>
        private static Window MakeDialogWindow(string title, double width, UIElement content)
        {
            return new Window
            {
                Title = title,
                Width = width,
                SizeToContent = SizeToContent.Height,
                WindowStartupLocation = WindowStartupLocation.CenterScreen,
                Topmost = true,
                Background = _dialogBg,
                Content = content,
                AllowsTransparency = false,
                WindowStyle = WindowStyle.SingleBorderWindow,
                ResizeMode = ResizeMode.NoResize,
            };
        }

        // ── Enrollment flow ───────────────────────────────────────────────

        private void EnrollAdminVoice_Click(object sender, RoutedEventArgs e)
        {
            if (!_bridgeStarted || _isEnrollmentActive) return;

            // ─── STEP 1: Password Authentication ──────────────────────────
            if (!ShowPasswordDialog()) return;

            // ─── STEP 2: Overwrite or Add ─────────────────────────────────
            bool? actionResult = ShowActionSelectionDialog();
            if (actionResult == null) return; // cancelled

            string selectedRole;
            string selectedLabel;

            if (actionResult == true)
            {
                // "Overwrite Existing" — re-enroll as Admin, replacing the current profile
                selectedRole = "Admin";
                selectedLabel = "";

                // НАХОДИМ И УДАЛЯЕМ старые профили админа, чтобы действительно перезаписать
                var existingAdmins = _vm.VoiceUsers.Where(u => u.Role.Equals("Admin", StringComparison.OrdinalIgnoreCase)).ToList();
                foreach (var admin in existingAdmins)
                {
                    // Добавляем флаг :force, чтобы Питон разрешил удалить единственного админа
                    _bridge.SendControlCommand($"delete_voice_user:{admin.Id}:force");
                }
            }
            else
            {
                // "Add New Voice" — STEP 3: Profile Configuration
                int currentCount = _vm.VoiceUsers.Count;
                if (currentCount >= MaxVoiceProfiles)
                {
                    MessageBox.Show(
                        $"Maximum of {MaxVoiceProfiles} voice profiles reached.\nPlease delete an existing profile first.",
                        "Profile Limit Reached",
                        MessageBoxButton.OK,
                        MessageBoxImage.Warning);
                    return;
                }

                var profileResult = ShowProfileConfigDialog(currentCount);
                if (profileResult == null) return; // cancelled
                selectedRole = profileResult.Value.role;
                selectedLabel = profileResult.Value.label;
            }

            // ─── Start recording ──────────────────────────────────────────
            StartEnrollmentRecording(selectedRole, selectedLabel);
        }

        // ── Step 1: Password ──────────────────────────────────────────────

        private bool ShowPasswordDialog()
        {
            string username = Environment.UserName;

            var titleText = new TextBlock
            {
                Text = "Authentication Required",
                FontSize = 20,
                FontWeight = FontWeights.Bold,
                Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 4)
            };

            var subtitleText = new TextBlock
            {
                Text = "Verify your identity to manage voice profiles",
                FontSize = 12,
                Foreground = _subtleText,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 24)
            };

            var userLabel = new TextBlock
            {
                Text = $"Windows User: {username}",
                FontSize = 12,
                Foreground = _subtleText,
                Margin = new Thickness(0, 0, 0, 8)
            };

            var passwordBox = new PasswordBox
            {
                Height = 42,
                FontSize = 15,
                Background = _inputBg,
                Foreground = Brushes.White,
                BorderBrush = _inputBorder,
                BorderThickness = new Thickness(1),
                Padding = new Thickness(12, 8, 12, 8),
                Margin = new Thickness(0, 0, 0, 20),
            };

            var verifyBtn = MakeDialogButton("Verify Identity", _accentBlue);

            var stack = new StackPanel { Margin = new Thickness(32, 28, 32, 28) };
            stack.Children.Add(titleText);
            stack.Children.Add(subtitleText);
            stack.Children.Add(userLabel);
            stack.Children.Add(passwordBox);
            stack.Children.Add(verifyBtn);

            var dialog = MakeDialogWindow("Aidy Security", 420, stack);

            bool isVerified = false;
            verifyBtn.Click += (s, args) =>
            {
                try
                {
                    using (var context = new PrincipalContext(ContextType.Machine))
                    {
                        isVerified = context.ValidateCredentials(username, passwordBox.Password);
                    }
                }
                catch { isVerified = false; }

                if (!isVerified)
                {
                    MessageBox.Show("Incorrect password. Please try again.",
                        "Authentication Failed", MessageBoxButton.OK, MessageBoxImage.Error);
                    return;
                }
                dialog.DialogResult = true;
                dialog.Close();
            };

            return dialog.ShowDialog() == true && isVerified;
        }

        // ── Step 2: Overwrite or Add ──────────────────────────────────────

        /// <returns>true = overwrite, false = add new, null = cancelled</returns>
        private bool? ShowActionSelectionDialog()
        {
            var titleText = new TextBlock
            {
                Text = "Voice Enrollment",
                FontSize = 22,
                FontWeight = FontWeights.Bold,
                Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 4)
            };

            var subtitleText = new TextBlock
            {
                Text = "Choose how to set up voice recognition",
                FontSize = 13,
                Foreground = _subtleText,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 28)
            };

            // ── Overwrite card ────────────────────────────────────────────
            var overwriteIcon = new TextBlock
            {
                Text = "\U0001F504",   // 🔄
                FontSize = 28,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 10)
            };
            var overwriteTitle = new TextBlock
            {
                Text = "Overwrite Existing",
                FontSize = 15,
                FontWeight = FontWeights.SemiBold,
                Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 6)
            };
            var overwriteDesc = new TextBlock
            {
                Text = "Replace the current admin\nvoice profile with a new one",
                FontSize = 11,
                Foreground = _subtleText,
                TextAlignment = TextAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Center,
                LineHeight = 16
            };
            var overwriteStack = new StackPanel
            {
                VerticalAlignment = VerticalAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Center
            };
            overwriteStack.Children.Add(overwriteIcon);
            overwriteStack.Children.Add(overwriteTitle);
            overwriteStack.Children.Add(overwriteDesc);

            var overwriteCard = new Border
            {
                Background = _dialogCardBg,
                BorderBrush = _inputBorder,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(14),
                Padding = new Thickness(20, 28, 20, 28),
                Cursor = Cursors.Hand,
                Child = overwriteStack,
                Width = 195
            };

            // ── Add New card ──────────────────────────────────────────────
            var addIcon = new TextBlock
            {
                Text = "\u2795",   // ➕
                FontSize = 28,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 10)
            };
            var addTitle = new TextBlock
            {
                Text = "Add New Voice",
                FontSize = 15,
                FontWeight = FontWeights.SemiBold,
                Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 6)
            };
            var addDesc = new TextBlock
            {
                Text = $"Register a new user profile\n({_vm.VoiceUsers.Count}/{MaxVoiceProfiles} slots used)",
                FontSize = 11,
                Foreground = _subtleText,
                TextAlignment = TextAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Center,
                LineHeight = 16
            };
            var addStack = new StackPanel
            {
                VerticalAlignment = VerticalAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Center
            };
            addStack.Children.Add(addIcon);
            addStack.Children.Add(addTitle);
            addStack.Children.Add(addDesc);

            var addCard = new Border
            {
                Background = _dialogCardBg,
                BorderBrush = _inputBorder,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(14),
                Padding = new Thickness(20, 28, 20, 28),
                Cursor = Cursors.Hand,
                Child = addStack,
                Width = 195
            };

            // Arrange the two cards side by side
            var cardsPanel = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Center
            };
            cardsPanel.Children.Add(overwriteCard);
            cardsPanel.Children.Add(new Border { Width = 16 }); // spacer
            cardsPanel.Children.Add(addCard);

            var outerStack = new StackPanel { Margin = new Thickness(28, 28, 28, 28) };
            outerStack.Children.Add(titleText);
            outerStack.Children.Add(subtitleText);
            outerStack.Children.Add(cardsPanel);

            var dialog = MakeDialogWindow("Aidy - Voice Enrollment", 480, outerStack);
            bool? result = null;

            // Hover effects
            overwriteCard.MouseEnter += (s, a) => overwriteCard.BorderBrush = _accentBlue;
            overwriteCard.MouseLeave += (s, a) => overwriteCard.BorderBrush = _inputBorder;
            addCard.MouseEnter += (s, a) => addCard.BorderBrush = _accentBlue;
            addCard.MouseLeave += (s, a) => addCard.BorderBrush = _inputBorder;

            overwriteCard.MouseLeftButtonUp += (s, a) =>
            {
                result = true; // overwrite
                dialog.DialogResult = true;
                dialog.Close();
            };

            addCard.MouseLeftButtonUp += (s, a) =>
            {
                result = false; // add new
                dialog.DialogResult = true;
                dialog.Close();
            };

            dialog.ShowDialog();
            return result;
        }

        // ── Step 3: Profile Configuration ─────────────────────────────────

        /// <returns>Tuple of (role, label) or null if cancelled.</returns>
        private (string role, string label)? ShowProfileConfigDialog(int currentCount)
        {
            var titleText = new TextBlock
            {
                Text = "New Voice Profile",
                FontSize = 20,
                FontWeight = FontWeights.Bold,
                Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 4)
            };
            var subtitleText = new TextBlock
            {
                Text = $"Configure the new profile ({currentCount}/{MaxVoiceProfiles} used)",
                FontSize = 12,
                Foreground = _subtleText,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 0, 0, 24)
            };

            // ── Role radio buttons ────────────────────────────────────────
            // ── Role radio buttons ────────────────────────────────────────
            var roleLabel = new TextBlock
            {
                Text = "ROLE",
                FontSize = 11,
                FontWeight = FontWeights.SemiBold,
                Foreground = _subtleText,
                Margin = new Thickness(0, 0, 0, 8),
                // LetterSpacing = 0.5,  <-- Удали или закомментируй эту строку
            };

            var radioUser = new RadioButton
            {
                Content = "  User  —  standard access",
                IsChecked = true,
                Foreground = Brushes.White,
                FontSize = 13,
                Margin = new Thickness(0, 0, 0, 6),
                GroupName = "Role"
            };
            var radioAdmin = new RadioButton
            {
                Content = "  Admin  —  full access",
                Foreground = Brushes.White,
                FontSize = 13,
                Margin = new Thickness(0, 0, 0, 6),
                GroupName = "Role"
            };
            var radioGuest = new RadioButton
            {
                Content = "  Guest  —  limited access",
                Foreground = Brushes.White,
                FontSize = 13,
                Margin = new Thickness(0, 0, 0, 0),
                GroupName = "Role"
            };

            var roleCard = new Border
            {
                Background = _dialogCardBg,
                CornerRadius = new CornerRadius(10),
                Padding = new Thickness(16, 14, 16, 14),
                Margin = new Thickness(0, 0, 0, 18),
            };
            var roleStack = new StackPanel();
            roleStack.Children.Add(radioUser);
            roleStack.Children.Add(radioAdmin);
            roleStack.Children.Add(radioGuest);
            roleCard.Child = roleStack;

            // ── Profile label ─────────────────────────────────────────────
            // ── Profile label ─────────────────────────────────────────────
            var labelTitle = new TextBlock
            {
                Text = "PROFILE LABEL",
                FontSize = 11,
                FontWeight = FontWeights.SemiBold,
                Foreground = _subtleText,
                Margin = new Thickness(0, 0, 0, 8),
                // LetterSpacing = 0.5,  <-- Удали или закомментируй эту строку
            };
            var labelBox = new TextBox
            {
                Height = 42,
                FontSize = 14,
                Background = _inputBg,
                Foreground = Brushes.White,
                BorderBrush = _inputBorder,
                BorderThickness = new Thickness(1),
                Padding = new Thickness(12, 8, 12, 8),
                Margin = new Thickness(0, 0, 0, 24),
                Text = "",
            };
            // Placeholder via GotFocus / LostFocus
            string placeholder = "e.g. Yasin, User 2, Mom...";
            labelBox.Text = placeholder;
            labelBox.Foreground = _subtleText;
            labelBox.GotFocus += (s, a) =>
            {
                if (labelBox.Text == placeholder)
                {
                    labelBox.Text = "";
                    labelBox.Foreground = Brushes.White;
                }
            };
            labelBox.LostFocus += (s, a) =>
            {
                if (string.IsNullOrWhiteSpace(labelBox.Text))
                {
                    labelBox.Text = placeholder;
                    labelBox.Foreground = _subtleText;
                }
            };

            // ── Buttons ──────────────────────────────────────────────────
            var startBtn = MakeDialogButton("Start Recording", _accentBlue, double.NaN, 44);
            var cancelBtn = MakeDialogButton("Cancel", new SolidColorBrush((Color)ColorConverter.ConvertFromString("#374151")), double.NaN, 44);

            var btnRow = new Grid { Margin = new Thickness(0) };
            btnRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            btnRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(12) }); // spacer
            btnRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetColumn(cancelBtn, 0);
            Grid.SetColumn(startBtn, 2);
            btnRow.Children.Add(cancelBtn);
            btnRow.Children.Add(startBtn);

            // ── Assemble ──────────────────────────────────────────────────
            var stack = new StackPanel { Margin = new Thickness(32, 28, 32, 28) };
            stack.Children.Add(titleText);
            stack.Children.Add(subtitleText);
            stack.Children.Add(roleLabel);
            stack.Children.Add(roleCard);
            stack.Children.Add(labelTitle);
            stack.Children.Add(labelBox);
            stack.Children.Add(btnRow);

            var dialog = MakeDialogWindow("Aidy - Profile Setup", 420, stack);

            (string role, string label)? result = null;
            startBtn.Click += (s, a) =>
            {
                string role = radioAdmin.IsChecked == true ? "Admin"
                            : radioGuest.IsChecked == true ? "Guest"
                            : "User";
                string label = (labelBox.Text == placeholder) ? "" : labelBox.Text.Trim();
                result = (role, label);
                dialog.DialogResult = true;
                dialog.Close();
            };
            cancelBtn.Click += (s, a) =>
            {
                dialog.DialogResult = false;
                dialog.Close();
            };

            dialog.ShowDialog();
            return result;
        }

        // ── Kick off recording ────────────────────────────────────────────

        private void StartEnrollmentRecording(string role, string label)
        {
            _isEnrollmentActive = true;
            _enrollmentConfirmedByPython = false;

            EnrollmentOverlay.Visibility = Visibility.Visible;
            EnrollmentStatusText.Text = "Aidy is preparing...";
            EnrollmentProgressText.Text = "";

            string enrollCommand = string.IsNullOrEmpty(label)
                ? $"enroll_user_force:{role}:"
                : $"enroll_user_force:{role}:{label}";
            Debug.WriteLine($"[Enroll] Sending control command: {enrollCommand}");
            var sent = _bridge.SendControlCommand(enrollCommand);
            if (!sent)
            {
                Debug.WriteLine("[Enroll] SendControlCommand failed — will retry in 3s");
            }

            // Timeout / retry timer
            int enrollRetries = 0;
            _enrollmentTimeoutTimer?.Stop();
            _enrollmentTimeoutTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(sent ? 15 : 3) };
            _enrollmentTimeoutTimer.Tick += (_, __) =>
            {
                if (_enrollmentConfirmedByPython || !_isEnrollmentActive)
                {
                    _enrollmentTimeoutTimer?.Stop();
                    return;
                }

                enrollRetries++;
                if (enrollRetries <= 2)
                {
                    Debug.WriteLine($"[Enroll] Timeout — retrying {enrollCommand} (attempt {enrollRetries})");
                    EnrollmentStatusText.Text = "Aidy is preparing... (retrying)";
                    _bridge.SendControlCommand(enrollCommand);
                    _enrollmentTimeoutTimer!.Interval = TimeSpan.FromSeconds(10);
                }
                else
                {
                    Debug.WriteLine("[Enroll] Timeout — giving up after retries");
                    _enrollmentTimeoutTimer?.Stop();
                    _isEnrollmentActive = false;
                    EnrollmentOverlay.Visibility = Visibility.Collapsed;
                    MessageBox.Show(
                        "Voice enrollment timed out. Please make sure Aidy is running and try again.",
                        "Enrollment Failed",
                        MessageBoxButton.OK,
                        MessageBoxImage.Warning);
                }
            };
            _enrollmentTimeoutTimer.Start();

            // Safety timeout (90s)
            var safetyTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(90) };
            safetyTimer.Tick += (_, __) =>
            {
                safetyTimer.Stop();
                if (_isEnrollmentActive)
                {
                    Debug.WriteLine("[Enroll] Safety timeout (90s) — force-closing overlay.");
                    _enrollmentTimeoutTimer?.Stop();
                    _isEnrollmentActive = false;
                    _enrollmentConfirmedByPython = false;
                    EnrollmentOverlay.Visibility = Visibility.Collapsed;
                }
            };
            safetyTimer.Start();
        }

        private void CancelEnrollment_Click(object sender, RoutedEventArgs e)
        {
            Debug.WriteLine($"[Enroll] CancelEnrollment_Click: confirmed={_enrollmentConfirmedByPython}, active={_isEnrollmentActive}");

            if (!_isEnrollmentActive)
            {
                Debug.WriteLine("[Enroll] Cancel ignored: enrollment not active.");
                return;
            }

            // User cancelled manually — clear flag and hide immediately.
            _isEnrollmentActive = false;
            EnrollmentOverlay.Visibility = Visibility.Collapsed;

            // Only send the cancel command if Python has acknowledged the enrollment.
            // This prevents a race where cancel_enrollment arrives before Python even starts.
            if (_enrollmentConfirmedByPython)
            {
                _enrollmentConfirmedByPython = false;
                Debug.WriteLine("[Enroll] Sending cancel_enrollment (Python had confirmed start).");
                _bridge.SendControlCommand("cancel_enrollment");
            }
            else
            {
                Debug.WriteLine("[Enroll] Cancel suppressed: Python has not confirmed ENROLL_STARTED yet.");
            }
        }

        private void DeleteVoiceUser_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button btn && btn.Tag is int userId)
            {
                var entry = _vm.VoiceUsers.FirstOrDefault(u => u.Id == userId);
                string label = entry?.Label ?? $"ID {userId}";

                var result = MessageBox.Show(
                    $"Delete voice profile '{label}'?",
                    "Confirm Deletion",
                    MessageBoxButton.YesNo,
                    MessageBoxImage.Warning);

                if (result != MessageBoxResult.Yes) return;

                Debug.WriteLine($"[Voice] Deleting voice user id={userId}");
                _bridge.SendControlCommand($"delete_voice_user:{userId}");
            }
        }

        private void CustomModeSlot_Click(object sender, RoutedEventArgs e)
        {
            Debug.WriteLine("[CustomMode] CustomModeSlot_Click fired");
            _vm.AppendLogLine("[CustomMode] Slot assign click");

            if (sender is not Button { Tag: int idx }) return;
            if (idx < 0 || idx >= _vm.CustomModeSlots.Count) return;

            _vm.AppendLogLine($"[CustomMode] Opening picker for slot {idx}");
            var picker = new CustomModePickerWindow(idx) { Owner = this };
            if (picker.ShowDialog() == true && picker.Result is { } result)
            {
                var slot = _vm.CustomModeSlots[idx];
                slot.ActionType  = result.ActionType;
                slot.Target      = result.Target;
                slot.DisplayName = result.DisplayName;
                _vm.AppendLogLine($"[CustomMode] Slot {idx} assigned: {result.ActionType}|{result.Target}|{result.DisplayName}");
                SaveCurrentConfig();
            }
            else
            {
                _vm.AppendLogLine("[CustomMode] Picker cancelled or no result");
            }
        }

        private void CustomModeSlotClear_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: int idx }) return;
            if (idx < 0 || idx >= _vm.CustomModeSlots.Count) return;

            // Extra slots (beyond the base 3): remove entirely instead of clearing
            if (idx >= MainViewModel.CustomModeSlotsMin)
            {
                _vm.CustomModeSlots.RemoveAt(idx);
                // Re-index remaining slots so badge numbers stay correct
                for (int i = 0; i < _vm.CustomModeSlots.Count; i++)
                {
                    var s = _vm.CustomModeSlots[i];
                    if (s.Index != i)
                    {
                        // Replace with a new VM at the correct index, preserving data
                        var replacement = new CustomModeSlotViewModel(i);
                        replacement.Apply(s.ToModel());
                        _vm.CustomModeSlots[i] = replacement;
                    }
                }
                _vm.RefreshCanAddCustomSlot();
            }
            else
            {
                _vm.CustomModeSlots[idx].Clear();
            }
            SaveCurrentConfig();
        }

        private void CustomModeAddSlot_Click(object sender, RoutedEventArgs e)
        {
            if (_vm.CustomModeSlots.Count >= MainViewModel.CustomModeSlotsMax) return;
            var newIndex = _vm.CustomModeSlots.Count;
            _vm.CustomModeSlots.Add(new CustomModeSlotViewModel(newIndex));
            _vm.RefreshCanAddCustomSlot();
            SaveCurrentConfig();
        }

        private void CustomSlotExecute_Click(object sender, RoutedEventArgs e)
        {
            Debug.WriteLine($"[CustomMode] CustomSlotExecute_Click fired, sender={sender?.GetType().Name}");
            _vm.AppendLogLine("[CustomMode] CustomSlotExecute_Click fired");

            if (sender is not Button { Tag: int idx })
            {
                Debug.WriteLine("[CustomMode] sender is not Button with int Tag — aborting");
                _vm.AppendLogLine("[CustomMode] ERROR: sender is not Button with int Tag");
                return;
            }

            Debug.WriteLine($"[CustomMode] slot index={idx}, slots count={_vm.CustomModeSlots.Count}");
            _vm.AppendLogLine($"[CustomMode] slot index={idx}");

            if (idx < 0 || idx >= _vm.CustomModeSlots.Count)
            {
                _vm.AppendLogLine($"[CustomMode] ERROR: index {idx} out of range");
                return;
            }

            var slot = _vm.CustomModeSlots[idx];
            if (slot.IsEmpty)
            {
                _vm.AppendLogLine("[CustomMode] slot is empty — nothing to execute");
                return;
            }

            var model = slot.ToModel();
            var payload = $"{model.ActionType}|{model.Target}|{model.DisplayName}";
            _vm.AppendLogLine($"[CustomMode] sending execute_slot:{payload}");
            var sent = _bridge.SendControlCommand($"execute_slot:{payload}");
            _vm.AppendLogLine($"[CustomMode] SendControlCommand returned: {sent}");
        }

        private void BrowseAidiFile_Click(object sender, RoutedEventArgs e)
        {
            string? initialDir = null;
            if (!string.IsNullOrWhiteSpace(_vm.AidiFilePath))
            {
                var existing = Path.GetDirectoryName(_vm.AidiFilePath);
                if (!string.IsNullOrEmpty(existing) && Directory.Exists(existing))
                    initialDir = existing;
            }

            var dialog = new OpenFileDialog
            {
                Title = "Select AIDI File",
                CheckFileExists = true,
                CheckPathExists = true,
                DereferenceLinks = true,
                Multiselect = false,
                Filter = "All files (*.*)|*.*",
                InitialDirectory = initialDir ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)
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

        private DateTime _lastDeviceRefresh = DateTime.MinValue;

        private void RefreshAudioDevices()
        {
            // Debounce: WM_DEVICECHANGE fires multiple times per plug/unplug event.
            var now = DateTime.UtcNow;
            if ((now - _lastDeviceRefresh).TotalMilliseconds < 500)
                return;
            _lastDeviceRefresh = now;

            var inputDevices = _audioDeviceService.GetInputDevices();
            var outputDevices = _audioDeviceService.GetOutputDevices();
            _vm.SetAudioDevices(
                inputDevices,
                outputDevices,
                _vm.SelectedMicrophoneDevice,
                _vm.SelectedOutputDevice);
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
                CustomMode = new CustomModeConfig
                {
                    Enabled = _vm.CustomModeEnabled,
                    Slots   = _vm.CustomModeSlots.Select(s => s.ToModel()).ToArray(),
                },
                VoiceSensitivity = new VoiceSensitivityConfig
                {
                    VadThreshold = _vm.VadThreshold,
                    SilenceMs = _vm.SilenceMs,
                    MinSpeechMs = _vm.MinSpeechMs,
                },
                MouseControl = new MouseControlConfig
                {
                    MovePx = _vm.MouseMovePx,
                    ScrollClicks = _vm.MouseScrollClicks,
                    ScrollStep = _vm.MouseScrollStep,
                },
                PreferredApps = new PreferredAppsConfig
                {
                    Browser = LookupAppId(_browserDisplayToId, _vm.PreferredBrowser),
                    MusicApp = LookupAppId(_musicDisplayToId, _vm.PreferredMusicApp),
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

        // ── Preferred Apps: dynamic population from apps.json ──────

        private void PopulatePreferredAppOptions(string baseDir)
        {
            _browserDisplayToId.Clear();
            _musicDisplayToId.Clear();
            _vm.BrowserOptions.Clear();
            _vm.MusicAppOptions.Clear();

            _vm.BrowserOptions.Add(SystemDefaultLabel);
            _vm.MusicAppOptions.Add(SystemDefaultLabel);
            _browserDisplayToId[SystemDefaultLabel] = string.Empty;
            _musicDisplayToId[SystemDefaultLabel] = string.Empty;

            var appsJsonPath = Path.Combine(baseDir, "apps.json");
            if (!File.Exists(appsJsonPath)) return;

            try
            {
                var json = File.ReadAllText(appsJsonPath);
                var root = JsonNode.Parse(json) as JsonObject;
                var apps = root?["apps"] as JsonArray;
                if (apps == null) return;

                foreach (var node in apps)
                {
                    if (node is not JsonObject app) continue;
                    var id = app["id"]?.GetValue<string>()?.Trim() ?? "";
                    var category = app["category"]?.GetValue<string>()?.Trim().ToLowerInvariant() ?? "";
                    if (string.IsNullOrEmpty(id)) continue;

                    var displayName = FormatAppDisplayName(id);

                    if (category == "browser")
                    {
                        _browserDisplayToId[displayName] = id;
                        _vm.BrowserOptions.Add(displayName);
                    }
                    else if (category == "music")
                    {
                        _musicDisplayToId[displayName] = id;
                        _vm.MusicAppOptions.Add(displayName);
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[PreferredApps] apps.json parse failed: {ex}");
            }
        }

        private static string FormatAppDisplayName(string appId)
        {
            // "yandex_browser" → "Yandex Browser", "chrome" → "Chrome"
            var parts = appId.Split('_');
            for (int i = 0; i < parts.Length; i++)
            {
                if (parts[i].Length > 0)
                    parts[i] = char.ToUpper(parts[i][0]) + parts[i].Substring(1);
            }
            return string.Join(" ", parts);
        }

        private static string ResolvePreferredSelection(
            System.Collections.ObjectModel.ObservableCollection<string> options,
            Dictionary<string, string> displayToId,
            string configId)
        {
            if (string.IsNullOrEmpty(configId))
                return SystemDefaultLabel;

            foreach (var kvp in displayToId)
            {
                if (string.Equals(kvp.Value, configId, StringComparison.OrdinalIgnoreCase))
                    return kvp.Key;
            }

            return options.Count > 0 ? options[0] : SystemDefaultLabel;
        }

        private static string LookupAppId(Dictionary<string, string> displayToId, string? displayName)
        {
            if (string.IsNullOrEmpty(displayName) || displayName == SystemDefaultLabel)
                return string.Empty;
            return displayToId.TryGetValue(displayName, out var id) ? id : string.Empty;
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

        private static void CloseOpenComboBoxes(DependencyObject root)
        {
            if (root == null) return;
            int count = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
            for (int i = 0; i < count; i++)
            {
                var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
                if (child is ComboBox cb && cb.IsDropDownOpen)
                    cb.IsDropDownOpen = false;
                else
                    CloseOpenComboBoxes(child);
            }
        }

        private static string GetProjectRootDirectory(string baseDir)
        {
            // Walk up, skipping typical build output segments (bin, obj, Debug, Release, net*-*)
            var buildSegments = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                { "bin", "obj", "Debug", "Release", "x64", "x86", "AnyCPU" };
            var dir = new DirectoryInfo(baseDir.TrimEnd(
                Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            while (dir?.Parent != null &&
                   (buildSegments.Contains(dir.Name) ||
                    (dir.Name.StartsWith("net", StringComparison.OrdinalIgnoreCase) && dir.Name.Contains('.'))))
            {
                dir = dir.Parent;
            }
            return dir?.FullName ?? baseDir;
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

            _glowCommandListening = new DoubleAnimation
            {
                From = 1.00,
                To = 1.10,
                Duration = new Duration(TimeSpan.FromSeconds(0.8)),
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

        // ===== Emblem VAD animation =====
        private void SetEmblemActive(bool active)
        {
            // Only animate when emblem is actually visible (LISTENING / COMMAND_LISTENING states)
            if (!_emblemVisible)
                return;

            if (active)
            {
                if (_emblemSb != null)
                    return; // already running
                if (Resources["SB_Emblem_Active"] is Storyboard sb)
                {
                    _emblemSb = sb;
                    sb.Begin(this, true);
                }
            }
            else
            {
                if (_emblemSb != null)
                {
                    _emblemSb.Stop(this);
                    _emblemSb = null;
                }
                // Reset emblem to neutral
                EmblemScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
                EmblemScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);
                EmblemScale.ScaleX = 1; EmblemScale.ScaleY = 1;
                EmblemGlow.BeginAnimation(System.Windows.Media.Effects.DropShadowEffect.OpacityProperty, null);
                EmblemGlow.BeginAnimation(System.Windows.Media.Effects.DropShadowEffect.BlurRadiusProperty, null);
                EmblemGlow.Opacity = 0; EmblemGlow.BlurRadius = 0;
            }
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
            OuterGlow.Visibility = Visibility.Visible;
            OuterGlow.BeginAnimation(OpacityProperty, null);
            OuterGlow.Opacity = 1;

            RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, null);
            OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
            OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);
            WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
            WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);
            Wave.BeginAnimation(OpacityProperty, null);
            Wave.Opacity = 1;
            Wave.Visibility = Visibility.Visible;
            WaitingHotkeySleep.Visibility = Visibility.Collapsed;
            SpeakingWave.Visibility = Visibility.Collapsed;
            FollowUpWave.Visibility = Visibility.Collapsed;
            // Reset denied cross animations
            DeniedCross.BeginAnimation(OpacityProperty, null);
            DeniedCrossScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
            DeniedCrossScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);
            DeniedCross.Opacity = 1;
            DeniedCrossScale.ScaleX = 1; DeniedCrossScale.ScaleY = 1;
            DeniedCross.Visibility = Visibility.Collapsed;
            CenterEmblem.Visibility = Visibility.Collapsed;
            _emblemVisible = false;
            
            if (_emblemSb != null) { _emblemSb.Stop(this); _emblemSb = null; }
            EmblemScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, null);
            EmblemScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, null);
            EmblemScale.ScaleX = 1; EmblemScale.ScaleY = 1;
            EmblemGlow.BeginAnimation(System.Windows.Media.Effects.DropShadowEffect.OpacityProperty, null);
            EmblemGlow.BeginAnimation(System.Windows.Media.Effects.DropShadowEffect.BlurRadiusProperty, null);
            EmblemGlow.Opacity = 0; EmblemGlow.BlurRadius = 0;
            StopWaveformAnimation();

            // ===== Ring storyboard by state =====
            switch (state)
            {
                // ... (оставь все остальные case без изменений, кроме этих двух ниже) ...

                case AidyState.AccessDenied:
                    System.Media.SystemSounds.Hand.Play();
                    // Fade out the blue glow & wave smoothly
                    OuterGlow.BeginAnimation(OpacityProperty,
                        new DoubleAnimation(0, new Duration(TimeSpan.FromMilliseconds(250))));
                    Wave.BeginAnimation(OpacityProperty,
                        new DoubleAnimation(0, new Duration(TimeSpan.FromMilliseconds(200))));
                    // Show denied cross with fade-in + scale pop
                    DeniedCross.Visibility = Visibility.Visible;
                    DeniedCross.Opacity = 0;
                    DeniedCross.BeginAnimation(OpacityProperty,
                        new DoubleAnimation(0, 1, new Duration(TimeSpan.FromMilliseconds(300)))
                        { EasingFunction = new SineEase { EasingMode = EasingMode.EaseOut } });
                    DeniedCrossScale.ScaleX = 0.3;
                    DeniedCrossScale.ScaleY = 0.3;
                    var deniedPopX = new DoubleAnimation(0.3, 1.0, new Duration(TimeSpan.FromMilliseconds(350)))
                    { EasingFunction = new ElasticEase { EasingMode = EasingMode.EaseOut, Oscillations = 1, Springiness = 4 } };
                    var deniedPopY = new DoubleAnimation(0.3, 1.0, new Duration(TimeSpan.FromMilliseconds(350)))
                    { EasingFunction = new ElasticEase { EasingMode = EasingMode.EaseOut, Oscillations = 1, Springiness = 4 } };
                    DeniedCrossScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, deniedPopX);
                    DeniedCrossScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, deniedPopY);
                    StartRing("SB_Ring_AccessDenied");
                    break;

                case AidyState.Error:
                    OuterGlow.Visibility = Visibility.Collapsed; // Прячем синий фон!
                    Wave.BeginAnimation(OpacityProperty, _waveOff);
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    break;
                    
                // ... (остальные case тоже без изменений) ...
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
                    StartRing("SB_Ring_CommandListening");
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
                case AidyState.GrantRole:
                    StartRing("SB_Ring_GrantRole");
                    break;
                case AidyState.GrantDuration:
                    StartRing("SB_Ring_GrantDuration");
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
                    Wave.Opacity = 1;
                    Wave.Visibility = Visibility.Collapsed;
                    CenterEmblem.Visibility = Visibility.Visible;
                    _emblemVisible = true;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;

                case AidyState.CommandListening:
                    Wave.Opacity = 1;
                    Wave.Visibility = Visibility.Collapsed;
                    CenterEmblem.Visibility = Visibility.Visible;
                    _emblemVisible = true;
                    // Faster rotation + bigger glow = "I heard you, speak now!"
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateFast);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowCommandListening);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowCommandListening);
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
                    Wave.Visibility = Visibility.Collapsed;
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
                    Wave.Visibility = Visibility.Collapsed;
                    FollowUpWave.Visibility = Visibility.Visible;
                    StartWaveformAnimation();
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
                case AidyState.GrantRole:
                    Wave.Opacity = 1;
                    Wave.Visibility = Visibility.Collapsed;
                    CenterEmblem.Visibility = Visibility.Visible;
                    _emblemVisible = true;
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _glowActive);
                    OuterGlowScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _glowActive);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, _waveProcessing);
                    WaveScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, _waveProcessing);
                    break;
                case AidyState.GrantDuration:
                    Wave.Opacity = 1;
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

                case AidyState.AccessDenied:
                    // Wave & DeniedCross already animated in the first switch
                    RingRotate.BeginAnimation(System.Windows.Media.RotateTransform.AngleProperty, _rotateSlow);
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

                CompactToggleButton.Width = 36;
                CompactToggleButton.Height = 26;
                MinimizeButton.Width = 32;
                MinimizeButton.Height = 26;
                CloseButton.Width = 32;
                CloseButton.Height = 26;

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

            CompactToggleButton.Width = 52;
            CompactToggleButton.Height = 34;
            MinimizeButton.Width = 44;
            MinimizeButton.Height = 32;
            CloseButton.Width = 44;
            CloseButton.Height = 32;

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
        private void NavSettings_Click(object sender, RoutedEventArgs e)
        {
            ShowPage("SETTINGS");
            if (_bridgeStarted && _vm.VoiceIdEnabled)
                _bridge.SendControlCommand("list_voice_users");
        }

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

            // Close any open ComboBox dropdowns before scrolling so they don't float out of place.
            CloseOpenComboBoxes(SettingsPage);

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
