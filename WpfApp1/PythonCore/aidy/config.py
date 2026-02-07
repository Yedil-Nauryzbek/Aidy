API_URL = "http://127.0.0.1:8008/predict"

WAKE_KEYWORDS = {
    "aidy",
    "ady",
    "hey",
    "hey aidy",
    "hey assistant",
    "hello assistant",
    "ok aidy",
    "okay aidy",
    "okay assistant",
    "eddie",
    "hey eddie",
    "ok eddie",
    "okay eddie",
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

REPEAT_LAST_STEPS = False
FOLLOW_MODE_ENABLED = True
FOLLOW_MODE_TTL_SECONDS = 10
FOLLOW_MODE_REPEAT_LAST_STEPS = False

MORE_ACTION_PHRASES = {
    "more",
    "again",
    "next",
    "ещё",
    "еще",
    "дальше",
}

LESS_ACTION_PHRASES = {
    "less",
    "back",
    "меньше",
    "назад",
}

NUMERIC_VARIANTS = {
    1: [
        "1", "one", "won", "wun", "wan", "wone", "on", "un", "oan", "hwon",
        "number one", "num one", "one step", "one please", "won step",
    ],
    2: [
        "2", "two", "too", "to", "tu", "tuu", "twoo", "tow", "tew", "number two",
        "num two", "two step", "too step", "to step", "two please",
    ],
    3: [
        "3", "three", "tree", "threee", "thre", "thri", "thry", "free", "sree", "number three",
        "num three", "three step", "tree step", "three please", "thri step",
    ],
    4: [
        "4", "four", "for", "fore", "foor", "fourr", "fur", "phor", "foar", "number four",
        "num four", "four step", "for step", "four please", "fore step",
    ],
    5: [
        "5", "five", "fiv", "fife", "faiv", "faeve", "fyve", "fibe", "hive", "number five",
        "num five", "five step", "fife step", "five please", "faiv step",
    ],
    6: [
        "6", "six", "sics", "sic", "sik", "seeks", "sikx", "sex", "sicks", "number six",
        "num six", "six step", "sik step", "six please", "sics step",
    ],
    7: [
        "7", "seven", "sevan", "siven", "sevun", "seben", "zeven", "savin", "sevin", "number seven",
        "num seven", "seven step", "seven please", "sevun step", "siven step",
    ],
    8: [
        "8", "eight", "ate", "aight", "eit", "eyt", "ait", "eigh", "eightt", "number eight",
        "num eight", "eight step", "ate step", "eight please", "aight step",
    ],
    9: [
        "9", "nine", "nain", "nyne", "naine", "nein", "nien", "nayn", "number nine", "num nine",
        "nine step", "nain step", "nine please", "nyne step", "nayn step",
    ],
    10: [
        "10", "ten", "tin", "tenn", "tehn", "tane", "den", "then", "number ten", "num ten",
        "ten step", "tin step", "ten please", "tehn step", "then step",
    ],
}

NUMERIC_FOLLOWUP_WORD_TO_VALUE = {}
for _value, _variants in NUMERIC_VARIANTS.items():
    for _variant in _variants:
        NUMERIC_FOLLOWUP_WORD_TO_VALUE[_variant] = _value
NUMERIC_FOLLOWUP_GRAMMAR_PHRASES = sorted(NUMERIC_FOLLOWUP_WORD_TO_VALUE.keys())


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
