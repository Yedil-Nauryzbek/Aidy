from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import numpy as np
import joblib
import json
import os
from collections import OrderedDict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ART_DIR = os.path.join(BASE_DIR, "aidy_intent_model")

CLF_PATH = os.path.join(ART_DIR, "classifier.joblib")
ID2INTENT_PATH = os.path.join(ART_DIR, "id2intent.json")
ENCODER_NAME_PATH = os.path.join(ART_DIR, "encoder_name.txt")

# Tunables
CACHE_MAX = 2048
MIN_CONFIDENCE = 0.40   # ниже -> intent="" (пусть AIDY не исполняет)
TOP2_MARGIN_MIN = 0.05  # если топ-2 слишком близко -> intent=""

app = FastAPI(title="Aidy Intent API (Local, LogisticRegression)")

class CommandRequest(BaseModel):
    text: str

encoder: SentenceTransformer | None = None
clf = None
id2intent: dict[str, str] | None = None

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

@app.on_event("startup")
def _startup():
    global encoder, clf, id2intent

    # Check artifacts
    missing = [p for p in (CLF_PATH, ID2INTENT_PATH, ENCODER_NAME_PATH) if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Missing artifacts: {missing}. Files in {ART_DIR}: {os.listdir(ART_DIR) if os.path.isdir(ART_DIR) else 'NO_DIR'}")

    # Load
    with open(ENCODER_NAME_PATH, "r", encoding="utf-8") as f:
        enc_name = f.read().strip()

    encoder = SentenceTransformer(enc_name)   # downloads if needed
    clf = joblib.load(CLF_PATH)

    with open(ID2INTENT_PATH, "r", encoding="utf-8") as f:
        id2intent = json.load(f)

@app.get("/")
def root():
    return {
        "status": "ok",
        "encoder_loaded": encoder is not None,
        "clf_loaded": clf is not None,
        "num_classes": None if clf is None else int(len(getattr(clf, "classes_", []))),
        "cache_size": len(_cache),
        "artifacts_dir": os.path.basename(ART_DIR),
        "files": os.listdir(BASE_DIR),
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

    # Encode
    emb = encoder.encode([text], normalize_embeddings=True)
    proba = clf.predict_proba(emb)[0]  # shape: [num_classes]

    best_idx = int(np.argmax(proba))
    best_p = float(proba[best_idx])

    # margin between top-2
    if len(proba) >= 2:
        top2 = np.partition(proba, -2)[-2:]
        margin = float(top2.max() - top2.min())
    else:
        margin = 0.0

    intent = id2intent.get(str(best_idx), "")

    # Gate uncertain answers
    if best_p < MIN_CONFIDENCE or margin < TOP2_MARGIN_MIN:
        intent_out = ""
    else:
        intent_out = intent

    resp = {
        "text": text,
        "intent": intent_out,
        "confidence": round(best_p, 4),
        "margin": round(margin, 4),
        "raw_intent": intent,   # полезно для дебага (AIDY может игнорировать)
    }
    _cache_put(text, resp)
    return resp
