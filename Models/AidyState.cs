// WpfApp1/Models/AidyState.cs
namespace WpfApp1.Models
{
    public enum AidyState
    {
        Starting,    // app/core booting
        Idle,        // READY
        Listening,   // mic listening
        CommandListening, // listening right after wake word
        Processing,  // thinking/processing
        Speaking,    // TTS speaking
        Confirming,  // awaiting confirmation
        FollowUp,    // awaiting short numeric follow-up
        GrantRole,   // grant access — asking "User or Admin?"
        GrantDuration, // grant access — asking "By how much? (1-60)"

        Executing,   // command executing
        Success,     // one-shot ping then back to Idle
        Warning,     // needs attention/confirmation
        AccessDenied,// voice auth failed — red cross + beep
        Error,       // error state
        Offline      // disconnected
    }
}
