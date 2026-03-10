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
    }

    public sealed class AudioConfig
    {
        public string Microphone { get; set; } = string.Empty;
        public string OutputDevice { get; set; } = string.Empty;
    }

    public sealed class StartupConfig
    {
        public bool GreetingEnabled { get; set; }
    }

    public sealed class AidiConfig
    {
        public string FilePath { get; set; } = string.Empty;
        public int Volume { get; set; } = AppConfig.DefaultAidiVolume;
    }
}
