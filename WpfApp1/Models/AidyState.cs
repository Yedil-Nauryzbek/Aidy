// WpfApp1/Models/AidyState.cs
namespace WpfApp1.Models
{
    public enum AidyState
    {
        Idle,        // READY
        Listening,   // mic listening
        Processing,  // thinking/processing
        Speaking,    // TTS speaking

        Executing,   // command executing
        Success,     // one-shot ping then back to Idle
        Warning,     // needs attention/confirmation
        Error,       // error state
        Offline      // disconnected
    }
}
