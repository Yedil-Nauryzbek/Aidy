API_URL = "http://127.0.0.1:8008/predict"

WAKE_KEYWORDS = {
    "aidy",
    "aidy assistant",
    "aidy ai",
    "aidy assitant",
    "aidi assistant",
    "ady",
    "adi",
    "aidi",
    "aidyy",
    "aidyy assistant",
    "eidy",
    "edy",
    "edi",
    "aiddy",
    "a i d y",
    "a d i",
    "a d y",
    "ai di",
    "ay dee",
    "эй ди",
    "hey aidy",
    "hey ady",
    "hey aidi",
    "hey adi",
    "hey aidi assistant",
    "hey eidy",
    "hey edy",
    "hey edi",
    "hi aidy",
    "hi ady",
    "hi eidy",
    "hey assistant",
    "hello assistant",
    "hello aidy",
    "hello ady",
    "hello eidy",
    "yo aidy",
    "ok ady",
    "ok aidi",
    "ok aidy",
    "okay aidy",
    "okay ady",
    "okay aidi",
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
    "эйди",
    "эйди ассистент",
    "эйди помощник",
    "эй ди",
    "эди",
    "эйди",
    "эйдии",
    "хей эйди",
    "хэй эйди",
    "привет эйди",
    "эйди слушай",
}

WAKE_FUZZY_ALIASES = {
    "well im",
    "while im",
    "will im",
    "well im up",
    "while im up",
    "will im up",
    "willem",
    "william",
    "ady",
    "aidy",
    "aidi",
    "eidy",
    "edi",
    "addy",
    "edy",
    "эйди",
    "эди",
}

def is_wake_phrase(text: str) -> bool:
    t = (text or "").lower().strip()
    t = " ".join(t.split())
    if len(t) < 3:
        return False

    strong_single_wake = {
        "aidy",
        "ady",
        "adi",
        "aidi",
        "aidyy",
        "eidy",
        "edy",
        "edi",
        "aiddy",
        "eddie",
        "eighty",
        "эйди",
        "эди",
        "эйди",
    }
    greetings = {"hey", "hello", "ok", "okay", "hi", "хей", "хэй"}

    if t in WAKE_KEYWORDS:
        if " " not in t and t not in strong_single_wake:
            return False
        return True

    compact = t.replace(" ", "")
    for w in WAKE_KEYWORDS:
        wc = w.replace(" ", "")
        if wc and compact == wc:
            if " " not in w and w not in strong_single_wake:
                return False
            return True

    words = t.split()
    if len(words) >= 2 and words[0] in greetings and words[1] in strong_single_wake:
        return True

    if len(words) == 1 and words[0] in strong_single_wake:
        return True

    for w in WAKE_KEYWORDS:
        if " " in w and w in t:
            return True

    return False


SAMPLE_RATE = 16000
CHUNK_SAMPLES = 800
WAKE_CHUNK_SAMPLES = 400
WAKE_DETECT_PARTIAL = True
WAKE_REPLY_DEAFEN_MS = 220
FRAME_MS = 250
VAD_START_THRESHOLD = 140
VAD_SILENCE_MS = 700
VAD_MIN_SPEECH_MS = 140

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
WINDOW_SWITCH_DONE = {"done", "select", "choose", "ok"}
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
