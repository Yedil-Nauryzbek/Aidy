"""Replay 'Hey', then a command during real Whisper inference; mock only actions/devices."""
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock, patch

import numpy as np
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PythonCore"))
from aidy import assistant as core
from aidy.audio_capture import BufferedInput, FRAME_BYTES
from aidy.speech import SpeechRecognizer


def main():
    hey = decode_audio(str(ROOT / ".validation/synthesized-0.wav"), sampling_rate=16000)
    command = decode_audio(str(ROOT / ".validation/synthesized-4.wav"), sampling_rate=16000)
    waveform = np.concatenate([hey, np.zeros(14400, np.float32), command, np.zeros(16000, np.float32)])
    pcm = (waveform * 32767).astype(np.int16).tobytes()
    with patch.object(core.pyaudio, "PyAudio"), \
         patch.object(core, "Voice", return_value=Mock(muted=True)), \
         patch.object(core, "start_local_intent_api", return_value=True), \
         patch.object(core, "stop_local_intent_api"), \
         patch.object(core, "launch_app", return_value=True) as launch:
        aidy = core.Aidy(str(ROOT))
        aidy.speech = SpeechRecognizer()
        aidy._sleep_success = Mock()
        aidy._record_action = Mock()
        aidy.start_stream = Mock()
        try:
            for attempt in range(2):
                driver = Mock()
                driver.open.return_value.is_active.return_value = True
                stream = BufferedInput(driver)
                aidy.stream = stream
                aidy._audio_generation += 1

                def feed():
                    started = time.monotonic()
                    for index, offset in enumerate(range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)):
                        delay = started + index * 0.02 - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
                        stream._callback(pcm[offset:offset + FRAME_BYTES], 320, {}, 0)

                producer = threading.Thread(target=feed, daemon=True)
                started = time.perf_counter()
                producer.start()
                wake_tail = aidy.wait_for_wake()
                text = wake_tail or aidy._listen_post_wake_command()
                assert text == "open browser", repr(text)
                assert aidy._safe_process_command(text)
                assert launch.call_count == attempt + 1
                print(f"TWO_STAGE_OK attempt={attempt + 1} launches={launch.call_count} total_s={time.perf_counter()-started:.2f}", flush=True)
                producer.join(timeout=5)

            # Unmuted standalone wake: emulate a recorded reply with the real
            # playback hooks, then speak after it finishes. No speakers/actions.
            ack_done = threading.Event()
            aidy.voice.muted = False
            def reply(key):
                aidy._before_playback()
                time.sleep(.05)
                aidy._after_playback()
                ack_done.set()
                return True
            aidy.voice.play_key.side_effect = reply
            def reopen():
                if aidy.stream is None:
                    aidy.stream = BufferedInput(Mock())
                    aidy._audio_generation += 1
                elif aidy.stream._suspended and not aidy._is_deafened():
                    aidy.stream.resume()
                    aidy._audio_generation += 1
            aidy.start_stream = reopen
            def send(samples):
                data = (samples * 32767).astype(np.int16).tobytes()
                start = time.monotonic()
                for i, offset in enumerate(range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES)):
                    delay = start + i * .02 - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    stream = aidy.stream
                    if stream is not None:
                        stream._callback(data[offset:offset + FRAME_BYTES], 320, {}, 0)
            def speak_after_reply():
                send(np.concatenate([hey, np.zeros(8000, np.float32)]))
                if not ack_done.wait(timeout=10):
                    return
                time.sleep(.15)
                send(np.concatenate([command, np.zeros(16000, np.float32)]))
            reopen()
            producer = threading.Thread(target=speak_after_reply, daemon=True)
            producer.start()
            assert aidy.wait_for_wake() is None
            assert ack_done.is_set(), 'Missing wake voice reply'
            text = aidy._listen_post_wake_command()
            assert text == 'open browser', repr(text)
            assert aidy._safe_process_command(text)
            assert launch.call_count == 3
            producer.join(timeout=5)
            print('SPOKEN_ACK_OK replies=1 launches=3', flush=True)

            # Command already queued during wake recognition: preserve its
            # complete audio, speak, then recognize/execute it exactly once.
            aidy.stop_stream()
            reopen()
            queued = np.concatenate([hey, np.zeros(8000, np.float32),
                                     command, np.zeros(16000, np.float32)])
            queued_pcm = (queued * 32767).astype(np.int16).tobytes()
            for offset in range(0, len(queued_pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                aidy.stream._callback(queued_pcm[offset:offset + FRAME_BYTES], 320, {}, 0)
            states = []
            replies_before = aidy.voice.play_key.call_count
            with patch.object(core, 'ui_state', side_effect=states.append):
                assert aidy.wait_for_wake() is None
                assert states == ['LISTENING', 'SPEAKING', 'COMMAND_LISTENING'], states
                assert aidy.voice.play_key.call_count == replies_before + 1
                text = aidy._listen_post_wake_command()
            assert text == 'open browser', repr(text)
            assert aidy._post_wake_capture is None
            assert aidy._safe_process_command(text)
            assert launch.call_count == 4
            print('QUEUED_COMMAND_ACK_OK replies=1 launches=4', flush=True)
        finally:
            aidy.close()


if __name__ == "__main__":
    main()
