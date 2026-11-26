"""Prepare the offline model and a consistent synthetic voice pack.

--preset selects one of voice_style.VOICE_PRESETS; --preview-only renders a
sample of every preset to .validation/voice-previews for listening.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))
from aidy.voice_style import DEFAULT_STYLE, DEFAULT_PRESET, MODEL_ASSETS, MODEL_BASE, VOICE_PRESETS, NeuralSpeaker, model_directory, phrase_file, speech_text


KEYS={
    # Short spoken wake acknowledgements; Voice picks one of wake_fast/_2/_3 at random.
    'wake_fast':"I'm listening.", 'wake_fast_2':'Yes?', 'wake_fast_3':'Go ahead.', 'wake':'Yes?', 'ready':'At your service.', 'aidy_is_ready':'All systems online.',
    'listening':"I'm listening.", 'success':'Done.', 'executing':'On it.', 'processing':'Working on it.',
    'error':"I couldn't complete that request.", 'not_heard':"I didn't catch that.",
    'not_sure':"I'm not certain. Please repeat that.", 'are_you_sure_clean':'Are you sure?',
    'are_you_sure':'Are you sure?', 'are_you_sure_yes_no':'Are you sure? Please say yes or no.',
    'by_how_much':'By how much?', 'how_much':'By how much?',
    'distract_guard_closed':'Distraction closed.', 'warmup_silence':'',
}
# Dynamic replies common enough to prerecord: synthesis on this CPU delays
# speech by 0.5-2 s, a recorded clip starts at once.
EXTRA_PHRASES=('Permanently empty the Windows Recycle Bin. Are you sure?','Press Send. Are you sure?',
               'Send the message. Are you sure?')
# Status answers (AIRuntime.execute battery_status/volume_status), every value
# prerecorded so they play at once instead of after ~1.5-3 s of synthesis.
NUMBER_PHRASES=tuple(f'Battery at {n} percent.' for n in range(101))+tuple(
    f'Battery at {n} percent, charging.' for n in range(101))+tuple(f'Volume is at {n} percent.' for n in range(101))


def fixed_replies():
    """Constant results and errors of the AI layer, as Aidy speaks them (up to 12 words).
    The speaker also plays a clip for a matching first clause ("I couldn't do that.").
    AIPlanError texts are corrections sent back to the model, not speech, so they are skipped."""
    from aidy.ai_provider import OPENERS
    from aidy.ai_runtime import ORDINARY_WORDS, spoken_text
    replies={"I couldn't do that.",'Done.'}|{opener+mark for opener in OPENERS for mark in ',.'}
    for source in (ROOT/'PythonCore/aidy').glob('ai_*.py'):
        for call in ast.walk(ast.parse(source.read_text(encoding='utf-8-sig'))):
            if not isinstance(call,ast.Call):continue
            name=getattr(call.func,'id',getattr(call.func,'attr',''))
            texts=[k.value for k in call.keywords if k.arg in ('message','spoken_message')]
            if name in ('outcome','Result') and len(call.args)>=2:texts.append(call.args[1])
            if name in ('AIError','AIClarification') and call.args:texts.append(call.args[0])
            for text in texts:
                if isinstance(text,ast.Constant) and isinstance(text.value,str) and 0<len(text.value.split())<=ORDINARY_WORDS:
                    replies.add(spoken_text(text.value,important=True))
    return replies-{''}


def prepare_models():
    import requests
    directory=model_directory()
    directory.mkdir(parents=True,exist_ok=True)
    for name,digest in MODEL_ASSETS.items():
        path=directory/name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:
            continue
        temporary=path.with_suffix(path.suffix+'.part')
        with requests.get(MODEL_BASE+name,stream=True,timeout=(10,30)) as response:
            response.raise_for_status()
            with temporary.open('wb') as target:
                for chunk in response.iter_content(1024*1024):target.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Voice model checksum failed: '+name)
        temporary.replace(path)
        print('Downloaded and verified '+name,flush=True)


def static_phrases():
    phrases=(set(KEYS.values())|set(EXTRA_PHRASES)|set(NUMBER_PHRASES)|fixed_replies())-{''}
    keys=dict(KEYS)
    for source in (ROOT/'PythonCore/aidy').glob('*.py'):
        if source.name in {'voice_style.py','voice.py'}:continue
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        for call in ast.walk(tree):
            if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute):continue
            if call.func.attr=='play_or_tts' and len(call.args)>=2:
                key,fallback=call.args[:2]
                if isinstance(fallback,ast.Constant) and isinstance(fallback.value,str):
                    phrases.add(speech_text(fallback.value))
                    if isinstance(key,ast.Constant) and isinstance(key.value,str):keys.setdefault(key.value,fallback.value)
            if call.func.attr=='tts_blocking' and call.args and isinstance(call.args[0],ast.Constant) and isinstance(call.args[0].value,str):
                phrases.add(speech_text(call.args[0].value))
    from aidy.config import VOICE_RESPONSES
    phrases.update(VOICE_RESPONSES.values())
    # Fixed direct clip families; original numbered MP3/WAVs stay untouched.
    for path in (ROOT/'Assets/voice').iterdir():
        if path.suffix.casefold() not in {'.wav','.mp3'}:continue
        key=re.sub(r'_\d+$','',path.stem)
        if key in KEYS:keys.setdefault(key,KEYS[key])
    phrases.update(text for text in keys.values() if text)
    # AIRuntime reports app launches as "Opening <id>." / "Closing <id>.":
    # prerecord them so the commonest answers play instantly.
    from aidy.apps import load_apps_config
    ids={app['id'] for app in load_apps_config(str(ROOT))}|{'browser','music','chatgpt_app'}
    for app in ids:
        if not app.startswith('installed_'):
            phrases.update(f'{verb} {app.replace("_"," ")}.' for verb in ('Opening','Closing'))
    return keys,{speech_text(p) for p in phrases if p.strip()}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--models-only',action='store_true')
    parser.add_argument('--preview-only',action='store_true')
    parser.add_argument('--preset',choices=sorted(VOICE_PRESETS),default=DEFAULT_PRESET)
    parser.add_argument('--missing-only',action='store_true',
                        help='Render only phrases without a WAV; the existing pack must use the same preset.')
    args=parser.parse_args()
    prepare_models()
    if args.models_only:return
    if args.preview_only:
        previews=ROOT/'.validation/voice-previews'
        previews.mkdir(parents=True,exist_ok=True)
        text="Hello, I'm Aidy. Opening Steam now. Done. I couldn't find that window. Your essay is ready."
        for name,preset in VOICE_PRESETS.items():
            (previews/(name+'.wav')).write_bytes(NeuralSpeaker(dict(DEFAULT_STYLE,preset=name,**preset)).render(text))
        print('Preview files: '+str(previews),flush=True)
        return
    style=dict(DEFAULT_STYLE,preset=args.preset,**VOICE_PRESETS[args.preset])
    speaker=NeuralSpeaker(style)
    directory=ROOT/'Assets/voice_jarvis'
    directory.mkdir(parents=True,exist_ok=True)
    keys,phrases=static_phrases()
    profile=dict(style,keys={},phrases={})
    if args.missing_only:
        existing=json.loads((directory/'profile.json').read_text(encoding='utf-8'))
        if existing.get('preset')!=args.preset:
            raise SystemExit('--missing-only needs a pack rendered with the same preset; run without it.')
    for index,text in enumerate(sorted(phrases)):
        start=time.perf_counter()
        name=phrase_file(text)
        if args.missing_only and (directory/name).is_file():
            profile['phrases'][text]=name
            continue
        (directory/name).write_bytes(speaker.render(text))
        profile['phrases'][text]=name
        print(f'{index+1}/{len(phrases)}  {time.perf_counter()-start:.2f}s  {text[:70]}',flush=True)
    for key,text in keys.items():
        if text:profile['keys'][key]=phrase_file(text)
    temporary=directory/'profile.json.tmp'
    temporary.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(directory/'profile.json')
    print('Voice pack ready: '+str(directory),flush=True)


if __name__=='__main__':main()
