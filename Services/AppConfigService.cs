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

                    var customModeNode = root["custom_mode"] as JsonObject;
                    var customModeSlots = ReadCustomModeSlots(customModeNode);
                    var voiceSensNode = root["voice_sensitivity"] as JsonObject;
                    var mouseCtrlNode = root["mouse_control"] as JsonObject;
                    var preferredAppsNode = root["preferred_apps"] as JsonObject;

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
                        CustomMode = new CustomModeConfig
                        {
                            Enabled = ReadBool(customModeNode, "enabled"),
                            Slots = customModeSlots,
                        },
                        VoiceSensitivity = new VoiceSensitivityConfig
                        {
                            VadThreshold = Math.Clamp(ReadInt(voiceSensNode, "vad_threshold", VoiceSensitivityConfig.DefaultVadThreshold), 50, 500),
                            SilenceMs = Math.Clamp(ReadInt(voiceSensNode, "silence_ms", VoiceSensitivityConfig.DefaultSilenceMs), 200, 2000),
                            MinSpeechMs = Math.Clamp(ReadInt(voiceSensNode, "min_speech_ms", VoiceSensitivityConfig.DefaultMinSpeechMs), 50, 500),
                        },
                        MouseControl = new MouseControlConfig
                        {
                            MovePx = Math.Clamp(ReadInt(mouseCtrlNode, "move_px", MouseControlConfig.DefaultMovePx), 50, 2000),
                            ScrollClicks = Math.Clamp(ReadInt(mouseCtrlNode, "scroll_clicks", MouseControlConfig.DefaultScrollClicks), 10, 1000),
                            ScrollStep = Math.Clamp(ReadInt(mouseCtrlNode, "scroll_step", MouseControlConfig.DefaultScrollStep), 1, 100),
                            ScrollDelay = Math.Clamp(ReadDouble(mouseCtrlNode, "scroll_delay", MouseControlConfig.DefaultScrollDelay), 0.0, 0.1),
                        },
                        PreferredApps = new PreferredAppsConfig
                        {
                            Browser = ReadString(preferredAppsNode, "browser"),
                            MusicApp = ReadString(preferredAppsNode, "music_app"),
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

                var customMode = EnsureObject(root, "custom_mode");
                customMode["enabled"] = config.CustomMode?.Enabled ?? false;
                WriteCustomModeSlots(customMode, config.CustomMode?.Slots);

                var voiceSens = EnsureObject(root, "voice_sensitivity");
                voiceSens["vad_threshold"] = config.VoiceSensitivity?.VadThreshold ?? VoiceSensitivityConfig.DefaultVadThreshold;
                voiceSens["silence_ms"] = config.VoiceSensitivity?.SilenceMs ?? VoiceSensitivityConfig.DefaultSilenceMs;
                voiceSens["min_speech_ms"] = config.VoiceSensitivity?.MinSpeechMs ?? VoiceSensitivityConfig.DefaultMinSpeechMs;

                var mouseCtrl = EnsureObject(root, "mouse_control");
                mouseCtrl["move_px"] = config.MouseControl?.MovePx ?? MouseControlConfig.DefaultMovePx;
                mouseCtrl["scroll_clicks"] = config.MouseControl?.ScrollClicks ?? MouseControlConfig.DefaultScrollClicks;
                mouseCtrl["scroll_step"] = config.MouseControl?.ScrollStep ?? MouseControlConfig.DefaultScrollStep;
                mouseCtrl["scroll_delay"] = config.MouseControl?.ScrollDelay ?? MouseControlConfig.DefaultScrollDelay;

                var preferredApps = EnsureObject(root, "preferred_apps");
                preferredApps["browser"] = config.PreferredApps?.Browser ?? string.Empty;
                preferredApps["music_app"] = config.PreferredApps?.MusicApp ?? string.Empty;

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

        private static double ReadDouble(JsonObject? node, string propertyName, double fallback)
        {
            if (node == null)
            {
                return fallback;
            }

            try
            {
                return node[propertyName]?.GetValue<double>() ?? fallback;
            }
            catch
            {
                return fallback;
            }
        }

        private static CustomModeSlot[] ReadCustomModeSlots(JsonObject? node)
        {
            var defaults = new[] { new CustomModeSlot(), new CustomModeSlot(), new CustomModeSlot() };
            if (node == null) return defaults;

            try
            {
                if (node["slots"] is not JsonArray arr) return defaults;

                var result = new CustomModeSlot[arr.Count];
                for (int i = 0; i < arr.Count; i++)
                {
                    if (arr[i] is JsonObject slotObj)
                    {
                        result[i] = new CustomModeSlot
                        {
                            ActionType  = ReadString(slotObj, "action_type"),
                            Target      = ReadString(slotObj, "target"),
                            DisplayName = ReadString(slotObj, "display_name"),
                        };
                    }
                    else
                    {
                        result[i] = new CustomModeSlot();
                    }
                }
                return result;
            }
            catch
            {
                return defaults;
            }
        }

        private static void WriteCustomModeSlots(JsonObject customMode, CustomModeSlot[]? slots)
        {
            var arr = new JsonArray();
            var src = slots ?? Array.Empty<CustomModeSlot>();
            foreach (var s in src)
            {
                arr.Add(new JsonObject
                {
                    ["action_type"]  = s?.ActionType  ?? string.Empty,
                    ["target"]       = s?.Target      ?? string.Empty,
                    ["display_name"] = s?.DisplayName ?? string.Empty,
                });
            }
            customMode["slots"] = arr;
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
