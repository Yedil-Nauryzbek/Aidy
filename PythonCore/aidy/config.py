API_URL = "http://127.0.0.1:8008/predict"

WAKE_KEYWORDS = {
    "aidy",
    "ady",
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

CONFIRM_YES = {"yes", "confirm", "do it", "sure", "ok", "okay", "proceed"}
CONFIRM_NO = {"no", "no sir", "cancel", "stop", "don't", "do not", "never mind", "abort"}
CONFIRM_GRAMMAR_PHRASES = sorted(CONFIRM_YES | CONFIRM_NO)


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
