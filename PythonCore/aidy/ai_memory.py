"""What the user asked Aidy to remember (assistant core, stage 3, 2026-11-26).

One local JSON file in %LOCALAPPDATA%\\Aidy\\Data; nothing is stored without a request and no
secret is ever stored. Notes belong to a speaker: with Voice ID off that is the local Admin
('local'); an Admin profile also sees the local notes, a Guest has none.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import re

from .ai_tools import AIError

MAX_TEXT = 300
MAX_NOTES = 50      # per owner
CONTEXT_NOTES = 20  # newest notes the model sees
SECRET = re.compile(
    r'\b(?:pass\s?(?:word|code|phrase)s?|passwd|pin\s?(?:code)?s?|api[ _-]?keys?|secrets?|tokens?|cvv|пароль\w*)\b'
    r'|\b(?:wi-?fi|wireless|network|router|security|licen[cs]e|product)\s+keys?\b'
    r'|\b(?:door|alarm|gate|safe|lock|access|entry|garage)\s+codes?\b'
    r'|(?:\d[ -]?){13,19}'  # a card number
    r'|\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{20,}\b', re.I)  # a key or token


def notes_context(notes):
    """The newest notes the model sees: at most CONTEXT_NOTES, about 2000 characters, oldest first."""
    chosen, size = [], 0
    for note in reversed(list(notes)[-CONTEXT_NOTES:]):
        item = {'id': note['id'], 'text': note['text']}
        size += len(json.dumps(item, ensure_ascii=False)) + 2
        if size > 2000:
            break
        chosen.append(item)
    return chosen[::-1]


def owners(identity):
    """Whose notes this speaker may read and change."""
    key, role = str(identity[0]), identity[1]
    if role == 'Guest':
        return ()
    return (key, 'local') if role == 'Admin' and key != 'local' else (key,)


class MemoryStore:
    def __init__(self, path=None):
        # AIDY_MEMORY_FILE: override, like AIDY_AI_CONFIG (Run-AidyTests points it at a temp file).
        self.path = Path(path or os.environ.get('AIDY_MEMORY_FILE') or
                         Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Aidy' / 'Data' / 'memory.json')

    def _load(self):
        if not self.path.exists():
            return {'next_id': 1, 'notes': []}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            # A hand-edited file of the wrong shape must not break every command.
            if (not isinstance(data, dict) or type(data.get('next_id')) is not int or not isinstance(data.get('notes'), list)
                    or not all(isinstance(n, dict) and type(n.get('id')) is int and isinstance(n.get('owner'), str)
                               and isinstance(n.get('text'), str) for n in data['notes'])):
                raise ValueError()
            return data
        except (OSError, ValueError):
            raise AIError('My memory file is unreadable; I left it untouched.') from None

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'{self.path.name}.{os.getpid()}.tmp')  # one per process
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())  # a power loss must not leave an empty memory file
        os.replace(temporary, self.path)

    def add(self, owner, text, said=''):
        """said: the user's own words this turn; the model's note may have dropped "password"."""
        text = ' '.join(str(text).split())
        if not text:
            raise AIError('What should I remember?')
        if len(text) > MAX_TEXT:
            raise AIError('That is too long to remember. Say it in one short sentence.')
        if SECRET.search(text) or SECRET.search(str(said)):
            raise AIError("I don't store passwords or keys.")
        data = self._load()
        if sum(note['owner'] == owner for note in data['notes']) >= MAX_NOTES:
            raise AIError('My memory is full. Ask me to forget something first.')
        note = dict(id=data['next_id'], owner=owner, text=text, created=datetime.now().strftime('%Y-%m-%d %H:%M'))
        data['notes'].append(note)
        data['next_id'] += 1
        self._save(data)
        return note

    def items(self, owner_keys):
        try:
            notes = self._load()['notes']
        except AIError:
            return []  # an unreadable file must not break ordinary commands
        return [{k: note[k] for k in ('id', 'text', 'created')} for note in notes if note['owner'] in owner_keys]

    def forget(self, owner_keys, note_id):
        """The removed note (so Aidy can say which one), or None."""
        data = self._load()
        removed = next((n for n in data['notes'] if n['id'] == note_id and n['owner'] in owner_keys), None)
        if removed:
            self._save(dict(data, notes=[n for n in data['notes'] if n is not removed]))
        return removed

    def forget_all(self, owner_keys):
        data = self._load()
        kept = [n for n in data['notes'] if n['owner'] not in owner_keys]
        if len(kept) != len(data['notes']):
            self._save(dict(data, notes=kept))
        return len(data['notes']) - len(kept)
