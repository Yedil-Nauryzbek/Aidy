API_URL = "http://127.0.0.1:8008/predict"

# "Hey" starts a bounded command window; it never performs an action by itself.
# Name spellings reflect the English transcripts of spoken "Aidy" / "Эйди".
# Ordinary numbers, partial recognition, and unrelated greetings cannot activate.
WAKE_KEYWORDS = {
    "hey", "hey i do",
    "aidy", "aidi", "ady", "eidy",
    "hey aidy", "hey aidi", "hey ady", "hey eidy", "hey eddie", "hey addie",
    "hello aidy", "hello aidi", "hi aidy", "hi aidi",
    "ok aidy", "okay aidy", "okay aidi", "yo aidy",
    "aidy assistant", "aidi assistant", "hey assistant", "hello assistant", "hey eighty", "hey 80",
    "okay assistant", "a i d y", "ay dee",
}
_AIDY_NAMES = {"aidy", "aidi", "ady", "eidy", "eddie", "eddy", "addie", "adie", "aidee",
               "ay dee", "a dee", "a d", "эйди"}
WAKE_KEYWORDS |= _AIDY_NAMES
WAKE_KEYWORDS |= {f"{prefix} {name}" for prefix in ("hey", "hi", "hello", "ok", "okay", "yo")
                  for name in _AIDY_NAMES}
WAKE_KEYWORDS |= {f"{prefix} {name}" for prefix in ("hey", "hi", "hello", "ok", "okay")
                  for name in ("80", "eighty", "ad")}


def is_wake_phrase(text: str) -> bool:
    from .activation import match_wake
    return match_wake(text, WAKE_KEYWORDS)[0]


SAMPLE_RATE = 16000
CHUNK_SAMPLES = 320
VAD_SILENCE_MS = 700
VAD_MIN_SPEECH_MS = 200
WAKE_SILENCE_MS = 120
WAKE_PREVIEW_SILENCE_MS = 40

TIMER_MAX_SECONDS = 12 * 60 * 60
TIMER_START_PHRASES = {
    "timer",
    "set timer",
    "set a timer",
    "set timer for",
    "set a timer for",
    "start timer",
    "start a timer",
    "start timer for",
    "put timer",
    "timer please",
    "tamer",
    "set tamer",
    "start tamer",
    "taymer",
    "set taymer",
    "start taymer",
    "time",
    "set time",
    "start time",
    "поставь таймер",
    "таймер",
    "postav taymer",
    "stav taymer",
}
TIMER_CANCEL_PHRASES = {
    "cancel timer",
    "stop timer",
    "clear timer",
    "timer off",
    "cancel the timer",
    "cancel tamer",
    "stop tamer",
    "clear tamer",
    "tamer off",
    "cancel taymer",
    "stop taymer",
    "clear taymer",
    "taymer off",
    "отмени таймер",
    "стоп таймер",
    "otmena taymera",
    "stop taymer",
}

STUDY_MODE_DIRECT_START_PHRASES = {
    "study mode",
    "start study mode",
    "start the study mode",
    "begin study mode",
    "enter study mode",
    "study mode on",
    "study mood",
    "study mod",
    "stady mode",
    "studi mode",
    "stadi mode",
}

STUDY_MODE_CONFIRM_START_PHRASES = {
    "study",
    "start study",
    "study please",
    "study now",
    "stady",
    "studi",
    "stadi",
    "study more",
    "study remote",
    "study remoat",
    "study mor",
    "open this court",
}

STUDY_MODE_START_PHRASES = (
    STUDY_MODE_DIRECT_START_PHRASES
    | STUDY_MODE_CONFIRM_START_PHRASES
)

STUDY_MODE_START_ALIASES = {
    "study mode please",
    "start the study",
    "study maude",
    "study moat",
    "study moth",
    "study mod on",
    "study mood on",
}

STUDY_MODE_STOP_PHRASES = {
    "stop",
    "finish",
    "end",
    "stop study",
    "stop study mode",
    "finish study",
    "finish study mode",
    "end study",
    "end study mode",
}

STUDY_MODE_STATUS_PHRASES = {
    "study status",
    "study mode status",
    "how much time left",
    "how much time is left",
    "time left",
    "time remaining",
    "how much study time left",
}


DANGEROUS_INTENTS = {"shutdown", "restart"}

CONFIRM_YES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "proceed",
    "confirm", "conferm", "confim", "confirmm",
    "this", "zis", "dis"
}
CONFIRM_NO = {
    "no", "nope", "nah", "no sir", "cancel", "stop", "dont", "don't", "do not", "never mind", "abort"
}
CONFIRM_GRAMMAR_PHRASES = sorted(CONFIRM_YES | CONFIRM_NO)

REPEAT_PHRASES = {
    "repeat",
    "please repeat",
    "repeat that",
    "please repeat that",
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
    "i'm not certain",
    "im not certain",
    "i am not certain",
    "not certain",
    "aim not certain",
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
# Whisper can hear a short "done" as "down". This alias is scoped to the
# Alt-Tab dialogue; it must not change ordinary commands such as volume down.
WINDOW_SWITCH_DONE = {"done", "down", "select", "choose", "ok", "finished", "finish", "completed", "okay"}
WINDOW_SWITCH_CANCEL = {"cancel", "stop", "exit"}
WINDOW_SWITCH_GRAMMAR = sorted(
    WINDOW_SWITCH_LEFT | WINDOW_SWITCH_RIGHT | WINDOW_SWITCH_DONE | WINDOW_SWITCH_CANCEL
)

REPEAT_LAST_STEPS = False
FOLLOW_MODE_ENABLED = False
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
        "number 1", "num 1", "1 step", "step 1", "step one", "one more", "just one",
        "first", "odin", "adin", "odyn", "adyn",
    ],
    2: [
        "2", "two", "too", "to", "tu", "tuu", "twoo", "tow", "tew", "number two",
        "num two", "two step", "too step", "to step", "two please",
        "number 2", "num 2", "2 step", "step 2", "step two", "two more", "just two",
        "second", "dva", "dua", "dwa",
    ],
    3: [
        "3", "three", "tree", "threee", "thre", "thri", "thry", "free", "sree", "number three",
        "num three", "three step", "tree step", "three please", "thri step",
        "number 3", "num 3", "3 step", "step 3", "step three", "three more", "just three",
        "third", "tri",
    ],
    4: [
        "4", "four", "for", "fore", "foor", "fourr", "fur", "phor", "foar", "number four",
        "num four", "four step", "for step", "four please", "fore step",
        "number 4", "num 4", "4 step", "step 4", "step four", "four more", "just four",
        "fourth", "chetyre", "chetire", "chitirye", "chityre",
    ],
    5: [
        "5", "five", "fiv", "fife", "faiv", "faeve", "fyve", "fibe", "hive", "number five",
        "num five", "five step", "fife step", "five please", "faiv step",
        "number 5", "num 5", "5 step", "step 5", "step five", "five more", "just five",
        "fifth", "pyat", "piat",
    ],
    6: [
        "6", "six", "sics", "sic", "sik", "seeks", "sikx", "sex", "sicks", "number six",
        "num six", "six step", "sik step", "six please", "sics step",
        "number 6", "num 6", "6 step", "step 6", "step six", "six more", "just six",
        "sixth", "shest",
    ],
    7: [
        "7", "seven", "sevan", "siven", "sevun", "seben", "zeven", "savin", "sevin", "number seven",
        "num seven", "seven step", "seven please", "sevun step", "siven step",
        "number 7", "num 7", "7 step", "step 7", "step seven", "seven more", "just seven",
        "seventh", "sem",
    ],
    8: [
        "8", "eight", "ate", "aight", "eit", "eyt", "ait", "eigh", "eightt", "number eight",
        "num eight", "eight step", "ate step", "eight please", "aight step",
        "number 8", "num 8", "8 step", "step 8", "step eight", "eight more", "just eight",
        "eighth", "vosem", "vosim",
    ],
    9: [
        "9", "nine", "nain", "nyne", "naine", "nein", "nien", "nayn", "number nine", "num nine",
        "nine step", "nain step", "nine please", "nyne step", "nayn step",
        "number 9", "num 9", "9 step", "step 9", "step nine", "nine more", "just nine",
        "ninth", "devyat", "deviat",
    ],
    10: [
        "10", "ten", "tin", "tenn", "tehn", "tane", "den", "then", "number ten", "num ten",
        "ten step", "tin step", "ten please", "tehn step", "then step",
        "number 10", "num 10", "10 step", "step 10", "step ten", "ten more", "just ten",
        "tenth", "desyat", "desiat", "deseat",
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

MOUSE_COMMAND_PHRASES = {
    "mouse up", "mouse down", "mouse left", "mouse right",
    "move up", "move down", "move left", "move right",
    "cursor up", "cursor down", "cursor left", "cursor right",
    "scroll up", "scroll down", "scroll top", "scroll bottom",
    "click", "left click", "mouse click",
    "double click", "double",
    "right click", "right",
}

CLOSE_EVERYTHING_PHRASES = {
    "close everything", "close all", "close all apps", "close all applications",
    "close everything please",
}

LOCAL_MODE_ON_PHRASES = {
    "local mode",
    "local mode on",
    "enable local mode",
    "turn on local mode",
    "start local mode",
    "local mod",
    "local mold",
    "locale mode",
}

LOCAL_MODE_OFF_PHRASES = {
    "local mode off",
    "disable local mode",
    "turn off local mode",
    "stop local mode",
    "exit local mode",
}

CUSTOM_MODE_ON_PHRASES = {
    "custom mode",
    "custom mode on",
    "enable custom mode",
    "turn on custom mode",
    "start custom mode",
    "custom mod",
    "custom mold",
    "custom mode please",
    # synonyms
    "personal mode",
    "personal mode on",
    "enable personal mode",
    "turn on personal mode",
    "start personal mode",
    "local mode",
    "local mode on",
    "enable local mode",
    "turn on local mode",
    "start local mode",
    "my mode",
    "my mode on",
    "start my mode",
    "quick mode",
    "quick mode on",
    "start quick mode",
}

CUSTOM_MODE_OFF_PHRASES = {
    "custom mode off",
    "disable custom mode",
    "turn off custom mode",
    "stop custom mode",
    "exit custom mode",
    # synonyms
    "personal mode off",
    "disable personal mode",
    "turn off personal mode",
    "stop personal mode",
    "exit personal mode",
    "local mode off",
    "disable local mode",
    "turn off local mode",
    "stop local mode",
    "exit local mode",
    "my mode off",
    "stop my mode",
    "exit my mode",
    "quick mode off",
    "stop quick mode",
    "exit quick mode",
}
