"""Whisper yes/no replay through the real browser confirmation flow; no OS actions."""
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import numpy as np
import win32com.client
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy import assistant as core
from aidy.audio_capture import BufferedInput, FRAME_BYTES
from aidy.speech import SpeechRecognizer


def main():
    import faulthandler
    faulthandler.dump_traceback_later(30, exit=True)
    with patch.object(core.pyaudio, 'PyAudio'), \
         patch.object(core, 'Voice', return_value=Mock(muted=True)), \
         patch.object(core, 'start_local_intent_api', return_value=True), \
         patch.object(core, 'stop_local_intent_api'):
        aidy = core.Aidy(str(ROOT))
        aidy.speech = SpeechRecognizer()
        aidy.start_stream = Mock()
        aidy._speak_confirm_prompt = Mock()
        aidy._sleep_success = Mock()
        voice = win32com.client.Dispatch('SAPI.SpVoice')
        try:
            for answer in ('Yes', 'No'):
                path = ROOT / f'.validation/confirm-{answer.lower()}.wav'
                output = win32com.client.Dispatch('SAPI.SpFileStream')
                output.Format.Type = 18
                output.Open(str(path), 3)
                voice.AudioOutputStream = output
                voice.Speak(answer + '.')
                output.Close()
                samples = decode_audio(str(path), sampling_rate=16000)
                samples = np.concatenate([samples, np.zeros(16000, np.float32)])
                pcm = (samples * 32767).astype(np.int16).tobytes()
                # The command router discards capture before prompting. Supply
                # the answer only when confirmation opens the input afterwards.
                supplied = False
                def open_answer_stream():
                    nonlocal supplied
                    if aidy.stream is None:
                        aidy.stream = BufferedInput(Mock())
                        aidy._audio_generation += 1
                        if not supplied:
                            supplied = True
                            for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                                aidy.stream._callback(pcm[offset:offset + FRAME_BYTES], 320, {}, 0)
                aidy.start_stream = open_answer_stream
                with patch.object(core, 'close_app', return_value=True) as close:
                    result = aidy._safe_process_command('close browser')
                    if answer == 'Yes':
                        assert result
                        close.assert_called_once()
                        assert close.call_args.args[0]['id'] == 'browser'
                        print('YES_OK target=' + close.call_args.args[0]['process'], flush=True)
                    else:
                        assert not result
                        close.assert_not_called()
                        print('NO_OK no close action', flush=True)
        finally:
            aidy.close()
            faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    main()
