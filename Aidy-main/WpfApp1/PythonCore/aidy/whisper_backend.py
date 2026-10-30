"""Whisper with a shorter silence window, supported by CTranslate2 >= 4.8.3.

Weights and the complete recorded utterance are unchanged. SpeechRecognizer
serializes calls and retries the standard 30-second window for uncertain output.
"""
import numpy as np
from faster_whisper import WhisperModel


class CommandWhisperModel(WhisperModel):
    supports_short_windows = True
    encoder_frames = 3000

    def warm_up(self, encoder_frames=1500):
        """Prepare VAD, encoder and decoder before the microphone becomes ready."""
        from faster_whisper.vad import get_vad_model
        get_vad_model()(np.zeros(512, np.float32))
        self.encoder_frames = encoder_frames
        segments, _ = self.transcribe(
            np.zeros(16000, np.float32), language='en', beam_size=1,
            vad_filter=False, without_timestamps=True, max_new_tokens=1,
            condition_on_previous_text=False)
        list(segments)  # Lazy inference; the warm-up transcript is never used.

    def encode(self, features):
        frames = max(2, min(3000, int(self.encoder_frames)))
        return super().encode(np.ascontiguousarray(features[..., :frames]))
