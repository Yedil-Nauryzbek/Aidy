"""Synthetic document-command replay through the unchanged English tiny.en model."""
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))


def main():
    import win32com.client
    from faster_whisper.audio import decode_audio
    from aidy.speech import SpeechRecognizer
    voice=win32com.client.Dispatch('SAPI.SpVoice')
    voices=voice.GetVoices()
    for i in range(voices.Count):
        if 'zira' in voices.Item(i).GetDescription().lower():
            voice.Voice=voices.Item(i);break
    cases=[('Open Notepad and then write an essay in 250 words.',('notepad','essay')),
           ('Open Microsoft Word, create a new document, and write a short story about friendship.',('word','document','story')),
           ('Close it.',('close','it')),
           ("Don't close Notepad.",('close','notepad')),
           ('Open Notepad and write a 500-word essay about Google.',('notepad','essay','google')),
           ('Write an essay about space in five hundred words.',('essay','space'))]
    recognizer=SpeechRecognizer()
    rows=[]
    try:
        for i,(phrase,expected) in enumerate(cases):
            path=ROOT/f'.validation/document-speech-{i}.wav'
            stream=win32com.client.Dispatch('SAPI.SpFileStream');stream.Format.Type=18;stream.Open(str(path),3)
            voice.AudioOutputStream=stream;voice.Speak(phrase);stream.Close()
            audio=decode_audio(str(path),sampling_rate=16000)
            start=time.perf_counter();text=recognizer.transcribe(audio,purpose='command')
            passed=all(word in text for word in expected)
            if i==0:passed=passed and bool(re.search(r'250|two hundred (?:and )?fifty',text))
            if i==3:passed=passed and bool(re.search(r"don't|do not",text))
            rows.append(dict(input=phrase,transcript=text,passed=passed,recognition_ms=round((time.perf_counter()-start)*1000,2)))
            print(json.dumps(rows[-1]),flush=True)
    finally:recognizer.close()
    (ROOT/'.validation/document-speech.json').write_text(json.dumps(dict(scope='Six synthesized Zira utterances; real cached tiny.en English decoding, no microphone/accent claim and no desktop actions.',results=rows),indent=2),encoding='utf-8')
    if not all(row['passed'] for row in rows):raise SystemExit(1)


if __name__=='__main__':main()
