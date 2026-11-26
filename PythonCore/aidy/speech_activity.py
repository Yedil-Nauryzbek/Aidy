"""Streaming speech/noise decisions using faster-whisper's bundled Silero VAD.

This detects speech activity only; Whisper remains the sole word recognizer.
The shared ONNX session is immutable; every stream owns its recurrent state.
"""
import numpy as np
from faster_whisper.vad import get_vad_model


class SpeechActivity:
    def __init__(self, session=None):
        self.session = session if session is not None else get_vad_model().session
        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.context = np.zeros((1, 64), np.float32)
        self.pending = np.empty(0, np.float32)
        self.active = False
        self.probability = 0.0

    def is_speech(self, frame, rate):
        if rate != 16000:
            raise ValueError('Speech activity expects 16 kHz PCM16')
        samples = np.frombuffer(frame, np.int16).astype(np.float32) / 32768.0
        self.pending = np.concatenate((self.pending, samples))
        while self.pending.size >= 512:
            block, self.pending = self.pending[:512], self.pending[512:]
            model_input = np.concatenate((self.context, block.reshape(1, -1)), axis=1)
            scores, self.h, self.c = self.session.run(
                None, {'input': model_input, 'h': self.h, 'c': self.c})
            self.context = block[-64:].reshape(1, -1).copy()
            self.probability = float(np.asarray(scores).reshape(-1)[-1])
            # Hysteresis retains softer word endings without letting a steady
            # fan/hum reset the endpoint timer as WebRTC's raw votes did.
            self.active = self.probability >= (.30 if self.active else .50)
        return self.active
