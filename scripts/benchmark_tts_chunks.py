"""How soon speech starts and how long it pauses between chunks (no sound is played).

Mirrors Voice._speak_neural: a worker renders the next chunk while the current
one "plays" (sleep for its WAV duration). Stage 1 target (2026-11-25): first
chunk ready within 1.5 s and no gap over 0.7 s in at least 9 of 10 phrases.
"""
import inspect
import io
from pathlib import Path
import sys
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.voice_style import NeuralSpeaker, load_profile  # noqa: E402

PHRASES = [
    'Certainly. Your report is in the Downloads folder.',
    'Ah, very well, thank you. And how are you?',
    'You have ten new messages and two of them look important.',
    'Right, the timer is set for ten minutes.',
    "I couldn't do that. The file is larger than sixty four kilobytes.",
    'Of course. Why did the robot go on holiday? It needed to recharge its batteries.',
    'Done. The document was saved to your desktop as notes.txt.',
    "Well, it depends on what you need it for, but I'd start with the smaller model.",
    'Search request sent.',
    'The page explains how to install Python on Windows, step by step, with screenshots.',
]


def seconds(data):
    with wave.open(io.BytesIO(data)) as clip:
        return clip.getnframes() / clip.getframerate()


def speak(speaker, text):
    """(chunks, first chunk ready s, max gap s) for one phrase."""
    chunks = iter(speaker.chunks(text))
    end = object()
    box = {}

    def render():
        box['data'] = next(chunks, end)

    start = time.perf_counter()
    worker = threading.Thread(target=render)
    worker.start()
    first, gap, count = None, 0.0, 0
    while True:
        waited = time.perf_counter()
        worker.join()
        data = box['data']
        if data is end:
            return count, first, gap
        now = time.perf_counter()
        if first is None:
            first = now - start
        else:
            gap = max(gap, now - waited)
        count += 1
        worker = threading.Thread(target=render)
        worker.start()
        time.sleep(seconds(data))  # "playback" while the next chunk renders


def main():
    voice_dir = ROOT / 'Assets/voice_jarvis'
    profile = load_profile(voice_dir)
    options = {'clip_dir': str(voice_dir)} if 'clip_dir' in inspect.signature(NeuralSpeaker).parameters else {}
    speaker = NeuralSpeaker(profile, **options)
    speaker.render('Warm up run.')
    print('clips:', 'on' if options else 'unsupported')
    good_first = good_gap = 0
    for text in PHRASES:
        speaker.cache.clear()
        speaker.cache_bytes = 0
        count, first, gap = speak(speaker, text)
        good_first += first <= 1.5
        good_gap += gap <= 0.7
        print(f'chunks={count} first={first:.2f}s max_gap={gap:.2f}s  {text[:60]}', flush=True)
    print(f'first<=1.5s: {good_first}/{len(PHRASES)}  max_gap<=0.7s: {good_gap}/{len(PHRASES)}')


if __name__ == '__main__':
    main()
