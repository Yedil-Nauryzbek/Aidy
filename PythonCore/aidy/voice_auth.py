"""voice_auth.py – Voice Biometric Authentication and RBAC for Aidy.

Speaker verification uses resemblyzer (pip install resemblyzer) when available.
If resemblyzer is not installed, a lightweight spectral fingerprint built from
numpy is used as a fallback.  The fallback is less robust but requires no extra
dependencies beyond numpy.

Database: SQLite  (voice_profiles.db next to main.py)
Roles:    Admin  – unrestricted
          User   – all commands except shutdown / restart
          Guest  – safe subset only
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
import time
from collections import deque
from typing import Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Optional: resemblyzer speaker encoder
# ---------------------------------------------------------------------------
try:
    from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
    HAS_RESEMBLYZER = True
except ImportError:
    HAS_RESEMBLYZER = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000

# Cosine-similarity threshold for accepting a voice match.
# Lower → more permissive (false accepts), Higher → stricter (false rejects).
SIMILARITY_THRESHOLD = 0.80

# Intents that require Admin role regardless of everything else.
ADMIN_ONLY_INTENTS: set[str] = {"shutdown", "restart", "grant_access"}

# Intents blocked for "User" role (they can do everything else).
USER_BLOCKED_INTENTS: set[str] = {"shutdown", "restart"}

# Intents that "Guest" role IS allowed to run (allow-list).
GUEST_ALLOWED_INTENTS: set[str] = {
    "screenshot",
    "open_app",
    "close_app",
    "open_browser",
    "timer",
    "cancel_timer",
    "volume_up",
    "volume_down",
    "volume_change",
    "set_volume",
    "brightness_up",
    "brightness_down",
    "brightness_change",
    "mute",
    "unmute",
    "study_mode_start",
    "study_mode_stop",
    "study_mode_status",
    "switch_window",
    "show_desktop",
    "lock",
}

# Voice-command phrases that the Vosk grammar should recognise for grant ops.
GRANT_GRAMMAR_PHRASES: list[str] = [
    "grant admin access",
    "grant user access",
    "grant guest access",
    "grant admin access for one hour",
    "grant admin access for two hours",
    "grant admin access for three hours",
    "grant admin access permanently",
    "grant user access for one hour",
    "grant user access for two hours",
    "grant user access for thirty minutes",
    "grant user access permanently",
    "grant guest access for one hour",
    "grant guest access for two hours",
    "grant guest access for thirty minutes",
    "grant guest access for one day",
    "grant guest access permanently",
    "revoke access",
    "list users",
]

# ---------------------------------------------------------------------------
# Duration parser for "for 2 hours", "for 30 minutes", "for 1 day"
# ---------------------------------------------------------------------------
_NUMBER_WORDS = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "sixty": 60,
}

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
}


def _parse_duration_seconds(text: str) -> Optional[int]:
    """Parse 'for N unit' from text.  Returns seconds or None."""
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    try:
        for_idx = tokens.index("for")
    except ValueError:
        return None
    remaining = tokens[for_idx + 1:]
    if not remaining:
        return None

    # Handle "permanently" / "permanent"
    if remaining[0] in ("permanently", "permanent", "forever"):
        return None  # None = no expiry

    # Parse number
    n = None
    unit_idx = 0
    for i, tok in enumerate(remaining):
        if tok.isdigit():
            n = int(tok)
            unit_idx = i + 1
            break
        if tok in _NUMBER_WORDS:
            n = _NUMBER_WORDS[tok]
            unit_idx = i + 1
            break

    if n is None:
        return None

    unit_tok = remaining[unit_idx] if unit_idx < len(remaining) else "minutes"
    multiplier = _UNIT_SECONDS.get(unit_tok, 60)
    seconds = n * multiplier
    return seconds if seconds > 0 else None


def _parse_grant_command(text: str) -> Optional[dict]:
    """
    Detect and parse a grant-access command.

    Returns:
        {"role": "Admin"|"User"|"Guest", "expires_at": float|None}
        or None if not a grant command.
    """
    t = " ".join((text or "").strip().lower().split())

    # Must start with "grant"
    if not t.startswith("grant "):
        return None

    role = None
    for candidate in ("admin", "user", "guest"):
        if candidate in t:
            role = candidate.capitalize()
            break

    if role is None:
        return None

    # Optional duration
    secs = _parse_duration_seconds(t)
    expires_at = (time.time() + secs) if secs else None

    return {"role": role, "expires_at": expires_at}


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _pcm16_to_float(raw_bytes: bytes) -> np.ndarray:
    """Convert raw PCM-16 little-endian bytes → float32 in [-1, 1]."""
    n = len(raw_bytes) // 2
    samples = struct.unpack(f"<{n}h", raw_bytes[: n * 2])
    return np.array(samples, dtype=np.float32) / 32768.0


def _spectral_embedding(raw_bytes: bytes, n_bands: int = 64) -> np.ndarray:
    """
    Lightweight fallback embedding: mean log-energy per mel-spaced frequency band.
    Produces a 64-dimensional vector; good enough for the same-session speaker
    discrimination but NOT production-grade biometrics.
    """
    wav = _pcm16_to_float(raw_bytes)
    if len(wav) < 400:
        return np.zeros(n_bands, dtype=np.float32)

    frame_size = 400
    hop = 160
    frames = []
    for i in range(0, len(wav) - frame_size, hop):
        frame = wav[i: i + frame_size] * np.hanning(frame_size)
        spectrum = np.abs(np.fft.rfft(frame))
        # Discard very low-energy frames (silence / noise)
        if spectrum.sum() < 1e-4:
            continue
        frames.append(spectrum)

    if not frames:
        return np.zeros(n_bands, dtype=np.float32)

    mean_spectrum = np.mean(frames, axis=0)
    # Bin into n_bands using logarithmically-spaced indices
    half = len(mean_spectrum)
    log_idxs = np.unique(
        np.round(np.logspace(0, np.log10(half), n_bands + 1)).astype(int)
    )
    log_idxs = np.clip(log_idxs, 0, half)
    bands = []
    for a, b in zip(log_idxs[:-1], log_idxs[1:]):
        seg = mean_spectrum[a:b]
        bands.append(float(seg.mean()) if len(seg) > 0 else 0.0)

    emb = np.array(bands, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    if norm > 1e-8:
        emb /= norm
    return emb


# ---------------------------------------------------------------------------
# VoiceAuthManager
# ---------------------------------------------------------------------------

class VoiceAuthManager:
    """Manages voice-profile database and RBAC enforcement."""

    DB_FILE = "voice_profiles.db"
    MIN_AUDIO_BYTES = 3200  # 0.1 s at 16 kHz PCM-16

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.db_path = os.path.join(base_dir, self.DB_FILE)
        self._encoder = None
        # Holds the embedding of the most-recent unknown speaker so that an
        # Admin can later grant them access via voice command.
        self.last_unknown_embedding: Optional[np.ndarray] = None
        self.last_unknown_ts: float = 0.0
        self._user_count_cache: Optional[int] = None
        self._init_db()
        self._load_encoder()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _load_encoder(self) -> None:
        if not HAS_RESEMBLYZER:
            return
        try:
            self._encoder = VoiceEncoder()
            print("[VoiceAuth] resemblyzer encoder loaded")
        except Exception as exc:
            print(f"[VoiceAuth] resemblyzer failed to load ({exc}); using spectral fallback")
            self._encoder = None

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_users (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    label      TEXT    NOT NULL,
                    role       TEXT    NOT NULL DEFAULT 'Guest',
                    embedding  TEXT    NOT NULL,
                    created_at REAL    NOT NULL,
                    expires_at REAL
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _invalidate_cache(self) -> None:
        self._user_count_cache = None

    def has_any_user(self) -> bool:
        if self._user_count_cache is not None:
            return self._user_count_cache > 0
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM voice_users").fetchone()
        count = row[0] if row else 0
        self._user_count_cache = count
        return count > 0

    def has_admin(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM voice_users WHERE role='Admin'"
            ).fetchone()
        return (row[0] if row else 0) > 0

    def _load_active_users(self) -> list[dict]:
        """Return all non-expired user rows from the DB."""
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, label, role, embedding, expires_at "
                "FROM voice_users "
                "WHERE expires_at IS NULL OR expires_at > ?",
                (now,),
            ).fetchall()
        users = []
        for row in rows:
            try:
                emb = np.array(json.loads(row[3]), dtype=np.float32)
                users.append(
                    {
                        "id": row[0],
                        "label": row[1],
                        "role": row[2],
                        "embedding": emb,
                        "expires_at": row[4],
                    }
                )
            except Exception:
                continue
        return users

    def revoke_expired(self) -> int:
        """Purge expired rows; returns number deleted."""
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM voice_users WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            conn.commit()
            deleted = cur.rowcount
        if deleted:
            self._invalidate_cache()
        return deleted

    def list_users(self) -> list[dict]:
        """Return human-readable summary of all non-expired users."""
        users = self._load_active_users()
        summary = []
        for u in users:
            exp = u["expires_at"]
            if exp is None:
                exp_str = "permanent"
            else:
                mins = max(0, int((exp - time.time()) / 60))
                exp_str = f"expires in {mins} min"
            summary.append({"id": u["id"], "label": u["label"], "role": u["role"], "expires": exp_str})
        return summary

    # ------------------------------------------------------------------
    # Embedding extraction
    # ------------------------------------------------------------------

    def extract_embedding(self, raw_bytes: bytes) -> Optional[np.ndarray]:
        """Extract a speaker embedding from raw PCM-16 audio bytes."""
        if len(raw_bytes) < self.MIN_AUDIO_BYTES:
            return None

        # Primary: resemblyzer ECAPA-style embedding (256-dim)
        if self._encoder is not None and HAS_RESEMBLYZER:
            try:
                wav = _pcm16_to_float(raw_bytes)
                wav_proc = preprocess_wav(wav, source_sr=SAMPLE_RATE)
                emb = self._encoder.embed_utterance(wav_proc)
                return emb.astype(np.float32)
            except Exception as exc:
                print(f"[VoiceAuth] resemblyzer embed error ({exc}); falling back")

        # Fallback: lightweight spectral fingerprint (64-dim)
        return _spectral_embedding(raw_bytes)

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll(
        self,
        raw_bytes: bytes,
        role: str = "Admin",
        label: str = "user",
        expires_at: Optional[float] = None,
    ) -> bool:
        """Enroll a new speaker from a raw PCM-16 byte buffer."""
        emb = self.extract_embedding(raw_bytes)
        if emb is None:
            print("[VoiceAuth] enroll: audio too short to extract embedding")
            return False
        emb_json = json.dumps(emb.tolist())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO voice_users (label, role, embedding, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (label, role, emb_json, time.time(), expires_at),
            )
            conn.commit()
        self._invalidate_cache()
        exp_str = "permanent" if expires_at is None else f"until {time.ctime(expires_at)}"
        print(f"[VoiceAuth] Enrolled '{label}' as {role} ({exp_str})")
        return True

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na < 1e-8 or nb < 1e-8:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    def identify(
        self, raw_bytes: bytes
    ) -> Tuple[Optional[str], float, Optional[np.ndarray]]:
        """
        Identify the speaker.

        Returns:
            (role, best_similarity, embedding)
            role is None when the voice does not match any enrolled profile.
        """
        emb = self.extract_embedding(raw_bytes)
        if emb is None:
            return None, 0.0, None

        users = self._load_active_users()
        if not users:
            return None, 0.0, emb

        best_sim = 0.0
        best_role: Optional[str] = None
        for user in users:
            sim = self._cosine_sim(emb, user["embedding"])
            if sim > best_sim:
                best_sim = sim
                best_role = user["role"]

        if best_sim >= SIMILARITY_THRESHOLD:
            return best_role, best_sim, emb

        # Unknown speaker – stash embedding so Admin can grant access
        self.last_unknown_embedding = emb
        self.last_unknown_ts = time.time()
        print(f"[VoiceAuth] Unknown speaker (best_sim={best_sim:.3f})")
        return None, best_sim, emb

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def authorize(
        self, raw_bytes: bytes, intent: str = ""
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Check whether the current speaker may execute *intent*.

        Returns:
            (allowed, role_label, denial_message)
            denial_message is None when access is granted.
        """
        role, sim, _ = self.identify(raw_bytes)

        if role is None:
            return (
                False,
                "unknown",
                "Access denied: voice not recognised. "
                "An Admin must grant you access first.",
            )

        if role == "Admin":
            return True, "Admin", None

        intent_key = intent.strip().lower().replace(" ", "_")

        if role == "User":
            if intent_key in ADMIN_ONLY_INTENTS:
                return (
                    False,
                    "User",
                    f"Access denied: '{intent}' requires Admin role.",
                )
            if intent_key in USER_BLOCKED_INTENTS:
                return (
                    False,
                    "User",
                    f"Access denied: '{intent}' is not allowed for your role.",
                )
            return True, "User", None

        if role == "Guest":
            if intent_key not in GUEST_ALLOWED_INTENTS and intent_key:
                return (
                    False,
                    "Guest",
                    f"Access denied: '{intent}' is not available for Guest access.",
                )
            return True, "Guest", None

        return False, "unknown", "Access denied: unrecognised role."

    # ------------------------------------------------------------------
    # Grant
    # ------------------------------------------------------------------

    def grant_access(
        self, role: str, expires_at: Optional[float] = None
    ) -> bool:
        """
        Register the last unknown speaker with *role*.

        Returns True on success, False if no unknown embedding is available.
        """
        if self.last_unknown_embedding is None:
            print("[VoiceAuth] grant_access: no pending unknown speaker")
            return False

        label = f"voice_{int(self.last_unknown_ts)}"
        emb_json = json.dumps(self.last_unknown_embedding.tolist())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO voice_users (label, role, embedding, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (label, role, emb_json, time.time(), expires_at),
            )
            conn.commit()
        self._invalidate_cache()
        self.last_unknown_embedding = None
        exp_str = "permanent" if expires_at is None else f"until {time.ctime(expires_at)}"
        print(f"[VoiceAuth] Granted {role} access to '{label}' ({exp_str})")
        return True
