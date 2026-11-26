using System;
using System.Diagnostics;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using WpfApp1.Models;

namespace WpfApp1.Views
{
    public partial class MainWindow
    {
        private readonly Stopwatch _audioWaveClock = Stopwatch.StartNew();
        private DispatcherTimer? _audioWaveTimer;
        private double _audioWaveTarget;
        private double _audioWaveLevel;
        private double _lastAudioWaveInput = -1;
        private double _lastAudioWaveFrame;

        private void InitializeAudioWaves()
        {
            _audioWaveTimer = new DispatcherTimer(DispatcherPriority.Background)
            {
                Interval = TimeSpan.FromMilliseconds(33)
            };
            _audioWaveTimer.Tick += AudioWaveTick;
            _bridge.AudioLevelChanged += OnAudioLevelChanged;
            IsVisibleChanged += (_, _) => { if (!IsVisible) StopAudioWaves(); };
            StateChanged += (_, _) => { if (WindowState == WindowState.Minimized) StopAudioWaves(); };
        }

        private bool CanShowAudioWaves => IsVisible && WindowState != WindowState.Minimized &&
            _vm.CurrentState is AidyState.Listening or AidyState.CommandListening or
                AidyState.Confirming or AidyState.FollowUp;

        private void OnAudioLevelChanged(double level) => OnUi(() =>
        {
            if (!CanShowAudioWaves) return;
            _audioWaveTarget = Math.Clamp(level, 0, 1);
            _lastAudioWaveInput = _audioWaveClock.Elapsed.TotalSeconds;
            if (_audioWaveTarget > 0.015 && _audioWaveTimer is { IsEnabled: false })
            {
                _lastAudioWaveFrame = _lastAudioWaveInput;
                _audioWaveTimer.Start();
            }
        });

        private void UpdateAudioWaveState(AidyState state)
        {
            if (state is not (AidyState.Listening or AidyState.CommandListening or
                AidyState.Confirming or AidyState.FollowUp))
                StopAudioWaves();
        }

        private void AudioWaveTick(object? sender, EventArgs e)
        {
            if (!CanShowAudioWaves) { StopAudioWaves(); return; }
            double now = _audioWaveClock.Elapsed.TotalSeconds;
            double dt = Math.Clamp(now - _lastAudioWaveFrame, 0.001, 0.1);
            _lastAudioWaveFrame = now;
            // Fade if capture stops, the backend disconnects or frames are late.
            if (now - _lastAudioWaveInput > 0.18) _audioWaveTarget = 0;
            double tau = _audioWaveTarget > _audioWaveLevel ? 0.025 : 0.19;
            _audioWaveLevel += (_audioWaveTarget - _audioWaveLevel) * (1 - Math.Exp(-dt / tau));
            if (_audioWaveLevel < 0.006 && _audioWaveTarget == 0)
            {
                StopAudioWaves();
                return;
            }

            // Rounded equalizer bars, like the Speaking icon, around the same
            // outer edge. Their size follows microphone amplitude, not STT.
            double edge = (NeonRing.Width + NeonRing.StrokeThickness) / 2 *
                          Math.Max(RingScale.ScaleX, RingScale.ScaleY);
            // Boost quiet speech visually without changing microphone gain or
            // speech detection. Keep headroom so louder sounds still grow.
            double response = Math.Sqrt(_audioWaveLevel);
            var bars = CreateAudioBars(edge, response, now);
            AudioWaveInner.Data = AudioWaveOuter.Data = bars;
            AudioWaveInner.Opacity = Math.Min(0.95, response * 1.8);
            AudioWaveOuter.Opacity = Math.Min(0.18, response * 0.32);
        }

        private static StreamGeometry CreateAudioBars(double edge, double level, double time)
        {
            var geometry = new StreamGeometry();
            using (var context = geometry.Open())
            {
                ReadOnlySpan<double> pattern = [0.30, 0.42, 0.24, 0.59, 0.82, 0.44, 1.0, 0.35, 0.76];
                const int count = 64;
                double radius = edge + 6;
                // Reserve space for rounded strokes within the 520px host,
                // including at the largest animated ring scale.
                double amplitude = Math.Clamp(260 - radius - 7, 0, 66);
                for (int i = 0; i < count; i++)
                {
                    double angle = i * 2 * Math.PI / count - Math.PI / 2;
                    double length = 2 + amplitude * level * pattern[i % pattern.Length] *
                                    (0.9 + 0.1 * Math.Sin(time * 4 + i * .8));
                    context.BeginFigure(new Point(260 + Math.Cos(angle) * radius,
                        260 + Math.Sin(angle) * radius), isFilled: false, isClosed: false);
                    context.LineTo(new Point(260 + Math.Cos(angle) * (radius + length),
                        260 + Math.Sin(angle) * (radius + length)), isStroked: true, isSmoothJoin: false);
                }
            }
            geometry.Freeze();
            return geometry;
        }

        private void StopAudioWaves()
        {
            _audioWaveTimer?.Stop();
            _audioWaveTarget = _audioWaveLevel = 0;
            AudioWaveInner.Opacity = AudioWaveOuter.Opacity = 0;
            AudioWaveInner.Data = AudioWaveOuter.Data = Geometry.Empty;
        }
    }
}
