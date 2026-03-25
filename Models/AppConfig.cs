namespace WpfApp1.Models
{
    public sealed class AppConfig
    {
        public const int DefaultAidiVolume = 50;

        public bool PushToTalkEnabled { get; set; }
        public string PushToTalkKey { get; set; } = "LeftCtrl";
        public bool AutoStartEnabled { get; set; }
        public bool VoiceIdEnabled { get; set; }
        public AudioConfig Audio { get; set; } = new();
        public StartupConfig Startup { get; set; } = new();
        public AidiConfig Aidi { get; set; } = new();
        public CustomModeConfig CustomMode { get; set; } = new();
        public VoiceSensitivityConfig VoiceSensitivity { get; set; } = new();
        public MouseControlConfig MouseControl { get; set; } = new();
        public PreferredAppsConfig PreferredApps { get; set; } = new();
    }

    public sealed class AudioConfig
    {
        public string Microphone { get; set; } = string.Empty;
        public string OutputDevice { get; set; } = string.Empty;
    }

    public sealed class StartupConfig
    {
        public bool GreetingEnabled { get; set; } = true;
    }

    public sealed class AidiConfig
    {
        public string FilePath { get; set; } = string.Empty;
        public int Volume { get; set; } = AppConfig.DefaultAidiVolume;
    }

    public sealed class CustomModeSlot
    {
        public string ActionType { get; set; } = string.Empty;
        public string Target { get; set; } = string.Empty;
        public string DisplayName { get; set; } = string.Empty;
    }

    public sealed class CustomModeConfig
    {
        public bool Enabled { get; set; }
        public CustomModeSlot[] Slots { get; set; } =
            new[] { new CustomModeSlot(), new CustomModeSlot(), new CustomModeSlot() };
    }

    public sealed class VoiceSensitivityConfig
    {
        public const int DefaultVadThreshold = 140;
        public const int DefaultSilenceMs = 700;
        public const int DefaultMinSpeechMs = 140;

        public int VadThreshold { get; set; } = DefaultVadThreshold;
        public int SilenceMs { get; set; } = DefaultSilenceMs;
        public int MinSpeechMs { get; set; } = DefaultMinSpeechMs;
    }

    public sealed class MouseControlConfig
    {
        public const int DefaultMovePx = 500;
        public const int DefaultScrollClicks = 300;
        public const int DefaultScrollStep = 15;
        public const double DefaultScrollDelay = 0.002;

        public int MovePx { get; set; } = DefaultMovePx;
        public int ScrollClicks { get; set; } = DefaultScrollClicks;
        public int ScrollStep { get; set; } = DefaultScrollStep;
        public double ScrollDelay { get; set; } = DefaultScrollDelay;
    }

    public sealed class PreferredAppsConfig
    {
        public string Browser { get; set; } = string.Empty;
        public string MusicApp { get; set; } = string.Empty;
    }
}
