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
from typing import Optional, Tuple, List, Union

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

# Three-tier similarity thresholds for confidence-based matching.
SIMILARITY_HIGH = 0.72     # High confidence — auto-accept, eligible for rolling update
SIMILARITY_MEDIUM = 0.62   # Medium confidence — accept with warning
SIMILARITY_LOW = 0.50      # Below this — reject outright

# Kept for backward compat with any external code referencing the old name.
SIMILARITY_THRESHOLD = SIMILARITY_MEDIUM

# Enrollment quality gates
MIN_ENROLLMENT_SNR_DB = 12.0      # minimum signal-to-noise ratio for enrollment audio
MIN_ENROLLMENT_DURATION_MS = 300  # minimum usable audio per phrase
MAX_EMBEDDINGS_PER_USER = 10      # cap stored embeddings for rolling update

# Rolling update rate limit (seconds) — at most one update per user per interval.
_ROLLING_UPDATE_INTERVAL = 60.0

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


def _estimate_snr_db(raw_bytes: bytes) -> float:
    """Estimate signal-to-noise ratio in dB from raw PCM-16."""
    wav = _pcm16_to_float(raw_bytes)
    if len(wav) < 800:
        return 0.0
    frame_size = 400
    hop = 160
    energies = []
    for i in range(0, len(wav) - frame_size, hop):
        frame = wav[i : i + frame_size]
        energies.append(float(np.mean(frame ** 2)))
    if not energies:
        return 0.0
    energies.sort()
    n = max(1, len(energies) // 10)
    noise_energy = float(np.mean(energies[:n]))
    signal_energy = float(np.mean(energies[-n:]))
    if noise_energy < 1e-10:
        return 60.0
    return 10.0 * np.log10(signal_energy / noise_energy)


def _bin_spectrum_to_bands(spectrum: np.ndarray, n_bands: int) -> np.ndarray:
    """Bin a half-spectrum into n_bands log-spaced frequency bands."""
    half = len(spectrum)
    log_idxs = np.unique(
        np.round(np.logspace(0, np.log10(half), n_bands + 1)).astype(int)
    )
    log_idxs = np.clip(log_idxs, 0, half)
    bands = []
    for a, b in zip(log_idxs[:-1], log_idxs[1:]):
        seg = spectrum[a:b]
        bands.append(float(seg.mean()) if len(seg) > 0 else 0.0)
    # Pad or truncate to exactly n_bands
    while len(bands) < n_bands:
        bands.append(0.0)
    return np.array(bands[:n_bands], dtype=np.float32)


def _spectral_embedding(raw_bytes: bytes, n_bands: int = 32) -> np.ndarray:
    """
    Enhanced spectral fingerprint: static mel-band energies + delta features.
    Produces a 64-dimensional vector (32 static + 32 delta).
    """
    wav = _pcm16_to_float(raw_bytes)
    out_dim = n_bands * 2
    if len(wav) < 400:
        return np.zeros(out_dim, dtype=np.float32)

    frame_size = 400
    hop = 160
    frame_bands = []
    for i in range(0, len(wav) - frame_size, hop):
        frame = wav[i : i + frame_size] * np.hanning(frame_size)
        spectrum = np.abs(np.fft.rfft(frame))
        if spectrum.sum() < 1e-4:
            continue
        bands = _bin_spectrum_to_bands(spectrum, n_bands)
        frame_bands.append(bands)

    if not frame_bands:
        return np.zeros(out_dim, dtype=np.float32)

    frame_bands = np.array(frame_bands)  # (N_frames, n_bands)

    # Static features: mean energy per band
    static = np.mean(frame_bands, axis=0)

    # Delta features: mean of frame-to-frame differences (captures dynamics)
    if len(frame_bands) > 1:
        deltas = np.diff(frame_bands, axis=0)
        delta_mean = np.mean(np.abs(deltas), axis=0)
    else:
        delta_mean = np.zeros(n_bands, dtype=np.float32)

    emb = np.concatenate([static, delta_mean]).astype(np.float32)
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

    _USERS_CACHE_TTL = 5.0  # seconds before reloading active users from DB

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.db_path = os.path.join(base_dir, self.DB_FILE)
        self._encoder = None
        # Holds the embedding of the most-recent unknown speaker so that an
        # Admin can later grant them access via voice command.
        self.last_unknown_embedding: Optional[np.ndarray] = None
        self.last_unknown_ts: float = 0.0
        self._user_count_cache: Optional[int] = None
        # Persistent connection + user list cache
        self._conn: Optional[sqlite3.Connection] = None
        self._active_users_cache: Optional[list] = None
        self._active_users_cache_ts: float = 0.0
        # Rolling update rate limiter: {user_id: last_update_timestamp}
        self._rolling_update_ts: dict[int, float] = {}
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

    def _get_conn(self) -> sqlite3.Connection:
        """Return a persistent SQLite connection (created once)."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                label      TEXT    NOT NULL,
                role       TEXT    NOT NULL DEFAULT 'Guest',
                embedding  TEXT    NOT NULL DEFAULT '[]',
                created_at REAL    NOT NULL,
                expires_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_embeddings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES voice_users(id) ON DELETE CASCADE,
                embedding   TEXT    NOT NULL,
                snr_db      REAL,
                duration_ms INTEGER,
                created_at  REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ve_user_id
            ON voice_embeddings(user_id)
        """)
        conn.commit()
        self._migrate_legacy_embeddings()

    def _migrate_legacy_embeddings(self) -> None:
        """Move single-embedding rows from voice_users into voice_embeddings."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, embedding FROM voice_users WHERE embedding != '[]' AND embedding != ''"
        ).fetchall()
        migrated = 0
        for uid, emb_json in rows:
            try:
                arr = json.loads(emb_json)
                if not arr or not isinstance(arr, list) or not isinstance(arr[0], (int, float)):
                    continue
            except Exception:
                continue
            # Check if already migrated
            existing = conn.execute(
                "SELECT COUNT(*) FROM voice_embeddings WHERE user_id = ?", (uid,)
            ).fetchone()[0]
            if existing > 0:
                continue
            conn.execute(
                "INSERT INTO voice_embeddings (user_id, embedding, created_at) VALUES (?, ?, ?)",
                (uid, emb_json, time.time()),
            )
            migrated += 1
        if migrated:
            conn.commit()
            print(f"[VoiceAuth] Migrated {migrated} legacy embedding(s) to voice_embeddings table")

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _invalidate_cache(self) -> None:
        self._user_count_cache = None
        self._active_users_cache = None
        self._active_users_cache_ts = 0.0

    def has_any_user(self) -> bool:
        if self._user_count_cache is not None:
            return self._user_count_cache > 0
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM voice_users").fetchone()
        count = row[0] if row else 0
        self._user_count_cache = count
        return count > 0

    def has_admin(self) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM voice_users WHERE role='Admin'"
        ).fetchone()
        return (row[0] if row else 0) > 0

    def _load_active_users(self) -> list[dict]:
        """Return all non-expired users with their embedding lists (cached)."""
        now = time.time()
        if (
            self._active_users_cache is not None
            and (now - self._active_users_cache_ts) < self._USERS_CACHE_TTL
        ):
            return self._active_users_cache

        conn = self._get_conn()
        # Load user metadata
        user_rows = conn.execute(
            "SELECT id, label, role, expires_at "
            "FROM voice_users "
            "WHERE expires_at IS NULL OR expires_at > ?",
            (now,),
        ).fetchall()

        if not user_rows:
            self._active_users_cache = []
            self._active_users_cache_ts = now
            return []

        user_ids = [r[0] for r in user_rows]
        placeholders = ",".join("?" * len(user_ids))
        emb_rows = conn.execute(
            f"SELECT user_id, embedding FROM voice_embeddings WHERE user_id IN ({placeholders})",
            user_ids,
        ).fetchall()

        # Group embeddings by user_id
        emb_map: dict[int, list[np.ndarray]] = {}
        for uid, emb_json in emb_rows:
            try:
                arr = np.array(json.loads(emb_json), dtype=np.float32)
                emb_map.setdefault(uid, []).append(arr)
            except Exception:
                continue

        users = []
        for row in user_rows:
            uid = row[0]
            embeddings = emb_map.get(uid, [])
            if not embeddings:
                continue  # Skip users with no valid embeddings
            users.append(
                {
                    "id": uid,
                    "label": row[1],
                    "role": row[2],
                    "embeddings": embeddings,
                    "expires_at": row[3],
                }
            )

        self._active_users_cache = users
        self._active_users_cache_ts = now
        return users

    def revoke_expired(self) -> int:
        """Purge expired rows; returns number deleted."""
        now = time.time()
        conn = self._get_conn()
        # Get IDs to delete (cascade will remove embeddings)
        expired_ids = conn.execute(
            "SELECT id FROM voice_users WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        ).fetchall()
        if not expired_ids:
            return 0
        id_list = [r[0] for r in expired_ids]
        placeholders = ",".join("?" * len(id_list))
        conn.execute(f"DELETE FROM voice_embeddings WHERE user_id IN ({placeholders})", id_list)
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
            n_emb = len(u.get("embeddings", []))
            summary.append({
                "id": u["id"],
                "label": u["label"],
                "role": u["role"],
                "expires": exp_str,
                "embeddings": n_emb,
            })
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

        # Fallback: enhanced spectral fingerprint (64-dim: 32 static + 32 delta)
        return _spectral_embedding(raw_bytes)

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll(
        self,
        audio: Union[bytes, List[bytes]],
        role: str = "Admin",
        label: str = "user",
        expires_at: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Enroll a new speaker from audio data.

        Args:
            audio: Either a single PCM-16 buffer (legacy) or a list of
                   per-phrase PCM-16 buffers for multi-embedding enrollment.

        Returns:
            (success, message) tuple with quality feedback.
        """
        # Normalize input: single buffer → list of one
        if isinstance(audio, bytes):
            segments = [audio]
        else:
            segments = list(audio)

        embeddings: list[np.ndarray] = []
        quality_notes: list[str] = []

        for i, seg in enumerate(segments):
            duration_ms = len(seg) / (SAMPLE_RATE * 2) * 1000
            if duration_ms < MIN_ENROLLMENT_DURATION_MS:
                quality_notes.append(f"Phrase {i+1}: too short ({duration_ms:.0f}ms)")
                continue

            snr = _estimate_snr_db(seg)
            if snr < MIN_ENROLLMENT_SNR_DB:
                quality_notes.append(f"Phrase {i+1}: noisy (SNR {snr:.1f}dB)")
                # Still try to extract — may be usable
                emb = self.extract_embedding(seg)
                if emb is not None:
                    embeddings.append(emb)
                continue

            emb = self.extract_embedding(seg)
            if emb is not None:
                embeddings.append(emb)
            else:
                quality_notes.append(f"Phrase {i+1}: embedding extraction failed")

        min_required = max(1, len(segments) // 2)  # Need at least half
        if len(embeddings) < min_required:
            msg = f"Only {len(embeddings)}/{len(segments)} usable phrases. Need at least {min_required}."
            if quality_notes:
                msg += " Issues: " + "; ".join(quality_notes)
            print(f"[VoiceAuth] enroll failed: {msg}")
            return False, msg

        # Insert user row
        conn = self._get_conn()
        now = time.time()
        cur = conn.execute(
            "INSERT INTO voice_users (label, role, embedding, created_at, expires_at) "
            "VALUES (?, ?, '[]', ?, ?)",
            (label, role, now, expires_at),
        )
        user_id = cur.lastrowid

        # Insert individual embeddings
        for j, emb in enumerate(embeddings):
            seg = segments[j] if j < len(segments) else segments[-1]
            snr = _estimate_snr_db(seg)
            duration_ms = len(seg) / (SAMPLE_RATE * 2) * 1000
            conn.execute(
                "INSERT INTO voice_embeddings (user_id, embedding, snr_db, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, json.dumps(emb.tolist()), snr, int(duration_ms), now),
            )

        conn.commit()
        self._invalidate_cache()
        exp_str = "permanent" if expires_at is None else f"until {time.ctime(expires_at)}"
        msg = f"Enrolled with {len(embeddings)}/{len(segments)} phrases"
        if quality_notes:
            msg += f" ({'; '.join(quality_notes)})"
        print(f"[VoiceAuth] Enrolled '{label}' as {role} ({exp_str}) — {len(embeddings)} embeddings")
        return True, msg

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        if a.shape != b.shape:
            return 0.0  # Dimension mismatch (e.g., old 64-dim vs new 64-dim)
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na < 1e-8 or nb < 1e-8:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    def identify(
        self, raw_bytes: bytes
    ) -> Tuple[Optional[str], float, Optional[np.ndarray], str]:
        """
        Identify the speaker using multi-embedding matching.

        Returns:
            (role, best_similarity, embedding, confidence)
            confidence is one of: "high", "medium", "low", "none"
            role is None when the voice does not match any enrolled profile.
        """
        emb = self.extract_embedding(raw_bytes)
        if emb is None:
            return None, 0.0, None, "none"

        users = self._load_active_users()
        if not users:
            return None, 0.0, emb, "none"

        best_sim = 0.0
        best_role: Optional[str] = None
        best_user_id: Optional[int] = None

        for user in users:
            # Match against ALL stored embeddings for this user, take the max
            user_best = 0.0
            for stored_emb in user["embeddings"]:
                sim = self._cosine_sim(emb, stored_emb)
                if sim > user_best:
                    user_best = sim
            if user_best > best_sim:
                best_sim = user_best
                best_role = user["role"]
                best_user_id = user["id"]

        # Classify confidence
        if best_sim >= SIMILARITY_HIGH:
            confidence = "high"
        elif best_sim >= SIMILARITY_MEDIUM:
            confidence = "medium"
        elif best_sim >= SIMILARITY_LOW:
            confidence = "low"
        else:
            confidence = "none"

        if confidence in ("high", "medium"):
            # Rolling embedding update on high-confidence matches
            if confidence == "high" and best_user_id is not None:
                self._rolling_update(best_user_id, emb)
            return best_role, best_sim, emb, confidence

        # Unknown speaker – stash embedding so Admin can grant access
        self.last_unknown_embedding = emb
        self.last_unknown_ts = time.time()
        print(
            f"[VoiceAuth] Unknown speaker (best_sim={best_sim:.3f}, confidence={confidence})",
            flush=True,
        )
        return None, best_sim, emb, confidence

    # ------------------------------------------------------------------
    # Rolling embedding update
    # ------------------------------------------------------------------

    def _rolling_update(self, user_id: int, new_embedding: np.ndarray) -> None:
        """On high-confidence match, blend new embedding into user profile.

        Keeps up to MAX_EMBEDDINGS_PER_USER embeddings per user.
        Rate-limited to avoid DB writes on every single command.
        """
        now = time.time()
        last = self._rolling_update_ts.get(user_id, 0.0)
        if (now - last) < _ROLLING_UPDATE_INTERVAL:
            return

        self._rolling_update_ts[user_id] = now
        conn = self._get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM voice_embeddings WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        emb_json = json.dumps(new_embedding.tolist())

        if count < MAX_EMBEDDINGS_PER_USER:
            conn.execute(
                "INSERT INTO voice_embeddings (user_id, embedding, created_at) VALUES (?, ?, ?)",
                (user_id, emb_json, now),
            )
        else:
            # Replace the oldest embedding
            oldest = conn.execute(
                "SELECT id FROM voice_embeddings WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            if oldest:
                conn.execute(
                    "UPDATE voice_embeddings SET embedding = ?, created_at = ? WHERE id = ?",
                    (emb_json, now, oldest[0]),
                )
        conn.commit()
        self._invalidate_cache()

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
        role, sim, _, confidence = self.identify(raw_bytes)

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
        now = time.time()
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO voice_users (label, role, embedding, created_at, expires_at) "
            "VALUES (?, ?, '[]', ?, ?)",
            (label, role, now, expires_at),
        )
        user_id = cur.lastrowid
        conn.execute(
            "INSERT INTO voice_embeddings (user_id, embedding, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(self.last_unknown_embedding.tolist()), now),
        )
        conn.commit()
        self._invalidate_cache()
        self.last_unknown_embedding = None
        exp_str = "permanent" if expires_at is None else f"until {time.ctime(expires_at)}"
        print(f"[VoiceAuth] Granted {role} access to '{label}' ({exp_str})")
        return True
