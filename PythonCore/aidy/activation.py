"""Strict activation of finalized, speech-validated English utterances.

This gate accepts finalized, confidence-checked Whisper transcripts.
Raw sound levels and partial words cannot activate it.
"""
import re


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]|_", " ", (text or "").lower()).split())


def match_wake(text: str, phrases) -> tuple[bool, str]:
    text = (text or "").lower().strip()
    tokens = list(re.finditer(r"[^\W_]+", text))
    words = [token.group() for token in tokens]
    for phrase in sorted({normalize(p) for p in phrases}, key=len, reverse=True):
        if not phrase:
            continue
        expected = phrase.split()
        if words[:len(expected)] == expected:
            # Preserve the command's apostrophes, decimals and URLs. Normalizing
            # the entire phrase could turn "don't" into an actionable command.
            tail = text[tokens[len(expected) - 1].end():].lstrip(" \t,.:;!?-")
            return True, " ".join(tail.rstrip(".!?,;").split())
    return False, ""
