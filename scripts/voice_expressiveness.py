"""Compare candidate Jarvis-style voice blends by intonation range.

Renders the same conversational sentences for each candidate to
.validation/voice_sweep/<name>.wav and prints the median pitch variation
(F0 standard deviation in semitones, raw Kokoro output before effects) and
the total duration. More pitch variation = livelier intonation.
"""
from pathlib import Path
import sys

import numpy as np
import pyworld

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.voice_style import DEFAULT_STYLE, VOICE_PRESETS, NeuralSpeaker, treated_audio, trim_silence, wave_bytes

SENTENCES = (
    'Good evening. Everything is running smoothly, and your calendar is clear for the rest of the day.',
    'Would you like me to open your project folder, or shall we start with the news?',
    "Ah, that's brilliant news! Congratulations, truly well done.",
    "Of course. Take your time, I'll be right here when you need me.",
    "I'm afraid the battery is getting rather low. You might want to plug it in soon.",
    'Today the dollar is at about five hundred and twelve tenge, slightly up from yesterday.',
)
CANDIDATES = {
    'jarvis': (VOICE_PRESETS['jarvis']['blend'], VOICE_PRESETS['jarvis']['effect_strength']),
    'jarvis_clean': ({'bm_lewis': 1.0}, .12),
    'lewis_george': ({'bm_lewis': .5, 'bm_george': .5}, .12),
    'lewis_fable': ({'bm_lewis': .5, 'bm_fable': .5}, .12),
    'george': ({'bm_george': 1.0}, .12),
    'fable_george': ({'bm_fable': .5, 'bm_george': .5}, .12),
}


def pitch_variation(samples, rate):
    audio = np.asarray(samples, dtype=np.float64)
    f0, times = pyworld.dio(audio, rate, frame_period=10)
    f0 = pyworld.stonemask(audio, f0, times, rate)
    voiced = f0[f0 > 0]
    return float(np.std(12 * np.log2(voiced / np.median(voiced)))) if voiced.size > 10 else 0.0


def main():
    out = ROOT / '.validation/voice_sweep'
    out.mkdir(parents=True, exist_ok=True)
    speaker = NeuralSpeaker(dict(DEFAULT_STYLE, lang='en-gb', speed=1.15))
    print(f'{"candidate":<14} {"F0 std (st)":>12} {"seconds":>8}')
    for name, (blend, effect) in CANDIDATES.items():
        voice = sum(weight * speaker.engine.get_voice_style(v) for v, weight in blend.items()).astype(np.float32)
        variations, parts, rate = [], [], 24000
        for text in SENTENCES:
            samples, rate = speaker.engine.create(text, voice=voice, speed=1.15, lang='en-gb',
                                                  sentence_pause=.20, clause_pause=.075)
            variations.append(pitch_variation(samples, rate))
            parts += [treated_audio(trim_silence(samples, rate), rate, effect), np.zeros(int(.35 * rate), np.float32)]
        audio = np.concatenate(parts)
        (out / (name + '.wav')).write_bytes(wave_bytes(audio, rate))
        print(f'{name:<14} {np.median(variations):>12.2f} {audio.size / rate:>8.1f}', flush=True)
    print('Previews: ' + str(out))


if __name__ == '__main__':
    main()
