"""Offline neural speech with a blended, lightly synthetic voice treatment."""
from collections import OrderedDict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import wave

import numpy as np

PROFILE_VERSION = 3
# Androgynous Kokoro blends; samples are rendered by scripts/prepare_jarvis_voice.py --previews.
VOICE_PRESETS = {
    # Default since 22.11.2026 (second change): original calm British "Jarvis-style"
    # voice from stock Kokoro bm_lewis; not a clone of any real person.
    # speed 1.15 (was 1.0) on the owner's request: replies sounded slow.
    'jarvis': dict(lang='en-gb', speed=1.15, effect_strength=.30, blend={'bm_lewis': 1.0}),
    # Default since 23.11.2026 (conversational Aidy): same bm_lewis timbre, much
    # lighter processing (the 11/17 ms doubling made speech sound robotic).
    # scripts/voice_expressiveness.py: blends with bm_george/bm_fable had LESS
    # pitch variation (3.43/3.99 st) than bm_lewis alone (~4.1 st).
    'jarvis_live': dict(lang='en-gb', speed=1.15, effect_strength=.12, blend={'bm_lewis': 1.0}),
    # Previous default: clearly synthetic, slightly feminine, quick pace.
    'aidy_robot': dict(lang='en-us', speed=1.15, effect_strength=.36, blend={'af_nova': .6, 'am_echo': .4}),
    'nova_soft': dict(lang='en-us', speed=1.2, effect_strength=.08, blend={'af_nova': .65, 'am_echo': .35}),
    'nova_synth': dict(lang='en-us', speed=1.12, effect_strength=.22, blend={'af_nova': .5, 'am_echo': .5}),
    'neutral_glass': dict(lang='en-gb', speed=1.12, effect_strength=.26, blend={'bf_emma': .55, 'bm_lewis': .45}),
    'soft_android': dict(lang='en-us', speed=1.15, effect_strength=.30, blend={'af_sky': .45, 'am_puck': .30, 'af_river': .25}),
    'crisp_robot': dict(lang='en-us', speed=1.12, effect_strength=.34, blend={'af_heart': .5, 'am_michael': .5}),
}
DEFAULT_PRESET = 'jarvis_live'
DEFAULT_STYLE = dict(version=3, engine='kokoro', voice='aidy_synthetic', preset=DEFAULT_PRESET,
                     **VOICE_PRESETS[DEFAULT_PRESET])
# Kokoro has no emotion control; conversational tone shapes pace and pauses
# around the profile speed: (speed multiplier, pause multiplier).
TONE_PROSODY = {'neutral': (1.00, 1.00), 'warm': (.95, 1.15), 'amused': (1.03, .95),
                'concerned': (.92, 1.25), 'upbeat': (1.06, .90)}


def tone_prosody(base_speed, tone):
    """(speed, sentence_pause, clause_pause) for a tone, within the validated speed range."""
    speed, pause = TONE_PROSODY.get(tone, TONE_PROSODY['neutral'])
    return min(1.25, max(.85, base_speed * speed)), .20 * pause, .075 * pause


MODEL_ASSETS = {
    'kokoro-v1.0.onnx': 'beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a',
    'voices-v1.0.bin': 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d',
}
MODEL_BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'


def model_directory():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Aidy/models/kokoro-v1.0'


def speech_text(text):
    """Keep punctuation, questions and decimal values; remove display markup."""
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', str(text or ''))
    text = text.replace('**', '').replace('`', '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return re.sub(r'[ \t]+', ' ', text).strip()


def load_profile(directory):
    try:
        profile = json.loads((Path(directory)/'profile.json').read_text(encoding='utf-8'))
        blend = profile.get('blend')
        if (profile.get('version') != PROFILE_VERSION or profile.get('engine') != 'kokoro'
                or profile.get('voice') != 'aidy_synthetic'
                or profile.get('lang') not in {'en-us', 'en-gb'}
                or not .85 <= profile.get('speed',0) <= 1.25
                or not 0 <= profile.get('effect_strength',-1) <= .4
                or not isinstance(blend,dict) or not 1 <= len(blend) <= 4
                or any(not isinstance(k,str) or not re.fullmatch(r'[a-z]{2}_[a-z]{2,20}',k)
                       or type(v) not in (int,float) or not 0 < v <= 1 for k,v in blend.items())
                or abs(sum(blend.values())-1) > .01):
            return None
        for mapping in ('keys','phrases'):
            if not isinstance(profile.get(mapping),dict) or any(
                    not isinstance(k,str) or not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{24}\.wav',v)
                    for k,v in profile[mapping].items()):
                return None
        return profile
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def treated_audio(samples, sample_rate, strength=.18):
    """Preserve pitch and timing; add clean presence, short doubling and a small room.
    The old 71 Hz ring modulator made the voice sound rough, so it is gone.
    strength .30 is the full treatment validated on the voice previews."""
    from scipy.signal import butter, sosfilt
    audio=np.asarray(samples,dtype=np.float32).reshape(-1)
    if not audio.size:
        return audio.copy()
    if not np.isfinite(audio).all() or sample_rate < 16000 or not 0 <= strength <= .4:
        raise ValueError('Invalid speech audio or effect strength.')
    amount=strength/.30
    audio=audio-float(audio.mean())
    dry=sosfilt(butter(2,[80,min(9000,sample_rate*.46)],btype='bandpass',fs=sample_rate,output='sos'),audio)
    dry=dry+.25*amount*sosfilt(butter(2,[2000,5000],btype='bandpass',fs=sample_rate,output='sos'),dry)
    out=dry.copy()
    for seconds,gain in [(.011,.22),(.017,.14)]:
        delay=int(sample_rate*seconds)
        if len(dry)>delay:out[delay:]+=amount*gain*dry[:-delay]
    room=np.zeros_like(out)
    for seconds,gain in [(.037,.08),(.053,.05),(.071,.03)]:
        delay=int(sample_rate*seconds)
        if len(out)>delay:room[delay:]+=amount*gain*out[:-delay]
    audio=np.tanh((out+room)*1.05)/1.05
    peak=float(np.max(np.abs(audio)))
    if peak>1e-7:
        audio*=min(1.8,.88/peak)
    fade=min(int(.006*sample_rate),len(audio)//2)
    if fade:
        audio[:fade]*=np.linspace(0,1,fade)
        audio[-fade:]*=np.linspace(1,0,fade)
    return np.clip(audio,-.92,.92).astype(np.float32)


def trim_silence(samples, rate, lead=.12, tail=.06):
    """Drop the synthesizer's long leading/trailing silence; the microphone
    stays paused for every played sample. Keep a short lead-in: an idle output
    device (here the FxSound virtual device) can swallow a sound's first samples."""
    audio=np.asarray(samples,dtype=np.float32).reshape(-1)
    if not audio.size:
        return audio
    voiced=np.flatnonzero(np.abs(audio)>max(1e-4,.02*float(np.max(np.abs(audio)))))
    if not voiced.size:
        return audio[:0]
    start=max(0,int(voiced[0]-lead*rate))
    end=min(audio.size,int(voiced[-1]+tail*rate))
    return audio[start:end]


def wave_bytes(samples, rate=24000):
    data=io.BytesIO()
    with wave.open(data,'wb') as target:
        target.setparams((1,2,rate,0,'NONE','not compressed'))
        target.writeframes(np.rint(np.clip(samples,-1,1)*32767).astype('<i2').tobytes())
    return data.getvalue()


def phrase_file(text):
    return hashlib.sha256(speech_text(text).encode('utf-8')).hexdigest()[:24]+'.wav'


def clip_paths(profile, clip_dir):
    """Prerecorded phrase -> WAV path. Without clip_dir (the pack builder) every phrase is synthesized."""
    if not clip_dir:
        return {}
    return {text: os.path.join(clip_dir, name) for text, name in (profile or {}).get('phrases', {}).items()}


# Chunking (measured 25.11.2026): synthesis is ~1x real time here, so the first chunk is
# short and each next one renders while the previous one plays, growing with it.
FIRST_CHUNK = 24
MAX_CHUNK = 160
# Every model call costs a fixed ~0.5-0.85 s here, so a shorter piece mid-text buys almost no audio.
MIN_CHUNK = 12
# . ! ? end a unit only before a capital, digit, quote or bracket ("3 p.m. today" is one); , ; : dashes always.
UNIT_END = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["\')]))\s+(?=["\'(]?[A-Z0-9])|(?<=[,;:–—])\s+')
BREAK_BEFORE = re.compile(r' (?=(?:and|but|so|because|which|that|while|when|if|or|to|for|with|in|on|at|from|of)\b)')
# A title keeps its full stop inside the sentence: "Dr. Brown", "St. Petersburg".
ABBREVIATION = re.compile(r'(?:^|\s)(?:Mr|Mrs|Ms|Dr|St|Prof|Sr|Jr|vs|etc|e\.g|i\.e)\.$', re.I)


def rejoin(parts):
    """Glue back pieces that were split right after a title abbreviation."""
    joined = []
    for part in parts:
        if joined and ABBREVIATION.search(joined[-1]):
            joined[-1] += ' ' + part
        else:
            joined.append(part)
    return joined


def next_budget(length):
    return min(MAX_CHUNK, max(20, int(1.3 * length) + 4))


def split_point(unit, budget):
    """Where to cut a unit longer than the budget: before a conjunction/preposition, else at a
    space; never inside a word and never leaving either side shorter than MIN_CHUNK."""
    def usable(i, limit):
        return MIN_CHUNK <= i <= limit and len(unit) - i - 1 >= MIN_CHUNK
    cuts = [m.start() for m in BREAK_BEFORE.finditer(unit) if usable(m.start(), budget + 8)]
    spaces = [i for i, char in enumerate(unit) if char == ' ' and usable(i, budget)]
    if cuts or spaces:
        return (cuts or spaces)[-1]
    later = unit.find(' ', budget)  # a long word or path: cut after it, not inside it
    return later if later > 0 and len(unit) - later - 1 >= MIN_CHUNK else len(unit)


class NeuralSpeaker:
    """One bounded CPU session owned by the existing speech worker thread."""
    clips = {}

    def __init__(self, profile=None, directory=None, clip_dir=None):
        from kokoro_onnx import Kokoro
        import onnxruntime as ort
        self.profile=dict(DEFAULT_STYLE,**(profile or {}))
        self.clips=clip_paths(profile,clip_dir)
        directory=Path(directory or model_directory())
        options=ort.SessionOptions()
        # Speech runs while the microphone is paused, so recognition is idle;
        # two threads synthesized at under half real-time speed on this CPU.
        options.intra_op_num_threads=4
        options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        session=ort.InferenceSession(str(directory/'kokoro-v1.0.onnx'),sess_options=options,
                                    providers=['CPUExecutionProvider'])
        self.engine=Kokoro.from_session(session,str(directory/'voices-v1.0.bin'))
        self.model_voice=sum(weight*self.engine.get_voice_style(name)
                             for name,weight in self.profile['blend'].items()).astype(np.float32)
        self.cache=OrderedDict()
        self.cache_bytes=0

    def render(self, text, tone='neutral'):
        text=speech_text(text)
        if not text:
            return wave_bytes(np.zeros(0,np.float32))
        key=(tone if tone in TONE_PROSODY else 'neutral',text)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        result=None
        if text in self.clips:
            try:
                result=Path(self.clips[text]).read_bytes()  # prerecorded: no synthesis delay
            except OSError:
                result=None  # a missing pack file is synthesized instead
        if result is None:
            speed,sentence_pause,clause_pause=tone_prosody(self.profile['speed'],key[0])
            samples,rate=self.engine.create(text,voice=self.model_voice,speed=speed,
                lang=self.profile['lang'],sentence_pause=sentence_pause,clause_pause=clause_pause)
            result=wave_bytes(treated_audio(trim_silence(samples,rate),rate,self.profile['effect_strength']),rate)
        # Dynamic user responses are cached in memory only, not written to disk.
        if len(result)<=4*1024*1024:
            self.cache[key]=result
            self.cache_bytes+=len(result)
            while len(self.cache)>24 or self.cache_bytes>24*1024*1024:
                _,old=self.cache.popitem(last=False)
                self.cache_bytes-=len(old)
        return result

    def chunks(self,text,tone='neutral'):
        """Rendered pieces in order: a short first one, then each up to 1.3x the previous.
        Units (clauses, sentences) are packed whole; a prerecorded unit at a chunk start plays alone."""
        budget,current=FIRST_CHUNK,''
        for unit in rejoin(UNIT_END.split(speech_text(text))):
            unit=unit.strip()
            while unit:
                joined=current+' '+unit if current else unit
                if not current and unit in self.clips:
                    yield self.render(unit,tone)
                    budget,unit=next_budget(len(unit)),''
                elif len(joined)<=budget:
                    current,unit=joined,''
                elif len(current)>=MIN_CHUNK:
                    yield self.render(current,tone)
                    budget,current=next_budget(len(current)),''
                else:  # nothing or a fragment so far: cut the joined text instead
                    cut=split_point(joined,budget)
                    yield self.render(joined[:cut].rstrip(),tone)
                    budget,current,unit=next_budget(cut),'',joined[cut:].lstrip()
        if current:
            yield self.render(current,tone)
