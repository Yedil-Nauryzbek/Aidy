"""Generate the clean bundled confirmation with the app's Windows English voice.

No voice effects, pitch shifting or time stretching. Requires Windows SAPI/Zira.
"""
from pathlib import Path
import sys
import wave

import numpy as np
import win32com.client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.voice import _wav_speech_bounds


def main():
    output = ROOT / '.validation/confirmation-clean-source.wav'
    output.parent.mkdir(exist_ok=True)
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    voice.Voice = next(token for token in voice.GetVoices()
                       if 'zira' in token.GetDescription().lower())
    voice.Rate = 1
    voice.Volume = 100
    stream = win32com.client.Dispatch('SAPI.SpFileStream')
    stream.Format.Type = 18  # 16 kHz, mono, 16-bit PCM.
    stream.Open(str(output), 3)
    try:
        voice.AudioOutputStream = stream
        voice.Speak('Are you sure?')
    finally:
        stream.Close()
    stat = output.stat()
    start, end = _wav_speech_bounds(str(output), stat.st_mtime_ns, stat.st_size)
    with wave.open(str(output), 'rb') as source:
        params = source.getparams()
        source.setpos(start * source.getframerate() // 1000)
        samples = np.frombuffer(source.readframes((end - start) * source.getframerate() // 1000),
                                np.int16).astype(np.float32)
    # Match the level of the other short replies without compressing/clipping.
    peak = float(np.max(np.abs(samples)))
    samples *= min(2.0, .60 * 32767 / max(peak, 1))
    target = ROOT / 'Assets/voice/are_you_sure_clean.wav'
    with wave.open(str(target), 'wb') as result:
        result.setparams(params)
        result.writeframes(np.rint(samples).astype(np.int16).tobytes())
    print(f'{target}: {samples.size / params.framerate:.3f}s, {voice.Voice.GetDescription()}')


if __name__ == '__main__':
    main()
