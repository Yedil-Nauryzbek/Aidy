// WpfApp1/Services/VoiceCuePlayer.cs
// Заглушка под кастомные mp3 в папке Assets.
// 1) Создай папку: WpfApp1/Assets
// 2) Положи файлы: ready.mp3, listening.mp3, processing.mp3, speaking.mp3, ok.mp3, error.mp3 (любые названия ок)
// 3) Для каждого mp3: Build Action = Resource (важно)
using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Media;

namespace WpfApp1.Services
{
    public sealed class VoiceCuePlayer
    {
        private readonly Dictionary<string, Uri> _map;
        private MediaPlayer? _player;

        public VoiceCuePlayer()
        {
            _map = new Dictionary<string, Uri>(StringComparer.OrdinalIgnoreCase)
            {
                ["ready"] = Pack("Assets/ready.mp3"),
                ["listening"] = Pack("Assets/listening.mp3"),
                ["processing"] = Pack("Assets/processing.mp3"),
                ["speaking"] = Pack("Assets/speaking.mp3"),
                ["ok"] = Pack("Assets/ok.mp3"),
                ["error"] = Pack("Assets/error.mp3"),
            };
        }

        private static Uri Pack(string relative)
            => new Uri($"pack://application:,,,/{relative}", UriKind.Absolute);

        public void Play(string key, double volume = 0.95)
        {
            if (!_map.TryGetValue(key, out var uri))
                return; // нет файла — молча игнор (это и есть заглушка)

            // MediaPlayer должен жить на UI thread
            Application.Current.Dispatcher.Invoke(() =>
            {
                try
                {
                    _player ??= new MediaPlayer();
                    _player.Stop();
                    _player.Open(uri);
                    _player.Volume = Math.Clamp(volume, 0, 1);
                    _player.Play();
                }
                catch
                {
                    // заглушка: не падаем из-за аудио
                }
            });
        }

        public void Stop()
        {
            Application.Current.Dispatcher.Invoke(() =>
            {
                try { _player?.Stop(); } catch { }
            });
        }
    }
}
