"""Compare Whisper command models on synthetic spoken commands.

Kokoro renders each phrase with several voices, including non-US voice
embeddings as an accent proxy, plus light room noise. This ranks models
relative to each other; it does not replace a live microphone test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'PythonCore'))

PHRASES = [
    'open steam', 'open discord', 'close steam', 'open the recycle bin',
    'open the whatsapp app and then open the whatsapp website',
    'open notepad and write an essay about space in six hundred words',
    'can you open the command prompt', 'open bluetooth settings',
    'turn the volume up by three', 'set a timer for five minutes',
    'open youtube and search for lofi music', 'go to github dot com',
    'open the calculator and calculate twenty five times four',
    'open my downloads folder', 'uninstall zoom workplace',
    'open telegram and spotify', 'open tlauncher to play minecraft',
    'close everything except chatgpt', 'lock the computer', 'take a screenshot',
]
VOICES = ['am_adam', 'af_heart', 'bm_george', 'hm_omega', 'em_alex']
MODELS = ['Systran/faster-whisper-tiny.en', 'Systran/faster-whisper-base.en', 'Systran/faster-whisper-small.en']
NUMBERS = {'six hundred': '600', 'twenty five': '25', 'five': '5', 'three': '3', 'four': '4', 'dot com': '.com'}


def words(text):
    text = text.lower()
    for spoken, digits in NUMBERS.items():
        text = text.replace(spoken, digits)
    text = text.replace('cmd', 'command prompt').replace('what\'s app', 'whatsapp').replace('whats app', 'whatsapp')
    text = text.replace('lo-fi', 'lofi').replace('lo fi', 'lofi').replace('t-launcher', 'tlauncher').replace('t launcher', 'tlauncher')
    text = text.replace('chat gpt', 'chatgpt').replace('github.com', 'github .com')
    return re.sub(r"[^a-z0-9.' ]", ' ', text).replace(' .com', '.com').split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j-1] + 1, prev + (r[i-1] != h[j-1]))
    return d[len(h)] / max(1, len(r))


def render(cache):
    import onnxruntime as ort
    import soxr
    from kokoro_onnx import Kokoro
    from aidy.voice_style import model_directory
    if cache.exists():
        data = np.load(cache)
        return {key: data[key] for key in data.files}
    directory = model_directory()
    engine = Kokoro.from_session(ort.InferenceSession(str(directory/'kokoro-v1.0.onnx'), providers=['CPUExecutionProvider']),
                                 str(directory/'voices-v1.0.bin'))
    rng = np.random.default_rng(7)
    clips = {}
    for v, voice in enumerate(VOICES):
        for p, phrase in enumerate(PHRASES):
            samples, rate = engine.create(phrase, voice=voice, speed=1.0, lang='en-us')
            audio = soxr.resample(np.asarray(samples, np.float32), rate, 16000)
            audio = audio / max(1e-6, float(np.max(np.abs(audio)))) * .5
            noise = rng.normal(0, 1, audio.size).astype(np.float32)
            noise *= float(np.sqrt(np.mean(audio ** 2))) / 10 ** (20 / 20)  # 20 dB SNR
            clips[f'{v}_{p}'] = np.concatenate([np.zeros(4000, np.float32), audio + noise, np.zeros(4000, np.float32)]).astype(np.float32)
    cache.parent.mkdir(exist_ok=True)
    np.savez_compressed(cache, **clips)
    return clips


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', action='append', default=[])
    parser.add_argument('--beam', type=int, default=None)
    options = parser.parse_args()
    clips = render(ROOT/'.validation/asr-clips.npz')
    summary = []
    for model in options.model or MODELS:
        os.environ['AIDY_WHISPER_MODEL'] = model
        if options.beam:
            os.environ['AIDY_WHISPER_BEAM_SIZE'] = str(options.beam)
        from aidy.speech import SpeechRecognizer
        recognizer = SpeechRecognizer()
        rows = []
        try:
            for key, audio in clips.items():
                v, p = map(int, key.split('_'))
                started = time.perf_counter()
                text = recognizer.transcribe(audio, purpose='command') or ''
                rows.append(dict(voice=VOICES[v], ref=PHRASES[p], hyp=text, wer=wer(PHRASES[p], text),
                                 ms=(time.perf_counter() - started) * 1000))
        finally:
            recognizer.close()
        item = dict(model=model, beam=recognizer.beam_size, clips=len(rows),
                    wer=round(statistics.mean(r['wer'] for r in rows), 4),
                    exact=sum(r['wer'] == 0 for r in rows), rejected=sum(not r['hyp'] for r in rows),
                    median_ms=round(statistics.median(r['ms'] for r in rows)), p95_ms=round(sorted(r['ms'] for r in rows)[int(len(rows) * .95)]),
                    by_voice={voice: round(statistics.mean(r['wer'] for r in rows if r['voice'] == voice), 3) for voice in VOICES})
        summary.append(item)
        print(json.dumps(item), flush=True)
        name = model.split('/')[-1] + (f'-beam{options.beam}' if options.beam else '')
        (ROOT/f'.validation/asr-{name}.json').write_text(json.dumps(dict(summary=item, rows=rows), indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
