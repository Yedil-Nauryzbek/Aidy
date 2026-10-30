"""Local English speech benchmark. Synthesized files only; never executes commands."""
import json
from pathlib import Path
import sys
import time

import numpy as np
import win32com.client
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PythonCore"))
from aidy.speech import SpeechRecognizer
from aidy.activation import match_wake
from aidy.config import WAKE_KEYWORDS, VAD_SILENCE_MS
from aidy.audio_capture import UtteranceBuffer, FRAME_BYTES


def main():
    output = ROOT / ".validation"
    output.mkdir(exist_ok=True)
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voices = voice.GetVoices()
    for index in range(voices.Count):
        candidate = voices.Item(index)
        if "zira" in candidate.GetDescription().lower():
            voice.Voice = candidate
            break
    phrases = ["Hey.", "Hey, open browser.", "Hey Aidy, open browser.", "Hey assistant, open browser.",
               "Open browser.", "Yes.", "Five.", "The weather is pleasant today.",
               "Close Steam.", "Open Steam.", "Close Discord.", "Open Discord.",
               "Don't open Steam.", "Please close Steam.",
               "Aidy.", "Ay dee.", "Hi Aidy.", "Hello Aidy.", "Okay Aidy.",
               "Yo Aidy.", "Hey Aidy, close Steam.", "Aidy, don't open Steam.",
               "Hello assistant.", "Okay assistant.", "Aidy assistant.",
               "Hi.", "Okay.", "Hello there.", "Eighty.", "This is a test.",
               "Please do not close Discord."]
    wake_phrases = set(phrases[:4] + phrases[14:25])
    files = []
    for index, phrase in enumerate(phrases):
        path = output / f"synthesized-{index}.wav"
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 18  # SAPI 16 kHz, 16-bit mono.
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(phrase)
        stream.Close()
        files.append(path)
    recognizer = SpeechRecognizer()
    results = []
    try:
        for phrase, path in zip(phrases, files):
            samples = decode_audio(str(path), sampling_rate=16000)
            samples = np.concatenate([samples, np.zeros(16000, np.float32)])
            pcm = (samples * 32767).astype(np.int16).tobytes()
            collector = UtteranceBuffer(silence_ms=VAD_SILENCE_MS)
            utterances = []
            for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                if collector.feed(pcm[offset:offset + FRAME_BYTES]):
                    audio = collector.finish()
                    if audio is not None:
                        utterances.append(audio)
                    collector = UtteranceBuffer(silence_ms=VAD_SILENCE_MS)
            started = time.perf_counter()
            is_wake = phrase in wake_phrases
            purpose = "wake" if is_wake or phrase in phrases[25:30] else "command"
            transcripts = [recognizer.transcribe(audio, purpose=purpose if i == 0 else "command")
                           for i, audio in enumerate(utterances)]
            text = " ".join(filter(None, transcripts))
            row = {"input": phrase, "text": text, "seconds": round(time.perf_counter() - started, 3),
                   "capture_accepted": bool(utterances), "utterances": len(utterances),
                   "wake": match_wake(text, WAKE_KEYWORDS)[0]}
            # SAPI pronounces "eighty" almost identically to Aidy. Record this
            # ambiguity instead of claiming a wake detector can distinguish it.
            if phrase == "Eighty.":
                row["name_homophone"] = True
            results.append(row)
            print(json.dumps(row), flush=True)
            if is_wake:
                assert row["wake"], f"Missed activation: {row}"
                if phrase == "Hey Aidy, close Steam.":
                    assert match_wake(text, WAKE_KEYWORDS)[1] == "close steam", row
                if phrase == "Aidy, don't open Steam.":
                    assert match_wake(text, WAKE_KEYWORDS)[1] == "don't open steam", row
            elif phrase in phrases[25:30] and phrase != "Eighty.":
                assert not row["wake"], f"False activation: {row}"
            elif phrase in {"Close Steam.", "Open Steam.", "Close Discord.", "Open Discord.", "Open browser."}:
                assert text == phrase.lower().rstrip('.'), f"Wrong command: {row}"
            elif phrase == "Don't open Steam.":
                assert text == "don't open steam", f"Negation lost: {row}"
            elif phrase == "Please do not close Discord.":
                assert text == "please do not close discord", f"Negation lost: {row}"
            elif phrase == "Yes.":
                assert text == 'yes', row
            elif phrase == "Five.":
                assert text in {'five', '5'}, row
        for name, audio in [("silence", np.zeros(16000, np.float32)),
                            ("white_noise", np.random.default_rng(7).normal(0, .01, 16000).astype(np.float32))]:
            text = recognizer.transcribe(audio)
            results.append({"input": name, "text": text})
            assert not text, f"Unexpected transcription of {name}: {text}"
    finally:
        recognizer.close()
    (output / "speech-benchmark.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
