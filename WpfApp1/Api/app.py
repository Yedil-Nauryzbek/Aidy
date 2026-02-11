from collections import OrderedDict
import json
import os
from typing import Any

from fastapi import FastAPI
import joblib
import numpy as np
from pydantic import BaseModel

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ART_DIR = os.path.join(BASE_DIR, "aidy_intent_model")

CLF_PATH = os.path.join(ART_DIR, "classifier.joblib")
ID2INTENT_PATH = os.path.join(ART_DIR, "id2intent.json")
ENCODER_NAME_PATH = os.path.join(ART_DIR, "encoder_name.txt")

# Tunables
CACHE_MAX = 2048
MIN_CONFIDENCE = 0.40
TOP2_MARGIN_MIN = 0.05

app = FastAPI(title="Aidy Intent API (Local, Offline-safe)")


class CommandRequest(BaseModel):
    text: str


encoder: Any = None
clf = None
id2intent: dict[str, str] | None = None
startup_notes: list[str] = []

_cache: OrderedDict[str, dict] = OrderedDict()


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = str(s).strip()
    s = " ".join(s.split()).lower()
    return s


def _cache_get(k: str):
    if k in _cache:
        v = _cache.pop(k)
        _cache[k] = v
        return v
    return None


def _cache_put(k: str, v: dict):
    if k in _cache:
        _cache.pop(k)
    _cache[k] = v
    if len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


def _predict_fallback_intent(text: str) -> tuple[str, float]:
    t = _norm(text)
    if not t:
        return "", 0.0

    if _contains_any(t, ("shutdown", "shut down", "power off", "turn off computer")):
        return "shutdown", 0.9
    if _contains_any(t, ("restart", "reboot")):
        return "restart", 0.9
    if _contains_any(t, ("lock", "lock screen", "lock pc")):
        return "lock", 0.9
    if _contains_any(t, ("task manager", "open task manager")):
        return "task manager", 0.9
    if _contains_any(t, ("screenshot", "screen shot", "print screen")):
        return "screenshot", 0.9
    if _contains_any(t, ("show desktop", "desktop")):
        return "show desktop", 0.75
    if _contains_any(t, ("switch window", "switch app", "alt tab", "next window")):
        return "switch window", 0.9
    if _contains_any(t, ("open cmd", "open command prompt", "command prompt", "cmd")):
        return "open cmd", 0.85

    if _contains_any(t, ("brightness up", "increase brightness", "brighter")):
        return "brightness up", 0.86
    if _contains_any(t, ("brightness down", "decrease brightness", "dimmer")):
        return "brightness down", 0.86
    if _contains_any(t, ("volume up", "increase volume", "sound up", "louder")):
        return "volume up", 0.86
    if _contains_any(t, ("volume down", "decrease volume", "sound down", "quieter")):
        return "volume down", 0.86

    open_prefixes = ("open ", "open up ", "launch ", "start ", "run ", "go to ", "visit ", "show ")
    close_prefixes = ("close ", "quit ", "exit ", "kill ", "stop ")
    if t.startswith(open_prefixes):
        return "open app", 0.7
    if t.startswith(close_prefixes):
        return "close app", 0.7

    return "", 0.0


@app.on_event("startup")
def _startup():
    global encoder, clf, id2intent, startup_notes
    startup_notes = []

    missing = [p for p in (CLF_PATH, ID2INTENT_PATH, ENCODER_NAME_PATH) if not os.path.exists(p)]
    if missing:
        startup_notes.append(
            f"Missing artifacts: {missing}. Files in {ART_DIR}: {os.listdir(ART_DIR) if os.path.isdir(ART_DIR) else 'NO_DIR'}"
        )
        return

    with open(ENCODER_NAME_PATH, "r", encoding="utf-8") as f:
        enc_name = f.read().strip()

    with open(ID2INTENT_PATH, "r", encoding="utf-8") as f:
        id2intent = json.load(f)

    if SentenceTransformer is None:
        startup_notes.append("sentence_transformers package missing -> fallback intent rules only")
    else:
        try:
            # Strictly local load; do not download from the internet.
            encoder = SentenceTransformer(enc_name, local_files_only=True)
        except Exception as e:
            encoder = None
            startup_notes.append(f"encoder offline load failed: {e}")

    try:
        clf = joblib.load(CLF_PATH)
    except Exception as e:
        clf = None
        startup_notes.append(f"classifier load failed: {e}")


@app.get("/")
def root():
    return {
        "status": "ok",
        "encoder_loaded": encoder is not None and clf is not None and id2intent is not None,
        "clf_loaded": clf is not None,
        "num_classes": None if clf is None else int(len(getattr(clf, "classes_", []))),
        "cache_size": len(_cache),
        "artifacts_dir": os.path.basename(ART_DIR),
        "files": os.listdir(BASE_DIR),
        "startup_notes": startup_notes,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
def predict(req: CommandRequest):
    text = _norm(req.text)
    if not text:
        return {"text": "", "intent": "", "confidence": 0.0, "margin": 0.0, "error": "empty text"}

    cached = _cache_get(text)
    if cached is not None:
        return cached

    if encoder is not None and clf is not None and id2intent is not None:
        try:
            emb = encoder.encode([text], normalize_embeddings=True)
            proba = clf.predict_proba(emb)[0]

            best_idx = int(np.argmax(proba))
            best_p = float(proba[best_idx])

            if len(proba) >= 2:
                top2 = np.partition(proba, -2)[-2:]
                margin = float(top2.max() - top2.min())
            else:
                margin = 0.0

            intent = id2intent.get(str(best_idx), "")
            raw_intent = intent
        except Exception:
            intent, best_p = _predict_fallback_intent(text)
            margin = 0.5 if intent else 0.0
            raw_intent = intent
    else:
        intent, best_p = _predict_fallback_intent(text)
        margin = 0.5 if intent else 0.0
        raw_intent = intent

    if best_p < MIN_CONFIDENCE or margin < TOP2_MARGIN_MIN:
        intent_out = ""
    else:
        intent_out = intent

    resp = {
        "text": text,
        "intent": intent_out,
        "confidence": round(best_p, 4),
        "margin": round(margin, 4),
        "raw_intent": raw_intent,
        "fallback": encoder is None or clf is None or id2intent is None,
    }
    _cache_put(text, resp)
    return resp
