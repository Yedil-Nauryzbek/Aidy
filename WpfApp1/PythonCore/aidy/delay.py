import re


_PREP_RE = re.compile(r"\b(?:in|after)\b", re.IGNORECASE)
_UNIT_RE = re.compile(
    r"^(seconds?|sec|s|settings|setting|setings|seting|sekends|sekend|sekkonds|minutes?|mins?|min|m)$",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
}


def _parse_num(token: str) -> int | None:
    t = (token or "").strip().lower()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    return _NUMBER_WORDS.get(t)


def _unit_to_seconds(unit: str | None) -> int:
    if not unit:
        return 1
    u = unit.strip().lower()
    if u.startswith("set") or u.startswith("sek"):
        return 1
    if u.startswith("m"):
        return 60
    return 1


def parse_delay_request(text: str) -> tuple[str, int] | None:
    if not text:
        return None

    raw = " ".join((text or "").strip().lower().split())
    if not raw:
        return None

    tokens = re.findall(r"[a-z0-9]+", raw)
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
