"""Synthesized English speech replay, unchanged Whisper model/language, no actions."""
import json
from pathlib import Path
import re
import sys

import win32com.client
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.speech import SpeechRecognizer


def main():
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    voices = voice.GetVoices()
    for index in range(voices.Count):
        if 'zira' in voices.Item(index).GetDescription().lower():
            voice.Voice = voices.Item(index)
            break
    cases = ['Close Aidy.', 'Close yourself.', 'Close everything except ChatGPT.', 'Close it.']
    recognizer = SpeechRecognizer()
    results = []
    try:
        for index, phrase in enumerate(cases):
            path = ROOT / f'.validation/aidy-name-{index}.wav'
            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Format.Type = 18
            stream.Open(str(path), 3)
            voice.AudioOutputStream = stream
            voice.Speak(phrase)
            stream.Close()
            audio = decode_audio(str(path), sampling_rate=16000)
            text = recognizer.transcribe(audio, purpose='command')
            results.append({'synthetic_phrase': phrase, 'transcript': text})
            assert text and re.search(r'\bclose\b', text), (phrase, text)
        assert re.search(r'\b(aidy|aidi|ady|ad|80|eighty|eddie)\b', results[0]['transcript']), results[0]
    finally:
        recognizer.close()
    (ROOT / '.validation/aidy-name-replay.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results))
    print('Synthesized replay only; no live microphone or desktop actions.')


if __name__ == '__main__':
    main()
