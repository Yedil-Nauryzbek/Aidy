"""Download the Voice ID encoder once; normal launches use this local directory."""
import os
from pathlib import Path
from huggingface_hub import snapshot_download

target = Path(os.environ.get('AIDY_VOICE_MODEL_DIR') or
              Path(os.environ['LOCALAPPDATA']) / 'Aidy/models/voice-id/ecapa')
snapshot_download('speechbrain/spkrec-ecapa-voxceleb', local_dir=str(target),
                  allow_patterns=['*.ckpt', 'hyperparams.yaml', 'label_encoder.txt'])
print(f'Voice ID model ready: {target}')
