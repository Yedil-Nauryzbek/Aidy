"""Bounded utterance collection, independent from recognition and command execution."""
from collections import deque
import audioop
import queue
import threading
import time

import numpy as np
import pyaudio
from .logui import ui_audio_level
from .speech_activity import SpeechActivity


SAMPLE_RATE = 16000
FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * 2


class AudioDiscontinuityError(OSError):
    """Capture lost samples; discard this utterance and start a fresh capture."""


class AudioLevelMeter:
    """20 Hz amplitude telemetry from the existing input, independent of STT."""

    def __init__(self, emit=ui_audio_level, clock=time.monotonic):
        self.emit = emit
        self.clock = clock
        self._last_emit = float('-inf')

    def feed(self, pcm):
        now = self.clock()
        if now - self._last_emit < 0.05:
            return
        self._last_emit = now
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if not samples.size:
            return
        samples -= samples.mean()
        rms = float(np.sqrt(np.mean(samples * samples)))
        # Gate room hiss, compress louder input, keep medium-sized movement for
        # quiet speech. Visual gain never alters the audio used by recognition.
        level = min(1.0, max(0.0, (rms - 0.0035) / 0.09) ** 0.5)
        self.emit(level)

    def reset(self):
        self.emit(0.0)


class AudioLevelRelay:
    """Keep UI output and amplitude calculations off the PortAudio callback."""

    def __init__(self, meter=None):
        self._meter = meter or AudioLevelMeter()
        self._latest = deque(maxlen=1)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="aidy-levels", daemon=True)
        self._worker.start()

    def feed(self, pcm):
        if not self._stop.is_set():
            self._latest.append(pcm)

    def _run(self):
        try:
            while not self._stop.wait(0.05):
                try:
                    pcm = self._latest.pop()
                except IndexError:
                    continue
                self._meter.feed(pcm)
        except Exception:
            # Optional animation must never stop microphone capture.
            pass
        finally:
            try:
                self._meter.reset()
            except Exception:
                pass

    def close(self):
        self._stop.set()
        self._latest.clear()
        # Even a blocked UI pipe must not hold up audio shutdown/recovery.
        self._worker.join(timeout=0.1)


class BufferedInput:
    """Bounded callback capture keeps the next phrase during wake inference.

    Audio is consumed exactly once. Closing for playback/execution discards the
    queue; lost samples raise a recoverable discontinuity instead of replaying
    incomplete commands.
    """

    def __init__(self, audio, **kwargs):
        self._frames = queue.Queue(maxsize=500)
        self._closed = False
        self._overflowed = False
        self._suspended = False
        self._capture_lock = threading.Lock()
        self._rate = kwargs.get("rate", SAMPLE_RATE)
        self._level_meter = AudioLevelRelay()
        try:
            self._stream = audio.open(**kwargs, stream_callback=self._callback)
        except Exception:
            self._level_meter.close()
            raise

    def _callback(self, data, frame_count, time_info, status):
        with self._capture_lock:
            return self._capture_callback(data, frame_count, time_info, status)

    def _capture_callback(self, data, frame_count, time_info, status):
        if self._closed:
            return None, pyaudio.paComplete
        if self._suspended:
            return None, pyaudio.paContinue
        if status & pyaudio.paInputOverflow:
            self._overflowed = True
        self._level_meter.feed(data)
        try:
            self._frames.put_nowait((time.monotonic(), data))
        except queue.Full:
            self._overflowed = True
        return None, pyaudio.paContinue

    def suspend(self, *, take_pending=False):
        """Stop accepting playback audio; optionally detach the existing queue.

        Detaching is atomic with the callback and never waits for more input or
        runs VAD. Only audio captured before suspension can be returned.
        """
        with self._capture_lock:
            if take_pending:
                self.check_health()
            self._suspended = True
            pending = []
            while True:
                try:
                    frame = self._frames.get_nowait()
                except queue.Empty:
                    break
                if take_pending:
                    pending.append(frame)
            return pending

    def resume(self):
        with self._capture_lock:
            self._clear_pending()
            self._overflowed = False
            self._suspended = False

    def _clear_pending(self):
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break

    def read(self, samples, exception_on_overflow=True):
        self.check_health()
        if self._closed or self._suspended:
            return b""
        try:
            captured_at, data = self._frames.get(timeout=0.02)
        except queue.Empty:
            return b""
        if time.monotonic() - captured_at > 8:
            raise AudioDiscontinuityError("Microphone audio became stale")
        self.check_health()
        return data

    def check_health(self):
        if self._overflowed:
            raise AudioDiscontinuityError("Microphone buffer overflow; incomplete audio discarded")
        with self._frames.mutex:
            oldest = self._frames.queue[0][0] if self._frames.queue else None
        if oldest is not None and time.monotonic() - oldest > 8:
            raise AudioDiscontinuityError("Microphone audio became stale")

    def is_active(self):
        return not self._closed and self._stream.is_active()

    def has_pending_speech(self):
        """Inspect, without consuming, input received during wake inference.

        This diagnostic scan must not delay a wake acknowledgement. The wake
        path detaches the queue with suspend(take_pending=True) instead.
        """
        self.check_health()
        with self._frames.mutex:
            pending = list(self._frames.queue)
        vad = SpeechActivity()
        votes = deque(maxlen=5)
        pcm = b""
        resample_state = None
        for captured_at, data in pending:
            if time.monotonic() - captured_at > 8:
                raise AudioDiscontinuityError("Microphone audio became stale")
            if self._rate != SAMPLE_RATE:
                data, resample_state = audioop.ratecv(data, 2, 1, self._rate, SAMPLE_RATE, resample_state)
            pcm += data
            while len(pcm) >= FRAME_BYTES:
                frame, pcm = pcm[:FRAME_BYTES], pcm[FRAME_BYTES:]
                values = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(values * values)))
                speech = vad.is_speech(frame, SAMPLE_RATE)
                votes.append(rms >= 70 and speech)
                if sum(votes) >= 3:
                    return True
        return False

    def stop_stream(self):
        self._stream.stop_stream()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        finally:
            self._level_meter.close()
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break


class UtteranceBuffer:
    """Accept 20 ms PCM16 frames, retaining pre-roll and requiring actual speech."""

    def __init__(self, silence_ms=400, min_speech_ms=200, max_seconds=12, vad=None, rms_threshold=70):
        self.rms_threshold = max(20, min(1000, float(rms_threshold)))
        self.vad = vad or SpeechActivity()
        self.silence_frames = max(1, int(silence_ms / 20))
        self.min_speech_frames = max(1, int(min_speech_ms / 20))
        self.max_frames = max(1, int(max_seconds * 50))
        self.pre_roll = deque(maxlen=10)
        self.start_votes = deque(maxlen=5)
        self.frames = []
        self.voiced_frames = 0
        self.quiet_frames = 0
        self.started = False
        self.truncated = False

    def feed(self, pcm: bytes) -> bool:
        if len(pcm) != FRAME_BYTES:
            raise ValueError("Expected one 20 ms mono PCM16 frame at 16 kHz")
        values = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(values * values)))
        # Always feed silence too: the recurrent detector needs continuous audio.
        voiced = self.vad.is_speech(pcm, SAMPLE_RATE) and rms >= self.rms_threshold
        if not self.started:
            self.pre_roll.append(pcm)
            self.start_votes.append(voiced)
            if sum(self.start_votes) < 3:
                return False
            self.started = True
            self.frames = list(self.pre_roll)
            self.voiced_frames = sum(self.start_votes)
        else:
            self.frames.append(pcm)
            if voiced:
                self.voiced_frames += 1
        self.quiet_frames = 0 if voiced else self.quiet_frames + 1
        if len(self.frames) >= self.max_frames:
            self.truncated = True
            return True
        return self.quiet_frames >= self.silence_frames

    def finish(self):
        # Never execute a possibly incomplete command clipped by the duration cap.
        if self.truncated or self.voiced_frames < self.min_speech_frames:
            return None
        return np.frombuffer(b"".join(self.frames), dtype=np.int16).astype(np.float32) / 32768.0
