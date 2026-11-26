"""Exercise the real offline ECAPA encoder and SQLite enrollment with synthetic speech."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ['HF_HUB_OFFLINE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.voice_auth import VoiceAuthManager
from faster_whisper.audio import decode_audio


def main():
    with tempfile.TemporaryDirectory() as folder:
        manager = VoiceAuthManager(folder)
        assert manager._sb_encoder is not None, 'Primary Voice ID encoder did not load'
        waves = [decode_audio(str(ROOT / '.validation' / f'synthesized-{i}.wav'), sampling_rate=16000)
                 for i in (1, 2, 3, 4, 9)]
        pcm = [(wave * 32767).astype('<i2').tobytes() for wave in waves]
        embedding = manager.extract_embedding(pcm[0])
        assert embedding.shape == (192,), embedding.shape
        ok, message = manager.enroll(pcm, role='Admin', label='Synthetic verification only')
        assert ok, message
        # Enrollment/identification are real. Disable background adaptive writes so
        # temporary SQLite data can close deterministically when this test finishes.
        with patch.object(manager, '_rolling_update'):
            allowed, role, reason = manager.authorize(pcm[0], intent='open_app')
        assert allowed and role == 'Admin', (allowed, role, reason)
        manager._conn.close()
        print('PASS: offline ECAPA (192 dimensions), five-phrase enrollment, Admin authorization.')
        print('Synthetic integration check; human speaker discrimination still requires live enrollment.')


if __name__ == '__main__':
    main()
