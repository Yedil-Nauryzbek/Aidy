using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using WpfApp1.Models;
using WpfApp1.ViewModels;

namespace WpfApp1.Services
{
    public sealed class AppConfigService
    {
        private static readonly JsonSerializerOptions SaveJsonOptions = new()
        {
            WriteIndented = true
        };

        private readonly string _configPath;
        private readonly object _sync = new();

        public AppConfigService(string configPath)
        {
            _configPath = configPath ?? throw new ArgumentNullException(nameof(configPath));
        }

        public AppConfig Load()
        {
            lock (_sync)
            {
                try
                {
                    var root = LoadJsonObject() ?? new JsonObject();
                    var audioNode = root["audio"] as JsonObject;
                    var startupNode = root["startup"] as JsonObject;
                    var aidiNode = root["aidi"] as JsonObject;

                    var config = new AppConfig
                    {
                        PushToTalkEnabled = ReadBool(root, "push_to_talk_enabled"),
                        PushToTalkKey = MainViewModel.NormalizePushToTalkKeyName(
                            ReadString(root, "push_to_talk_key", MainViewModel.DefaultPushToTalkKey)),
                        AutoStartEnabled = ReadBool(root, "auto_start_enabled"),
                        VoiceIdEnabled = ReadBool(root, "voice_id_enabled"),
                        Audio = new AudioConfig
                        {
                            Microphone = ReadString(audioNode, "microphone"),
                            OutputDevice = ReadString(audioNode, "output_device"),
                        },
                        Startup = new StartupConfig
                        {
                            GreetingEnabled = ReadBool(startupNode, "greeting_enabled"),
                        },
                        Aidi = new AidiConfig
                        {
                            FilePath = NormalizeAbsolutePath(ReadString(aidiNode, "file_path")),
                            Volume = NormalizeVolume(ReadInt(aidiNode, "volume", AppConfig.DefaultAidiVolume)),
                        },
                    };

                    // Ensure defaults are materialized on first launch or after invalid edits.
                    Save(config);
                    return config;
                }
                catch
                {
                    var fallback = new AppConfig();
                    Save(fallback);
                    return fallback;
                }
            }
        }

        public void Save(AppConfig config)
        {
            if (config == null) throw new ArgumentNullException(nameof(config));

            lock (_sync)
            {
                var root = LoadJsonObject() ?? new JsonObject();
                var audio = EnsureObject(root, "audio");
                var startup = EnsureObject(root, "startup");
                var aidi = EnsureObject(root, "aidi");
                var normalizedPushToTalkKey = MainViewModel.NormalizePushToTalkKeyName(config.PushToTalkKey);
                var aidiVolume = NormalizeVolume(config.Aidi?.Volume ?? AppConfig.DefaultAidiVolume);
                var aidiFilePath = NormalizeAbsolutePath(config.Aidi?.FilePath);

                root["push_to_talk_enabled"] = config.PushToTalkEnabled;
                root["push_to_talk_key"] = normalizedPushToTalkKey;
                root["auto_start_enabled"] = config.AutoStartEnabled;
                root["voice_id_enabled"] = config.VoiceIdEnabled;
                audio["microphone"] = config.Audio?.Microphone ?? string.Empty;
                audio["output_device"] = config.Audio?.OutputDevice ?? string.Empty;
                startup["greeting_enabled"] = config.Startup?.GreetingEnabled ?? false;
                aidi["file_path"] = aidiFilePath;
                aidi["volume"] = aidiVolume;

                var dir = Path.GetDirectoryName(_configPath);
                if (!string.IsNullOrWhiteSpace(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                File.WriteAllText(_configPath, root.ToJsonString(SaveJsonOptions));
            }
        }

        private JsonObject? LoadJsonObject()
        {
            if (!File.Exists(_configPath))
            {
                return null;
            }

            try
            {
                var raw = File.ReadAllText(_configPath);
                return JsonNode.Parse(raw) as JsonObject;
            }
            catch
            {
                return null;
            }
        }

        private static JsonObject EnsureObject(JsonObject root, string propertyName)
        {
            if (root[propertyName] is JsonObject existing)
            {
                return existing;
            }

            var created = new JsonObject();
            root[propertyName] = created;
            return created;
        }

        private static string ReadString(JsonObject? node, string propertyName, string fallback = "")
        {
            if (node == null)
            {
                return fallback;
            }

            try
            {
                return node[propertyName]?.GetValue<string>()?.Trim() ?? fallback;
            }
            catch
            {
                return fallback;
            }
        }

        private static bool ReadBool(JsonObject? node, string propertyName, bool fallback = false)
        {
            if (node == null)
            {
                return fallback;
            }

            try
            {
                return node[propertyName]?.GetValue<bool>() ?? fallback;
            }
            catch
            {
                return fallback;
            }
        }

        private static int ReadInt(JsonObject? node, string propertyName, int fallback)
        {
            if (node == null)
            {
                return fallback;
            }

            try
            {
                return node[propertyName]?.GetValue<int>() ?? fallback;
            }
            catch
            {
                return fallback;
            }
        }

        private static int NormalizeVolume(int value)
        {
            return Math.Clamp(value, 0, 100);
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
    }
}
