"""Conservative speech hints and length policy; never an executable command plan."""
from dataclasses import dataclass
import re


_SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
_TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
_NUMBER = r'(?:\d{1,4}|(?:(?:' + '|'.join([*_SMALL, *_TENS, 'hundred', 'thousand', 'and']) + r')[ -]+)*(?:' + '|'.join([*_SMALL, *_TENS, 'hundred', 'thousand']) + r'))'
_COUNT = re.compile(r'\b(' + _NUMBER + r')\s*(?:-\s*)?(words?\b|worlds?\b)', re.I)
_QUOTED = re.compile(r'"[^"\n]*"|(?<!\w)\x27[^\x27\n]*\x27(?!\w)')


def _number(value):
    if value.isdigit():
        return int(value)
    total, part = 0, 0
    for token in re.split(r'[ -]+', value.lower().strip()):
        if token in _SMALL:
            part += _SMALL[token]
        elif token in _TENS:
            part += _TENS[token]
        elif token == 'hundred':
            part = (part or 1) * 100
        elif token == 'thousand':
            total += (part or 1) * 1000
            part = 0
    return total + part


def length_tolerance(count):
    """Ordinary lengths are targets within 5%; explicit 'exactly' is separate."""
    return count // 20


def prose_paragraphs(text):
    """Repair double-escaped paragraph separators in generated prose only."""
    return text.replace(r'\r\n\r\n', '\n\n').replace(r'\n\n', '\n\n')


def fit_near_length(text, count):
    """Shorten overlong prose using whole interior sentences only.

    Keep the opening and conclusion, all words inside each retained sentence,
    and paragraph breaks. If no acceptable whole-sentence selection exists,
    return the original for a bounded model revision; never chop a sentence.
    """
    actual, tolerance = len(text.split()), length_tolerance(count)
    if actual <= count + tolerance:
        return text
    sentences = list(re.finditer(r'\S[\s\S]*?(?:[.!?](?:["”’)]*)(?=\s|$)|$)', text))
    maximum = actual - (count - tolerance)
    choices = {0: ()}
    for index in range(1, len(sentences)-1):
        size = len(sentences[index][0].split())
        if size < 4:  # Keep short fragments/abbreviations attached to their prose.
            continue
        for removed, indices in list(choices.items()):
            value = removed + size
            candidate = indices + (index,)
            if value <= maximum and (value not in choices or len(candidate)<len(choices[value])):
                choices[value] = candidate
    feasible = [n for n in choices if abs(actual-n-count)<=tolerance]
    if not feasible:
        return text
    removed = set(choices[min(feasible,key=lambda n:(abs(actual-n-count),len(choices[n])))])
    result, previous, paragraph = '', 0, False
    for index, sentence in enumerate(sentences):
        gap = text[previous:sentence.start()]
        paragraph = paragraph or '\n' in gap
        if index not in removed:
            result += ('\n\n' if paragraph and result else ' ' if result else '') + sentence[0]
            paragraph = False
        previous = sentence.end()
    return result


@dataclass(frozen=True)
class WritingHints:
    normalized: str
    requested_words: int | None
    exact: bool
    writing: bool
    fresh_document: bool

    def context(self):
        return dict(normalized_request=self.normalized, requested_words=self.requested_words,
                    exact_word_count=self.exact, fresh_document=self.fresh_document,
                    note='Speech interpretation hints only. Preserve the raw request, negations, literal quotes and all other requested actions.')


def writing_hints(text):
    # Quoted text/paths are literal. In particular, never repair 'say' or 'save'
    # inside dictation, filenames, search queries, or a normal save instruction.
    masked = _QUOTED.sub(lambda m: ' ' * len(m[0]), text)
    writing = bool(re.search(r'\b(?:write|writing|right|compose|draft|generate)\b', masked, re.I)
                   and re.search(r'\b(?:essay|text|story|letter|document|about|words?|worlds?)\b', masked, re.I))
    if re.match(r'\s*(?:please\s+)?(?:search|find|look up|google)\b', masked, re.I):
        writing = False
    count_matches = list(_COUNT.finditer(masked)) if writing else []
    # Conflicting counts require AI clarification; do not silently pick one.
    counts = {_number(m[1]) for m in count_matches}
    requested = next(iter(counts)) if len(counts) == 1 else None
    if re.search(r'\b' + _NUMBER + r'\s+(?:or|to)\s+' + _NUMBER + r'\s+words?\b', masked, re.I):
        requested = None
    exact = bool(re.search(r'\b(?:exactly|precisely|no more than|at least|at most)\b', masked, re.I))
    normalized = text
    if writing:
        def repair(part):
            if _QUOTED.fullmatch(part):
                return part
            if requested is not None:
                part = _COUNT.sub(lambda m: str(_number(m[1])) + ' words', part)
            # These phonetic confusions occurred in accepted, confident speech.
            # Require a writing verb AND topic/length context, not a global alias.
            part = re.sub(r'\bin the right in the say\b', 'and write an essay', part, flags=re.I)
            part = re.sub(r'\b(?:write|writing|right)\s+(?:and i say you|in a saying|in me say|in the say|a say|an? safe|an? save|in s[- ]a)\b(?=\s+(?:about|for|\d))', 'write an essay', part, flags=re.I)
            return part
        pieces = re.split('(' + _QUOTED.pattern + ')', text)
        normalized = ''.join(repair(part) for part in pieces)
    opens_editor = bool(re.search(r'\b(?:open|launch|create)\b.*?\b(?:notepad|note ?book|word|document)\b', masked, re.I))
    keep_current = bool(re.search(r"\b(?:don['’]t|do not|never|without)\s+(?:open|launch|create)\b|\b(?:this|current|same|already open|existing)\s+(?:empty\s+)?(?:editor|document|window|notepad)\b", masked, re.I))
    return WritingHints(normalized, requested, exact, writing, writing and opens_editor and not keep_current)
