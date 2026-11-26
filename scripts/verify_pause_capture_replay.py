"""Real Silero/Whisper replay with an inserted pause; no microphone or actions."""
import json
from pathlib import Path
import sys

import numpy as np
import win32com.client
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.audio_capture import UtteranceBuffer, FRAME_BYTES
from aidy.speech import SpeechRecognizer


def main():
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    for candidate in voice.GetVoices():
        if 'zira' in candidate.GetDescription().lower():
            voice.Voice = candidate
            break

    def synthesize(text, index):
        path = ROOT / f'.validation/paused-part-{index}.wav'
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Format.Type = 18
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        audio = decode_audio(str(path), sampling_rate=16000)
        active = np.flatnonzero(np.abs(audio) > .012)
        assert active.size, 'No synthesized speech'
        # Remove only outside silence, keeping 30 ms around the sound. The
        # explicit 400 ms gap plus those margins models a short speaking pause.
        return audio[max(0, active[0] - 480):min(audio.size, active[-1] + 480)]

    recognizer = SpeechRecognizer()
    rows = []
    try:
        for index, (left, right, expected) in enumerate([('Open the', 'browser.', 'open the browser'),
                                                        ("Don't close", 'Steam.', "don't close steam")]):
            audio = np.concatenate([synthesize(left, index * 2), np.zeros(6400, np.float32),
                                    synthesize(right, index * 2 + 1), np.zeros(16000, np.float32)])
            pcm = (audio * 32767).astype(np.int16).tobytes()
            captures = {}
            for silence in [240, 700]:
                collector = UtteranceBuffer(silence_ms=silence)
                utterances = []
                for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                    if collector.feed(pcm[offset:offset + FRAME_BYTES]):
                        finished = collector.finish()
                        if finished is not None:
                            utterances.append(finished)
                        collector = UtteranceBuffer(silence_ms=silence)
                captures[silence] = utterances
            text = recognizer.transcribe(captures[700][0]) if len(captures[700]) == 1 else ''
            row = dict(expected=expected, inserted_pause_ms=400, old_utterances=len(captures[240]),
                       new_utterances=len(captures[700]), transcript=text,
                       passed=len(captures[700]) == 1 and text == expected)
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        recognizer.close()
    (ROOT / '.validation/pause-capture-replay.json').write_text(json.dumps(rows, indent=2))
    assert all(row['passed'] for row in rows), 'Paused speech replay regression'


if __name__ == '__main__':
    main()
