import re


_TIME_RE = re.compile(
    r"\b(?:через|in|after)\s+(\d{1,4})\s*(секунд|секунды|сек|с|seconds|second|sec|минут|минуты|минута|мин|m|min|minutes|minute)\b",
    re.IGNORECASE,
)


def parse_delay_request(text: str) -> tuple[str, int] | None:
    if not text:
        return None
    m = _TIME_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    is_minutes = unit.startswith(("мин", "m", "min"))
    delay_seconds = n * 60 if is_minutes else n
    if delay_seconds <= 0:
        return None
    action_text = (text[: m.start()] + " " + text[m.end():]).strip()
    action_text = " ".join(action_text.split())
    if not action_text:
        return None
    return action_text, delay_seconds

