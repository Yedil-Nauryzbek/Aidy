API_URL = "http://127.0.0.1:8008/predict"

WAKE_KEYWORDS = {
    "aidy",
    "ady",
    "hey",
    "hey aidy",
    "hey assistant",
    "hello assistant",
    "ok aidy",
    "eddie",
    "hey eddie",
    "ok eddie",
    "eighty",
    "hey eighty",
    "ok eighty",
    "a d",
    "id",
    "edit",
}

def is_wake_phrase(text: str) -> bool:
    t = (text or "").lower().strip()
    t = " ".join(t.split())
    if len(t) < 3:
        return False

    if t in WAKE_KEYWORDS:
        return True

    for w in WAKE_KEYWORDS:
        if w in t:
            return True

    return False


SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4000
FRAME_MS = 250
VAD_START_THRESHOLD = 250
VAD_SILENCE_MS = 650


DANGEROUS_INTENTS = {"shutdown", "restart"}

CONFIRM_YES = {"yes", "confirm", "conferm", "confim", "confirmm", "do it", "sure", "ok", "okay", "proceed"}
CONFIRM_NO = {"no", "no sir", "cancel", "stop", "don't", "do not", "never mind", "abort"}
CONFIRM_GRAMMAR_PHRASES = sorted(CONFIRM_YES | CONFIRM_NO)

REPEAT_PHRASES = {
    "repeat",
    "repeat that",
    "repeat it",
    "repeat last command",
    "repeat last",
    "do it again",
    "again",
    "repeet",
    "repete",
    "repit",
    "repet",
    "ripit",
    "repet it",
    "repeat it again",
}

CLOSE_ACTIVE_PHRASES = {
    "close this",
    "close it",
    "close window",
    "close current window",
    "close current app",
    "close current application",
    "close active app",
    "close active window",
    "close this window",
    "close this app",
}

MUTE_PHRASES = {
    "mute",
    "mute aidy",
    "mute ady",
    "mute eddie",
    "mute edy",
    "shut up",
    "shut it",
    "shutup",
    "shat up",
    "shut ap",
    "shut op",
    "shot up",
}

UNMUTE_PHRASES = {
    "unmute",
    "unmute aidy",
    "unmute ady",
    "unmute eddie",
    "unmute edy",
    "un mute",
    "an mute",
    "and mute",
    "on mute",
    "one mute",
    "unmuted",
    "unmoot",
    "unmoot aidy",
    "sound back",
    "sound on",
    "turn sound on",
    "sound on",
    "turn on sound",
    "turn sound on",
}

UNDO_LAST_PHRASES = {
    "undo",
    "undo last",
    "undo that",
    "undo it",
    "go back",
    "revert",
    "cancel that",
}

UNDO_ALL_PHRASES = {
    "undo all",
    "undo everything",
    "undo all that",
    "undo everything you did",
    "undo all you did",
    "undo the last actions",
}


WINDOW_SWITCH_LEFT = {"left", "previous", "back"}
WINDOW_SWITCH_RIGHT = {"right", "next", "forward"}
WINDOW_SWITCH_DONE = {"done", "select", "choose", "ok"}
WINDOW_SWITCH_CANCEL = {"cancel", "stop", "exit"}
WINDOW_SWITCH_GRAMMAR = sorted(
    WINDOW_SWITCH_LEFT | WINDOW_SWITCH_RIGHT | WINDOW_SWITCH_DONE | WINDOW_SWITCH_CANCEL
)


VOICE_RESPONSES = {
    "volume up": "Increasing volume",
    "volume down": "Decreasing volume",
    "set volume": "Setting volume",
    "brightness up": "Increasing brightness",
    "brightness down": "Decreasing brightness",
    "shutdown": "Shutting down computer in 5 seconds",
    "restart": "Restarting computer in 5 seconds",
    "lock": "Locking screen",
    "open cmd": "Opening command prompt",
    "show desktop": "Showing desktop",
    "screenshot": "Taking screenshot",
    "task manager": "Opening task manager",
    "switch window": "Switching window",
    "open app": "Opening application",
    "close app": "Closing application",
}
