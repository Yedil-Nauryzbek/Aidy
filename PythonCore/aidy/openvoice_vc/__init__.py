"""Tone color converter from OpenVoice V2 (MIT, MyShell.ai; see LICENSE).

Only the voice-conversion network is vendored; the OpenVoice text front end and
the wavmark watermark are not used. Weights: %LOCALAPPDATA%/Aidy/models/openvoice-v2.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace


def model_directory():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Aidy/models/openvoice-v2'


class ToneColorConverter:
    def __init__(self, directory=None, threads=4):
        import torch
        from .models import SynthesizerTrn
        directory = Path(directory or model_directory())
        torch.set_num_threads(threads)
        config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
        self.data = SimpleNamespace(**config['data'])
        self.model = SynthesizerTrn(0, self.data.filter_length // 2 + 1, n_speakers=self.data.n_speakers,
                                    **config['model']).eval()
        state = torch.load(directory / 'checkpoint.pth', map_location='cpu', weights_only=True)
        self.model.load_state_dict(state['model'], strict=False)
        self.rate = self.data.sampling_rate
        self.torch = torch

    def _spec(self, audio):
        from .mel_processing import spectrogram_torch
        y = self.torch.as_tensor(audio, dtype=self.torch.float32).unsqueeze(0)
        d = self.data
        return spectrogram_torch(y, d.filter_length, d.sampling_rate, d.hop_length, d.win_length, center=False)

    def extract_se(self, clips):
        """Mean speaker embedding of mono float clips already at self.rate."""
        with self.torch.no_grad():
            gs = [self.model.ref_enc(self._spec(c).transpose(1, 2)).unsqueeze(-1) for c in clips]
        return self.torch.stack(gs).mean(0)

    def convert(self, audio, src_se, tgt_se, tau=0.3):
        """audio: mono float at self.rate; returns converted mono float32 numpy."""
        with self.torch.no_grad():
            spec = self._spec(audio)
            lengths = self.torch.LongTensor([spec.size(-1)])
            out = self.model.voice_conversion(spec, lengths, sid_src=src_se, sid_tgt=tgt_se, tau=tau)[0]
        return out[0, 0].float().numpy()
