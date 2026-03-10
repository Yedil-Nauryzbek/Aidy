import re
from typing import List, Optional, Tuple


_PREP_RE = re.compile(r"\b(?:in|after)\b", re.IGNORECASE)
_UNIT_RE = re.compile(
    r"^(seconds?|sec|s|settings|setting|setings|seting|sekends|sekend|sekkonds|minutes?|mins?|min|m)$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_NUMBER_WORDS = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "odin": 1, "odna": 1,
    "dva": 2, "dve": 2,
    "tri": 3,
    "chetyre": 4, "chetire": 4,
    "pyat": 5, "piat": 5,
    "shest": 6,
    "sem": 7,
    "vosem": 8, "vosim": 8,
    "devyat": 9, "deviat": 9,
    "desyat": 10, "desiat": 10,
}

_MINUTE_UNITS = {
    "m", "min", "mins", "minute", "minutes",
    "minuta", "minuty", "minut",
}
_SECOND_UNITS = {
    "s", "sec", "secs", "second", "seconds",
    "set", "settings", "setting", "setings", "seting", "sekends", "sekend", "sekkonds",
    "sek", "sekunda", "sekundy", "sekund",
}


def _tokenize(text: str) -> List[str]:
    raw = " ".join((text or "").strip().lower().split())
    if not raw:
        return []
    return re.findall(_TOKEN_RE, raw)


def _parse_num(token: str) -> Optional[int]:
    t = (token or "").strip().lower()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    return _NUMBER_WORDS.get(t)


def _unit_to_seconds(unit: Optional[str]) -> int:
    if not unit:
        return 1
    u = unit.strip().lower()
    if u in _SECOND_UNITS or u.startswith("set") or u.startswith("sek"):
        return 1
    if u in _MINUTE_UNITS or u.startswith("m"):
        return 60
    return 1


def _extract_number(tokens: List[str]) -> Optional[Tuple[int, int]]:
    for i, tok in enumerate(tokens):
        value = _parse_num(tok)
        if value is not None:
            return value, i
    return None


def parse_timer_duration_seconds(
    text: str,
    default_unit: str = "minutes",
    max_seconds: int = 12 * 60 * 60,
) -> Optional[int]:
    tokens = _tokenize(text)
    if not tokens:
        return None

    found = _extract_number(tokens)
    if found is None:
        # Allow bare unit like "minute" as 1 minute.
        for tok in tokens:
            if tok in _MINUTE_UNITS:
                return 60
            if tok in _SECOND_UNITS:
                return 1
        return None
    value, idx = found

    unit = None
    if idx + 1 < len(tokens):
        unit = tokens[idx + 1]
    elif idx - 1 >= 0:
        unit = tokens[idx - 1]

    if unit and (unit not in _MINUTE_UNITS and unit not in _SECOND_UNITS):
        unit = None

    if unit is None:
        fallback = default_unit.strip().lower()
        multiplier = 60 if fallback.startswith("min") else 1
    else:
        multiplier = _unit_to_seconds(unit)

    delay_seconds = int(value) * int(multiplier)
    if delay_seconds <= 0 or delay_seconds > int(max_seconds):
        return None
    return delay_seconds


def parse_delay_request(text: str) -> Optional[Tuple[str, int]]:
    if not text:
        return None

    tokens = _tokenize(text)
    if len(tokens) < 2:
        return None

    # Pattern A: "in/after 30 sec open chrome"
    for i, tok in enumerate(tokens):
        if not _PREP_RE.match(tok):
            continue
        if i + 1 >= len(tokens):
            continue
        n = _parse_num(tokens[i + 1])
        if n is None:
            continue
        unit = tokens[i + 2] if (i + 2 < len(tokens) and _UNIT_RE.match(tokens[i + 2])) else None
        delay_seconds = n * _unit_to_seconds(unit)
        if delay_seconds <= 0:
            return None
        skip_to = i + (3 if unit else 2)
        action_tokens = tokens[:i] + tokens[skip_to:]
        action_text = " ".join(action_tokens).strip()
        if not action_text:
            return None
        return action_text, delay_seconds

    # Pattern B: "open chrome 30 sec" or "open chrome 30"
    unit = None
    n_idx = len(tokens) - 1
    if _UNIT_RE.match(tokens[-1]):
        unit = tokens[-1]
        n_idx = len(tokens) - 2
        if n_idx < 0:
            return None
    n = _parse_num(tokens[n_idx])
    if n is None:
        return None
    delay_seconds = n * _unit_to_seconds(unit)
    if delay_seconds <= 0:
        return None

    action_tokens = tokens[:n_idx]
    action_text = " ".join(action_tokens).strip()
    if not action_text:
        return None
    return action_text, delay_seconds
