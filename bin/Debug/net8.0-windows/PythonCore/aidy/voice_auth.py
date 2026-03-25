"""voice_auth.py – Voice Biometric Authentication and RBAC for Aidy.

Speaker verification uses SpeechBrain ECAPA-TDNN as the primary encoder.
Falls back to resemblyzer, then to a lightweight spectral fingerprint.

Database: SQLite  (voice_profiles.db next to main.py)
Roles:    Admin   – unrestricted (verified owner)
          User    – trusted member, blocked from admin-only intents
          Guest   – safe subset only (delegated sandbox)
          Unknown – 0% access, instant block
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
import threading
import time
from collections import deque
from typing import Optional, Tuple, List, Union

import numpy as np
from scipy.signal import butter, sosfilt

from .debug_logger import get_debug_logger

# ---------------------------------------------------------------------------
# Optional: SpeechBrain ECAPA-TDNN (primary, most robust)
# ---------------------------------------------------------------------------
HAS_SPEECHBRAIN = False
try:
    from speechbrain.inference.speaker import EncoderClassifier as _SBEncoder  # type: ignore
    import torch as _torch
    HAS_SPEECHBRAIN = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Optional: resemblyzer speaker encoder (secondary fallback)
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
# Tuned for hybrid matching (70% max + 30% mean-of-top-3) with SpeechBrain
# ECAPA-TDNN which produces lower absolute cosine similarities than
# resemblyzer but with much better speaker discrimination.
SIMILARITY_HIGH = 0.43     # High confidence — auto-accept, eligible for rolling update
SIMILARITY_MEDIUM = 0.33   # Medium confidence — accept with warning
SIMILARITY_LOW = 0.23      # Below this — reject outright

# Kept for backward compat with any external code referencing the old name.
SIMILARITY_THRESHOLD = SIMILARITY_MEDIUM

# Enrollment quality gates
MIN_ENROLLMENT_SNR_DB = 12.0          # ideal signal-to-noise ratio for enrollment audio
MIN_ENROLLMENT_SNR_DB_DEGRADED = 8.0  # fallback for noisy environments (accepted on 2nd+ attempt)
MIN_ENROLLMENT_DURATION_MS = 300      # minimum usable audio per phrase
MAX_EMBEDDINGS_PER_USER = 10      # cap stored embeddings for rolling update
MIN_MATURE_EMBEDDINGS = 3         # users with fewer embeddings get relaxed (medium) threshold

# Rolling update rate limit (seconds) — at most one update per user per interval.
_ROLLING_UPDATE_INTERVAL = 60.0

# Intents that require Admin role regardless of everything else.
# User and Guest roles are always blocked from these.
ADMIN_ONLY_INTENTS: set[str] = {"shutdown", "restart", "close_everything", "grant_access"}

# Intents that "Guest" role IS allowed to run (allow-list).
# Anything not in this set is blocked for Guest.
GUEST_ALLOWED_INTENTS: set[str] = {
    "screenshot",
    "open_app",
    "close_app",
    "close_active",
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
    "repeat",
    "undo_action",
}

# Voice-command phrases that the Vosk grammar should recognise for grant ops.
GRANT_GRAMMAR_PHRASES: list[str] = [
    "grant access",
    "grant admin access",
    "grant user access",
    "grant guest access",
    "revoke access",
    "revoke",
    "delete user one",
    "delete user two",
    "delete user three",
    "delete user four",
    "delete user five",
    "remove user one",
    "remove user two",
    "remove user three",
    "remove user four",
    "remove user five",
    "list users",
]

# Grammar for the role question ("User or Admin?")
GRANT_ROLE_GRAMMAR: list[str] = [
    "admin", "user", "guest",
    "admin access", "user access", "guest access",
    "cancel", "stop", "never mind",
]

# Grammar for the duration question ("How long?")
# Vosk needs the phrases it should recognise — include a wide range.
GRANT_DURATION_GRAMMAR: list[str] = [
    # bare numbers (spoken as words)
    "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
    "fifteen", "twenty", "twenty five", "thirty",
    "forty", "forty five", "fifty", "sixty",
    "ninety", "one hundred twenty",
    # bare digits (Vosk sometimes outputs these)
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    "15", "20", "25", "30", "45", "60", "90", "120",
    # N minutes
    "one minute", "two minutes", "three minutes", "four minutes", "five minutes",
    "ten minutes", "fifteen minutes", "twenty minutes", "twenty five minutes",
    "thirty minutes", "forty five minutes", "sixty minutes",
    "ninety minutes", "one hundred twenty minutes",
    # hours
    "half an hour", "half hour",
    "one hour", "one and a half hours", "two hours", "three hours",
    "four hours", "five hours", "six hours", "twelve hours",
    "twenty four hours",
    # days
    "one day", "two days", "three days",
    # permanent
    "permanent", "permanently", "forever",
    # cancel
    "cancel", "stop", "never mind",
]

# Legacy map kept for backward compatibility — new code should call
# parse_grant_duration_minutes() which handles arbitrary natural language.
GRANT_DURATION_WORD_TO_MINUTES: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "ninety": 90,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "15": 15, "20": 20, "25": 25, "30": 30, "45": 45, "60": 60, "90": 90, "120": 120,
    "one minute": 1, "two minutes": 2, "three minutes": 3, "four minutes": 4, "five minutes": 5,
    "ten minutes": 10, "fifteen minutes": 15, "twenty minutes": 20, "thirty minutes": 30,
    "forty five minutes": 45, "sixty minutes": 60, "ninety minutes": 90,
    "one hundred twenty minutes": 120,
    "half an hour": 30, "half hour": 30,
    "one hour": 60, "one and a half hours": 90,
    "two hours": 120, "three hours": 180, "four hours": 240,
    "five hours": 300, "six hours": 360, "twelve hours": 720,
    "twenty four hours": 1440,
    "one day": 1440, "two days": 2880, "three days": 4320,
    "permanent": -1, "permanently": -1, "forever": -1,
}

# ---------------------------------------------------------------------------
# Duration parser for "for 2 hours", "for 30 minutes", "for 1 day"
# ---------------------------------------------------------------------------
_NUMBER_WORDS = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
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


def _tokenise_number(tokens: list[str]) -> Tuple[Optional[int], int]:
    """Parse a natural-language number from the start of *tokens*.

    Handles:
        "5"                       → 5
        "twenty"                  → 20
        "twenty five"             → 25
        "one hundred twenty"      → 120
        "one hundred"             → 100

    Returns (value, tokens_consumed).  (None, 0) on failure.
    """
    if not tokens:
        return None, 0

    # Try bare digit first
    if tokens[0].isdigit():
        return int(tokens[0]), 1

    total = 0
    consumed = 0
    for tok in tokens:
        if tok in _NUMBER_WORDS:
            w = _NUMBER_WORDS[tok]
            if w == 100:
                # "one hundred" — multiply accumulator by 100
                total = (total or 1) * 100
            else:
                total += w
            consumed += 1
        else:
            break

    if consumed == 0:
        return None, 0
    return total, consumed


def parse_grant_duration_minutes(text: str) -> Optional[int]:
    """Parse a flexible, natural-language duration into total minutes.

    Accepts a wide range of spoken inputs:
        "5"                       → 5 min
        "thirty"                  → 30 min
        "twenty five"             → 25 min
        "120 minutes"             → 120 min
        "2 hours"                 → 120 min
        "one and a half hours"    → 90 min
        "half an hour"            → 30 min
        "half hour"               → 30 min
        "one day"                 → 1440 min
        "permanent" / "forever"   → -1 (sentinel for no expiry)

    Returns the total minutes, -1 for permanent, or None on failure.
    """
    t = " ".join((text or "").strip().lower().split())
    if not t:
        return None

    # ── 1. Quick-path: exact match in the legacy map ─────────────
    legacy = GRANT_DURATION_WORD_TO_MINUTES.get(t)
    if legacy is not None:
        return legacy

    # ── 2. Permanent / forever ───────────────────────────────────
    if t in ("permanent", "permanently", "forever"):
        return -1

    tokens = re.findall(r"[a-z0-9]+", t)
    if not tokens:
        return None

    # ── 3. "half an hour" / "half hour" ──────────────────────────
    joined = " ".join(tokens)
    if joined in ("half an hour", "half hour", "half"):
        return 30

    # ── 4. "N and a half hours" ──────────────────────────────────
    if "half" in tokens:
        # Pattern: <number> and a half <unit>
        half_idx = tokens.index("half")
        num_tokens = [tok for tok in tokens[:half_idx] if tok not in ("and", "a")]
        n, _ = _tokenise_number(num_tokens)
        # Find unit after "half"
        unit_tokens = tokens[half_idx + 1:]
        unit_tok = unit_tokens[0] if unit_tokens else "hours"
        multiplier_sec = _UNIT_SECONDS.get(unit_tok, 3600)  # default hours
        if n is not None:
            total_sec = n * multiplier_sec + multiplier_sec // 2
            return max(1, total_sec // 60)
        else:
            # bare "half hour" handled above; "half" alone
            return 30

    # ── 5. General: <number> [unit] ──────────────────────────────
    n, consumed = _tokenise_number(tokens)
    if n is None:
        return None

    remaining = tokens[consumed:]
    if remaining:
        unit_tok = remaining[0]
        multiplier_sec = _UNIT_SECONDS.get(unit_tok)
        if multiplier_sec is not None:
            return max(1, (n * multiplier_sec) // 60)

    # No unit — treat bare number as minutes
    return n if n > 0 else None


def _parse_grant_command(text: str) -> Optional[dict]:
    """
    Detect and parse a grant-access command.

    Returns:
        {"role": "Admin"|"Guest", "expires_at": float|None}
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


_WORD_TO_DIGIT: dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def _parse_delete_user_command(text: str) -> Optional[str]:
    """Detect 'delete user <label>' or 'remove user <label>'.

    Accepts:
        "delete user 1"            → "1"
        "delete user one"          → "1"
        "delete user one guest"    → "1 guest"
        "delete user user_2(guest)" → "user_2(guest)"
        "remove user three"        → "3"

    Returns the label/query portion, or None if not a delete-user command.
    """
    t = " ".join((text or "").strip().lower().split())
    for prefix in ("delete user ", "remove user "):
        if t.startswith(prefix):
            label = t[len(prefix):].strip()
            if not label:
                continue
            # Convert word-numbers to digits: "one" → "1", "two guest" → "2 guest"
            tokens = label.split()
            if tokens[0] in _WORD_TO_DIGIT:
                tokens[0] = _WORD_TO_DIGIT[tokens[0]]
                label = " ".join(tokens)
            return label
    return None


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _pcm16_to_float(raw_bytes: bytes) -> np.ndarray:
    """Convert raw PCM-16 little-endian bytes → float32 in [-1, 1]."""
    n = len(raw_bytes) // 2
    samples = struct.unpack(f"<{n}h", raw_bytes[: n * 2])
    return np.array(samples, dtype=np.float32) / 32768.0


# ---------------------------------------------------------------------------
# Channel normalization – removes microphone spectral coloring
# ---------------------------------------------------------------------------

# Pre-computed Butterworth bandpass filter coefficients (300–3400 Hz at 16 kHz).
# This keeps only the speech-relevant band and discards mic-specific
# low-frequency rumble and high-frequency coloring.
_BP_SOS = butter(5, [300, 3400], btype="bandpass", fs=SAMPLE_RATE, output="sos")


def _channel_normalize(wav: np.ndarray) -> np.ndarray:
    """Remove microphone/channel coloring from a speech waveform.

    Steps:
    1. Pre-emphasis (α=0.97) — flattens spectral tilt introduced by the mic.
    2. Bandpass 300–3400 Hz — discards non-voice frequencies where mic
       characteristics dominate.
    3. Per-utterance cepstral mean subtraction in the spectral domain —
       subtracts the average log-spectrum (≈ the channel transfer function)
       so only speaker-specific variation remains.
    """
    if len(wav) < 400:
        return wav

    # 1. Pre-emphasis: y[n] = x[n] - 0.97·x[n-1]
    wav = np.append(wav[0], wav[1:] - 0.97 * wav[:-1])

    # 2. Bandpass filter
    wav = sosfilt(_BP_SOS, wav).astype(np.float32)

    # 3. Per-utterance spectral mean subtraction (CMS)
    #    Compute STFT, subtract mean log-magnitude per frequency bin,
    #    then reconstruct.  This removes the channel transfer function.
    frame_size = 400  # 25 ms at 16 kHz
    hop = 160         # 10 ms
    n_frames = (len(wav) - frame_size) // hop
    if n_frames >= 2:
        window = np.hanning(frame_size).astype(np.float32)
        # Extract frames
        shape = (n_frames, frame_size)
        strides = (wav.strides[0] * hop, wav.strides[0])
        frames = np.lib.stride_tricks.as_strided(wav, shape=shape, strides=strides).copy()
        frames *= window

        # STFT
        spectra = np.fft.rfft(frames, axis=1)
        mag = np.abs(spectra)
        phase = np.angle(spectra)

        # Subtract mean log-magnitude (= channel transfer function)
        log_mag = np.log(mag + 1e-10)
        mean_log_mag = np.mean(log_mag, axis=0, keepdims=True)
        log_mag -= mean_log_mag

        # Reconstruct
        mag_norm = np.exp(log_mag)
        spectra_norm = mag_norm * np.exp(1j * phase)
        frames_norm = np.fft.irfft(spectra_norm, n=frame_size, axis=1).real.astype(np.float32)

        # Overlap-add reconstruction
        out = np.zeros(len(wav), dtype=np.float32)
        win_sum = np.zeros(len(wav), dtype=np.float32)
        for i in range(n_frames):
            start = i * hop
            out[start:start + frame_size] += frames_norm[i] * window
            win_sum[start:start + frame_size] += window ** 2
        # Normalize by window overlap
        valid = win_sum > 1e-8
        out[valid] /= win_sum[valid]
        wav = out

    # Normalize amplitude
    peak = np.max(np.abs(wav))
    if peak > 1e-8:
        wav = wav / peak * 0.95

    return wav


# ---------------------------------------------------------------------------
# Neural VAD trimming (Silero VAD primary, energy-based fallback)
# ---------------------------------------------------------------------------

_silero_vad = None
_silero_vad_utils = None
_silero_vad_failed = False  # avoid retrying after first failure


def _load_silero_vad() -> bool:
    """Lazy-load Silero VAD model (requires torch, already available via SpeechBrain)."""
    global _silero_vad, _silero_vad_utils, _silero_vad_failed
    if _silero_vad is not None:
        return True
    if _silero_vad_failed or not HAS_SPEECHBRAIN:
        return False
    try:
        model, utils = _torch.hub.load(
            "snakers4/silero-vad", "silero_vad", trust_repo=True
        )
        _silero_vad = model
        _silero_vad_utils = utils
        print("[VoiceAuth] Silero VAD loaded successfully")
        return True
    except Exception as exc:
        _silero_vad_failed = True
        print(f"[VoiceAuth] Silero VAD load failed ({exc}); using energy fallback")
        return False


def _energy_trim_silence(
    wav: np.ndarray,
    threshold_db: float = -40.0,
    frame_ms: int = 25,
    hop_ms: int = 10,
    pad_frames: int = 3,
) -> np.ndarray:
    """Trim leading and trailing silence using frame energy (fallback)."""
    frame_size = int(SAMPLE_RATE * frame_ms / 1000)
    hop = int(SAMPLE_RATE * hop_ms / 1000)
    n_frames = (len(wav) - frame_size) // hop
    if n_frames <= 0:
        return wav

    shape = (n_frames, frame_size)
    strides = (wav.strides[0] * hop, wav.strides[0])
    frames = np.lib.stride_tricks.as_strided(wav, shape=shape, strides=strides)
    energies_db = 10.0 * np.log10(np.mean(frames ** 2, axis=1) + 1e-10)

    active = np.where(energies_db > threshold_db)[0]
    if len(active) == 0:
        return wav

    start_frame = max(0, active[0] - pad_frames)
    end_frame = min(n_frames - 1, active[-1] + pad_frames)
    start_sample = start_frame * hop
    end_sample = min(len(wav), end_frame * hop + frame_size)
    return wav[start_sample:end_sample]


def _vad_trim_silence(wav: np.ndarray, pad_samples: int = 800) -> np.ndarray:
    """Trim non-speech regions using Silero VAD.

    Falls back to energy-based trimming if Silero is unavailable.
    NOTE: Silero VAD requires 8 kHz or 16 kHz audio.  Our SAMPLE_RATE is
    16000 so no resampling is needed.
    """
    if len(wav) < 1600:
        return wav
    if not _load_silero_vad():
        return _energy_trim_silence(wav)
    try:
        get_speech_timestamps = _silero_vad_utils[0]
        audio_tensor = _torch.tensor(wav)
        assert SAMPLE_RATE in (8000, 16000), (
            f"Silero VAD needs 8/16 kHz, got {SAMPLE_RATE}"
        )
        timestamps = get_speech_timestamps(
            audio_tensor, _silero_vad, sampling_rate=SAMPLE_RATE
        )
        if not timestamps:
            return wav
        start = max(0, timestamps[0]["start"] - pad_samples)
        end = min(len(wav), timestamps[-1]["end"] + pad_samples)
        return wav[start:end]
    except Exception:
        return _energy_trim_silence(wav)


def _estimate_snr_db(raw_bytes: bytes) -> float:
    """Estimate signal-to-noise ratio in dB from raw PCM-16."""
    wav = _pcm16_to_float(raw_bytes)
    if len(wav) < 800:
        return 0.0
    frame_size = 400
    hop = 160
    n_frames = (len(wav) - frame_size) // hop
    if n_frames <= 0:
        return 0.0

    # Vectorized frame extraction
    shape = (n_frames, frame_size)
    strides = (wav.strides[0] * hop, wav.strides[0])
    frames = np.lib.stride_tricks.as_strided(wav, shape=shape, strides=strides)

    # Compute energy per frame in one vectorized operation
    energies = np.mean(frames ** 2, axis=1)
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


_HANNING_400 = np.hanning(400).astype(np.float32)


def _spectral_embedding(raw_bytes: bytes, n_bands: int = 32, wav_override: Optional[np.ndarray] = None) -> np.ndarray:
    """
    Enhanced spectral fingerprint: static mel-band energies + delta features.
    Produces a 64-dimensional vector (32 static + 32 delta).
    If wav_override is provided, uses it instead of decoding raw_bytes.
    """
    wav = wav_override if wav_override is not None else _pcm16_to_float(raw_bytes)
    out_dim = n_bands * 2
    if len(wav) < 400:
        return np.zeros(out_dim, dtype=np.float32)

    frame_size = 400
    hop = 160
    n_frames = (len(wav) - frame_size) // hop
    if n_frames <= 0:
        return np.zeros(out_dim, dtype=np.float32)

    # Vectorized frame extraction using stride tricks
    shape = (n_frames, frame_size)
    strides = (wav.strides[0] * hop, wav.strides[0])
    frames = np.lib.stride_tricks.as_strided(wav, shape=shape, strides=strides).copy()

    # Apply window and FFT to all frames at once
    frames *= _HANNING_400
    spectra = np.abs(np.fft.rfft(frames, axis=1))

    # Filter out silent frames
    valid_mask = spectra.sum(axis=1) >= 1e-4
    spectra = spectra[valid_mask]
    if len(spectra) == 0:
        return np.zeros(out_dim, dtype=np.float32)

    # Bin each spectrum to bands
    frame_bands = np.array([_bin_spectrum_to_bands(s, n_bands) for s in spectra])

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

    def __init__(self, base_dir: str, async_encoder_init: bool = False):
        self.base_dir = base_dir
        self.db_path = os.path.join(base_dir, self.DB_FILE)
        self._debug = get_debug_logger(os.path.join(base_dir, "KILLER_BUG.txt"))
        self._encoder = None          # resemblyzer (secondary)
        self._sb_encoder = None       # SpeechBrain ECAPA-TDNN (primary)
        self._encoder_ready = threading.Event()  # Signals when encoder is loaded and warmed
        # Holds recent embeddings of unknown speakers so that an Admin can
        # grant them access via voice command.  We accumulate up to
        # _MAX_UNKNOWN_BUFFER embeddings within _UNKNOWN_WINDOW_SEC seconds
        # so that grant_access can enroll multiple phrases at once.
        self._unknown_embeddings: list[np.ndarray] = []
        self._unknown_first_ts: float = 0.0
        self._UNKNOWN_WINDOW_SEC: float = 60.0   # reset buffer after 60s gap
        self._MAX_UNKNOWN_BUFFER: int = 5
        # Legacy alias kept for any external code that reads it
        self.last_unknown_embedding: Optional[np.ndarray] = None
        self.last_unknown_ts: float = 0.0
        self._user_count_cache: Optional[int] = None
        # Persistent connection + user list cache
        self._conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()  # Serialise all DB writes
        self._active_users_cache: Optional[list] = None
        self._active_users_cache_ts: float = 0.0
        # Rolling update rate limiter: {user_id: last_update_timestamp}
        self._rolling_update_ts: dict[int, float] = {}
        self._init_db()
        if async_encoder_init:
            # Load encoder in background thread to avoid blocking startup
            threading.Thread(target=self._load_encoder, daemon=True).start()
        else:
            # Load encoder synchronously (moves 28s cold start to init time)
            self._load_encoder()
            self._encoder_ready.set()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    # Path to the bundled silent WAV used for cold-start warm-up.
    # 0.5 s of silence at 16 kHz / 16-bit mono — no microphone access needed.
    _WARMUP_WAV = os.path.join("Assets", "voice", "warmup_silence.wav")

    def _resolve_warmup_wav(self) -> Optional[str]:
        """Locate the bundled silent WAV file for model warm-up."""
        candidates = [
            os.path.join(self.base_dir, self._WARMUP_WAV),
            os.path.join(self.base_dir, "Assets", "voice", "warmup_silence.wav"),
            os.path.join(os.path.dirname(self.base_dir), "Assets", "voice", "warmup_silence.wav"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def _read_warmup_pcm(self) -> Optional[bytes]:
        """Read the silent WAV and return raw PCM-16 bytes.

        Returns None if the file is missing or unreadable.  This is a
        best-effort helper — callers fall back to a synthetic buffer.
        """
        import wave as _wave_mod
        path = self._resolve_warmup_wav()
        if path is None:
            return None
        try:
            with _wave_mod.open(path, "rb") as wf:
                return wf.readframes(wf.getnframes())
        except Exception as exc:
            print(f"[VoiceAuth] Failed to read warmup WAV ({exc})")
            return None

    def _warmup_from_file(self) -> None:
        """Run the full extract_embedding() pipeline on the silent WAV.

        This forces every layer of the inference pipeline (file I/O,
        PCM→float conversion, channel normalisation, encoder forward
        pass, tensor → numpy) to execute once, moving all cold-start
        costs into this invisible background call.  The resulting
        embedding is intentionally discarded.
        """
        pcm = self._read_warmup_pcm()
        if pcm is None:
            # Fallback: synthesise 0.5 s of silence in memory
            pcm = b"\x00\x00" * (SAMPLE_RATE // 2)
        t0 = time.time()
        _ = self.extract_embedding(pcm)        # result discarded
        elapsed_ms = (time.time() - t0) * 1000
        print(f"[VoiceAuth] Warmup inference complete ({elapsed_ms:.0f} ms)")

    def _load_encoder(self) -> None:
        # Try SpeechBrain ECAPA-TDNN first (best speaker discrimination)
        if HAS_SPEECHBRAIN:
            try:
                device = "cuda" if _torch.cuda.is_available() else "cpu"
                model_dir = os.path.join(self.base_dir, "speechbrain_models", "ecapa")
                # Load from local directory (pre-downloaded model)
                self._sb_encoder = _SBEncoder.from_hparams(
                    source=model_dir,
                    run_opts={"device": device},
                )
                print(f"[VoiceAuth] SpeechBrain ECAPA-TDNN loaded on {device}")
                self._encoder_ready.set()
                # Full pipeline warm-up using the bundled silent WAV
                self._warmup_from_file()
                return
            except Exception as exc:
                print(f"[VoiceAuth] SpeechBrain failed ({exc}); trying resemblyzer")
                self._sb_encoder = None

        # Fallback to resemblyzer
        try:
            if not HAS_RESEMBLYZER:
                self._encoder_ready.set()
                return
            self._encoder = VoiceEncoder()
            print("[VoiceAuth] resemblyzer encoder loaded")
            self._encoder_ready.set()
            # Full pipeline warm-up using the bundled silent WAV
            self._warmup_from_file()
        except Exception as exc:
            print(f"[VoiceAuth] resemblyzer failed to load ({exc}); using spectral fallback")
            self._encoder = None
            self._encoder_ready.set()
            # Even with spectral fallback, warm up the pipeline
            self._warmup_from_file()

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

    def revoke_last(self) -> Tuple[bool, str]:
        """Revoke the most recently granted non-admin user.

        Falls back to cleaning expired entries if no non-admin user exists.
        Returns (success, human-readable message).
        """
        conn = self._get_conn()

        # First try: find the most recently created non-Admin user
        row = conn.execute(
            "SELECT id, label, role FROM voice_users "
            "WHERE role != 'Admin' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        print(f"[VoiceAuth] revoke_last: found row={row}", flush=True)

        if row is not None:
            uid, label, role = row
            conn.execute("DELETE FROM voice_embeddings WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM voice_users WHERE id = ?", (uid,))
            conn.commit()
            self._invalidate_cache()
            print(f"[VoiceAuth] Revoked access for '{label}' (role={role})", flush=True)
            return True, label

        # Fallback: clean expired entries
        expired = self.revoke_expired()
        if expired:
            return True, f"{expired} expired"

        return False, ""

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

    def is_encoder_ready(self) -> bool:
        """Return True if the resemblyzer encoder has finished loading."""
        return self._encoder_ready.is_set()

    def extract_embedding(self, raw_bytes: bytes) -> Optional[np.ndarray]:
        """Extract a speaker embedding from raw PCM-16 audio bytes."""
        if len(raw_bytes) < self.MIN_AUDIO_BYTES:
            return None

        debug = self._debug
        start_time = time.time()

        wav_raw = _pcm16_to_float(raw_bytes)

        # Trim non-speech (silence, noise) using neural VAD before encoding
        wav_raw = _vad_trim_silence(wav_raw)
        if len(wav_raw) < self.MIN_AUDIO_BYTES // 2:
            return None  # too little speech after trimming

        # Wait for encoder to be ready
        if not self._encoder_ready.wait(timeout=60.0):
            print("[VoiceAuth] encoder not ready after 60s timeout")

        # Primary: SpeechBrain ECAPA-TDNN (192-dim, best discrimination)
        # ECAPA-TDNN is trained with heavy augmentation (noise, reverb, different
        # mics) so it is inherently channel-invariant — no pre-processing needed.
        if self._sb_encoder is not None:
            try:
                waveform = _torch.tensor(wav_raw).unsqueeze(0)
                if next(self._sb_encoder.mods.parameters()).is_cuda:
                    waveform = waveform.cuda()
                emb = self._sb_encoder.encode_batch(waveform)
                emb = emb.squeeze().cpu().numpy().astype(np.float32)
                elapsed = (time.time() - start_time) * 1000
                debug.log_extraction(elapsed, len(raw_bytes), method="speechbrain_ecapa")
                return emb
            except Exception as exc:
                print(f"[VoiceAuth] SpeechBrain embed error ({exc}); trying resemblyzer")

        # Channel normalization for weaker encoders (resemblyzer / spectral)
        wav_clean = _channel_normalize(wav_raw)

        # Secondary: resemblyzer GE2E embedding (256-dim)
        if self._encoder is not None:
            try:
                wav_proc = preprocess_wav(wav_clean, source_sr=SAMPLE_RATE)
                emb = self._encoder.embed_utterance(wav_proc)
                elapsed = (time.time() - start_time) * 1000
                debug.log_extraction(elapsed, len(raw_bytes), method="resemblyzer")
                return emb.astype(np.float32)
            except Exception as exc:
                print(f"[VoiceAuth] resemblyzer embed error ({exc}); falling back")

        # Last resort: enhanced spectral fingerprint (64-dim: 32 static + 32 delta)
        emb = _spectral_embedding(raw_bytes, wav_override=wav_clean)
        elapsed = (time.time() - start_time) * 1000
        debug.log_extraction(elapsed, len(raw_bytes), method="spectral_fallback")
        return emb

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
        emb_segment_indices: list[int] = []  # track which segment each embedding came from
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
                    emb_segment_indices.append(i)
                continue

            emb = self.extract_embedding(seg)
            if emb is not None:
                embeddings.append(emb)
                emb_segment_indices.append(i)
            else:
                quality_notes.append(f"Phrase {i+1}: embedding extraction failed")

        min_required = max(1, len(segments) // 2)  # Need at least half
        if len(embeddings) < min_required:
            msg = f"Only {len(embeddings)}/{len(segments)} usable phrases. Need at least {min_required}."
            if quality_notes:
                msg += " Issues: " + "; ".join(quality_notes)
            print(f"[VoiceAuth] enroll failed: {msg}")
            return False, msg

        # ── Cross-phrase consistency: MAD-based outlier detection ──────
        # Drop embeddings that are statistically inconsistent with the
        # batch (cough, background voice, mic bump).  Uses Median Absolute
        # Deviation so the cutoff auto-adapts to the encoder's similarity
        # distribution — no hardcoded threshold to break on model swap.
        if len(embeddings) >= 3:
            n = len(embeddings)
            avg_sims = np.zeros(n)
            for a in range(n):
                sims_a = [
                    self._cosine_sim(embeddings[a], embeddings[b])
                    for b in range(n) if b != a
                ]
                avg_sims[a] = float(np.mean(sims_a))

            median_sim = float(np.median(avg_sims))
            mad = float(np.median(np.abs(avg_sims - median_sim)))
            cutoff = median_sim - 1.5 * max(mad, 0.01)

            keep = [i for i in range(n) if avg_sims[i] >= cutoff]

            if len(keep) >= min_required:
                dropped = n - len(keep)
                if dropped > 0:
                    quality_notes.append(
                        f"{dropped} phrase(s) dropped as outliers (MAD filter)"
                    )
                    print(
                        f"[VoiceAuth] MAD filter: kept {len(keep)}/{n} embeddings "
                        f"(median_sim={median_sim:.3f}, mad={mad:.3f}, cutoff={cutoff:.3f})"
                    )
                embeddings = [embeddings[i] for i in keep]
                emb_segment_indices = [emb_segment_indices[i] for i in keep]
            else:
                # Too many outliers — session is fundamentally corrupted
                msg = (
                    f"Only {len(keep)}/{n} phrases passed consistency check "
                    f"(need {min_required}). Environment may be too noisy."
                )
                if quality_notes:
                    msg += " Issues: " + "; ".join(quality_notes)
                print(f"[VoiceAuth] enroll failed (consistency): {msg}")
                return False, msg

        # Insert user row + embeddings under lock to prevent concurrent write corruption.
        now = time.time()
        with self._db_lock:
            conn = self._get_conn()
            cur = conn.execute(
                "INSERT INTO voice_users (label, role, embedding, created_at, expires_at) "
                "VALUES (?, ?, '[]', ?, ?)",
                (label, role, now, expires_at),
            )
            user_id = cur.lastrowid

            for j, emb in enumerate(embeddings):
                seg_idx = emb_segment_indices[j] if j < len(emb_segment_indices) else -1
                seg = segments[seg_idx] if 0 <= seg_idx < len(segments) else segments[-1]
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

    @staticmethod
    def _cosine_sim_batch(query: np.ndarray, matrix: np.ndarray) -> float:
        """Compute max cosine similarity between query and rows of matrix.

        Uses vectorized numpy — much faster than per-row loop for >2 embeddings.
        """
        norms = np.linalg.norm(matrix, axis=1)
        valid = norms > 1e-8
        if not np.any(valid):
            return 0.0
        q_norm = float(np.linalg.norm(query))
        if q_norm < 1e-8:
            return 0.0
        dots = matrix[valid] @ query
        sims = dots / (norms[valid] * q_norm)
        return float(np.max(sims))

    def identify(
        self, raw_bytes: bytes, precomputed_emb: Optional[np.ndarray] = None
    ) -> Tuple[Optional[str], float, Optional[np.ndarray], str]:
        """
        Identify the speaker using multi-embedding matching.

        Args:
            raw_bytes: PCM-16 audio data.
            precomputed_emb: If provided, skip extraction and use this embedding
                             directly (avoids redundant ~200-400ms extraction).

        Returns:
            (role, best_similarity, embedding, confidence)
            confidence is one of: "high", "medium", "low", "none"
            role is None when the voice does not match any enrolled profile.
        """
        debug = self._debug

        if precomputed_emb is not None:
            emb = precomputed_emb
        else:
            emb = self.extract_embedding(raw_bytes)
        if emb is None:
            return None, 0.0, None, "none"

        users = self._load_active_users()
        if not users:
            return None, 0.0, emb, "none"

        # ── Dimension mismatch fallback ───────────────────────────────
        # If the primary embedding dimension doesn't match ANY stored
        # embedding, also extract a fallback using the other method so
        # we can still compare.  This prevents silent 0.0 scores when
        # resemblyzer vs spectral embeddings are mixed.
        stored_dims: set[int] = set()
        for user in users:
            for e in user["embeddings"]:
                stored_dims.add(e.shape[0])

        emb_alt: Optional[np.ndarray] = None
        if stored_dims and emb.shape[0] not in stored_dims:
            print(
                f"[VoiceAuth] dimension mismatch: extracted={emb.shape[0]}, "
                f"stored={stored_dims}; extracting fallback",
                flush=True,
            )
            wav_raw = _pcm16_to_float(raw_bytes)
            # Try each encoder to find one whose output dim matches stored
            for target_dim in stored_dims:
                if emb_alt is not None:
                    break
                # SpeechBrain produces 192-dim (no channel norm needed)
                if target_dim == 192 and self._sb_encoder is not None:
                    try:
                        waveform = _torch.tensor(wav_raw).unsqueeze(0)
                        if next(self._sb_encoder.mods.parameters()).is_cuda:
                            waveform = waveform.cuda()
                        emb_alt = self._sb_encoder.encode_batch(waveform).squeeze().cpu().numpy().astype(np.float32)
                    except Exception:
                        pass
                # resemblyzer produces 256-dim (needs channel norm)
                elif target_dim == 256 and self._encoder is not None:
                    try:
                        wav_clean = _channel_normalize(wav_raw)
                        wav_proc = preprocess_wav(wav_clean, source_sr=SAMPLE_RATE)
                        emb_alt = self._encoder.embed_utterance(wav_proc).astype(np.float32)
                    except Exception:
                        pass
                # spectral produces 64-dim (needs channel norm)
                elif target_dim == 64:
                    wav_clean = _channel_normalize(wav_raw)
                    emb_alt = _spectral_embedding(raw_bytes, wav_override=wav_clean)

        best_sim = 0.0
        best_role: Optional[str] = None
        best_user_id: Optional[int] = None
        best_user_label: Optional[str] = None
        best_user_emb_count: int = 0
        total_embeddings = 0

        # Measure voice comparison time
        comparison_start = time.time()
        for user in users:
            embeddings = user["embeddings"]
            total_embeddings += len(embeddings)

            # Pick the query embedding that matches stored dimensions
            query = emb
            if embeddings and embeddings[0].shape != emb.shape:
                if emb_alt is not None and embeddings[0].shape == emb_alt.shape:
                    query = emb_alt

            # Hybrid matching: 70% max + 30% mean-of-top-3
            # Requires consistency across stored embeddings, not just one lucky match.
            if len(embeddings) >= 2 and all(e.shape == query.shape for e in embeddings):
                matrix = np.stack(embeddings)
                q_norm = float(np.linalg.norm(query))
                norms = np.linalg.norm(matrix, axis=1)
                valid = (norms > 1e-8) & (q_norm > 1e-8)
                sims = np.zeros(len(embeddings))
                if np.any(valid):
                    sims[valid] = (matrix[valid] @ query) / (norms[valid] * q_norm)
                sorted_sims = np.sort(sims)[::-1]
                top_k = min(3, len(sorted_sims))
                user_best = float(
                    0.7 * sorted_sims[0] + 0.3 * np.mean(sorted_sims[:top_k])
                )
            else:
                user_best = max(
                    (self._cosine_sim(query, e) for e in embeddings), default=0.0
                )
            if user_best > best_sim:
                best_sim = user_best
                best_role = user["role"]
                best_user_id = user["id"]
                best_user_label = user["label"]
                best_user_emb_count = len(embeddings)
        comparison_elapsed = (time.time() - comparison_start) * 1000

        # Users with few embeddings (newly granted) get a relaxed threshold
        # so they can be recognised while the profile builds up via rolling updates.
        is_immature = best_user_emb_count < MIN_MATURE_EMBEDDINGS
        effective_threshold = SIMILARITY_MEDIUM if is_immature else SIMILARITY_HIGH

        # Classify confidence
        if best_sim >= SIMILARITY_HIGH:
            confidence = "high"
        elif best_sim >= SIMILARITY_MEDIUM:
            confidence = "medium"
        else:
            confidence = "low"

        # Log comparison metrics
        debug.log_voice_comparison(
            comparison_ms=comparison_elapsed,
            num_embeddings=total_embeddings,
            num_users=len(users),
            best_similarity=best_sim,
            matched_user=best_user_label if best_sim >= effective_threshold else None,
            confidence=confidence,
        )

        # Accept if similarity meets the effective threshold for this user
        if best_sim >= effective_threshold:
            # Rolling embedding update to build up immature profiles faster
            if best_user_id is not None:
                threading.Thread(
                    target=self._rolling_update, args=(best_user_id, emb, raw_bytes),
                    daemon=True,
                ).start()
            if is_immature:
                print(
                    f"[VoiceAuth] Learning mode: accepted {best_user_label} "
                    f"(sim={best_sim:.3f}, embeddings={best_user_emb_count})",
                    flush=True,
                )
            return best_role, best_sim, emb, confidence

        # MEDIUM/LOW confidence or NO match – stash embedding so Admin can grant access
        now = time.time()
        # Reset buffer if too much time has passed (likely a different person)
        if now - self._unknown_first_ts > self._UNKNOWN_WINDOW_SEC:
            self._unknown_embeddings = []
            self._unknown_first_ts = now
        # Accumulate (up to limit)
        if len(self._unknown_embeddings) < self._MAX_UNKNOWN_BUFFER:
            self._unknown_embeddings.append(emb)
        # Legacy single-embedding alias
        self.last_unknown_embedding = emb
        self.last_unknown_ts = now
        print(
            f"[VoiceAuth] Rejected speaker (best_sim={best_sim:.3f}, confidence={confidence})",
            flush=True,
        )
        return None, best_sim, emb, confidence

    # ------------------------------------------------------------------
    # Rolling embedding update
    # ------------------------------------------------------------------

    def _rolling_update(
        self,
        user_id: int,
        new_embedding: np.ndarray,
        raw_bytes: Optional[bytes] = None,
    ) -> None:
        """Diversity-aware rolling embedding update.

        When the profile is below capacity, simply appends.  At capacity,
        replaces the *most redundant low-SNR* embedding — the one among
        the below-median-SNR candidates that is closest to another stored
        embedding (adds the least acoustic diversity).  This preserves
        "morning voice", "tired voice", and distance variations while
        gradually improving overall quality.

        Rate-limited to one update per user per _ROLLING_UPDATE_INTERVAL.
        """
        now = time.time()
        last = self._rolling_update_ts.get(user_id, 0.0)
        if (now - last) < _ROLLING_UPDATE_INTERVAL:
            return

        self._rolling_update_ts[user_id] = now
        new_snr = _estimate_snr_db(raw_bytes) if raw_bytes else 15.0
        emb_json = json.dumps(new_embedding.tolist())

        with self._db_lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id, embedding, snr_db FROM voice_embeddings "
                "WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()

            if len(rows) < MAX_EMBEDDINGS_PER_USER:
                conn.execute(
                    "INSERT INTO voice_embeddings "
                    "(user_id, embedding, snr_db, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, emb_json, new_snr, now),
                )
            else:
                # Parse stored embeddings
                stored = []
                for row in rows:
                    try:
                        stored.append((
                            row[0],
                            np.array(json.loads(row[1]), dtype=np.float32),
                            row[2] or 0.0,
                        ))
                    except Exception:
                        continue

                if not stored:
                    conn.commit()
                    return

                # Candidates: embeddings with SNR ≤ median (protect high-quality)
                snrs = [s[2] for s in stored]
                median_snr = float(np.median(snrs))
                candidates = [
                    (i, s) for i, s in enumerate(stored) if s[2] <= median_snr
                ]
                if not candidates:
                    candidates = list(enumerate(stored))

                # Vectorized nearest-neighbour search for redundancy
                all_matrix = np.stack([s[1] for s in stored])
                all_norms = np.linalg.norm(all_matrix, axis=1)

                worst_idx = candidates[0][0]
                best_max_sim = -1.0
                for _ci, (idx, (_rid, remb, _rsnr)) in enumerate(candidates):
                    rnorm = float(np.linalg.norm(remb))
                    if rnorm < 1e-8:
                        continue
                    sims = (all_matrix @ remb) / (all_norms * rnorm + 1e-8)
                    sims[idx] = -1.0  # exclude self
                    max_sim = float(np.max(sims))
                    if max_sim > best_max_sim:
                        best_max_sim = max_sim
                        worst_idx = idx

                replace_id = stored[worst_idx][0]
                conn.execute(
                    "UPDATE voice_embeddings "
                    "SET embedding = ?, snr_db = ?, created_at = ? WHERE id = ?",
                    (emb_json, new_snr, now, replace_id),
                )
            conn.commit()
        self._invalidate_cache()

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def authorize(
        self, raw_bytes: bytes, intent: str = "",
        precomputed_emb: Optional[np.ndarray] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Check whether the current speaker may execute *intent*.

        Returns:
            (allowed, role_label, denial_message)
            denial_message is None when access is granted.
        """
        role, sim, _, confidence = self.identify(raw_bytes, precomputed_emb=precomputed_emb)

        if role is None:
            return (
                False,
                "unknown",
                "Sorry, I don't recognise your voice. "
                "Ask an admin to grant you access.",
            )

        if role == "Admin":
            return True, "Admin", None

        intent_key = intent.strip().lower().replace(" ", "_")

        if role == "User":
            if intent_key in ADMIN_ONLY_INTENTS and intent_key:
                return (
                    False,
                    "User",
                    "Sorry, only an admin can do that.",
                )
            return True, "User", None

        if role == "Guest":
            if intent_key not in GUEST_ALLOWED_INTENTS and intent_key:
                return (
                    False,
                    "Guest",
                    "Sorry, you don't have permission for that.",
                )
            return True, "Guest", None

        return False, "unknown", "Sorry, I don't recognise your voice."

    # ------------------------------------------------------------------
    # Grant
    # ------------------------------------------------------------------

    def _next_user_number(self) -> int:
        """Return the next sequential user number based on existing labels.

        Scans all user_N(...) labels and returns max(N) + 1, so numbers
        stay compact even after deletions.
        """
        conn = self._get_conn()
        rows = conn.execute("SELECT label FROM voice_users").fetchall()
        max_num = 0
        for (label,) in rows:
            # Extract N from "user_N(...)"
            m = re.match(r"user_(\d+)", label, re.IGNORECASE)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return max_num + 1

    def grant_access(
        self, role: str, expires_at: Optional[float] = None
    ) -> bool:
        """
        Register the last unknown speaker with *role*.

        Returns True on success, False if no unknown embedding is available.
        """
        # Use accumulated buffer; fall back to legacy single embedding
        embeddings = [
            e for e in self._unknown_embeddings
            if float(np.linalg.norm(e)) >= 1e-6
        ]
        if not embeddings and self.last_unknown_embedding is not None:
            if float(np.linalg.norm(self.last_unknown_embedding)) >= 1e-6:
                embeddings = [self.last_unknown_embedding]

        if not embeddings:
            print("[VoiceAuth] grant_access: no pending unknown speaker")
            return False

        num = self._next_user_number()
        label = f"user_{num}({role.lower()})"
        now = time.time()
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO voice_users (label, role, embedding, created_at, expires_at) "
            "VALUES (?, ?, '[]', ?, ?)",
            (label, role, now, expires_at),
        )
        user_id = cur.lastrowid
        for emb in embeddings:
            conn.execute(
                "INSERT INTO voice_embeddings (user_id, embedding, created_at) VALUES (?, ?, ?)",
                (user_id, json.dumps(emb.tolist()), now),
            )
        conn.commit()
        self._invalidate_cache()
        self._unknown_embeddings = []
        self.last_unknown_embedding = None
        exp_str = "permanent" if expires_at is None else f"until {time.ctime(expires_at)}"
        print(f"[VoiceAuth] Granted {role} access to '{label}' ({exp_str}) — {len(embeddings)} embeddings")
        return True

    def delete_user(self, query: str) -> Tuple[bool, str]:
        """Delete a user by number, label, or partial match.

        Accepts:
            "1"           → match user_1(...)
            "1 guest"     → match user_1(guest)
            "user_2(guest)" → exact label match
            "guest"       → partial match on label

        Will not delete the last remaining Admin.

        Returns:
            (success, message)
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, label, role FROM voice_users"
        ).fetchall()

        if not rows:
            return False, "There are no registered users."

        q = query.strip().lower()
        match = None

        # Try number-based match first: "1" or "1 guest" → user_1(...)
        tokens = q.split()
        if tokens and tokens[0].isdigit():
            num = tokens[0]
            role_hint = tokens[1] if len(tokens) > 1 else None
            for uid, ulabel, urole in rows:
                # Match "user_N(...)" pattern
                if f"user_{num}(" in ulabel.lower():
                    if role_hint is None or role_hint in ulabel.lower():
                        match = (uid, ulabel, urole)
                        break

        # Fall back to partial label match
        if match is None:
            for uid, ulabel, urole in rows:
                if q in ulabel.lower():
                    match = (uid, ulabel, urole)
                    break

        if match is None:
            return False, f"I couldn't find a user matching '{query}'."

        uid, ulabel, urole = match

        if urole == "Admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) FROM voice_users WHERE role='Admin'"
            ).fetchone()[0]
            if admin_count <= 1:
                return False, "I can't delete the only admin."

        conn.execute("DELETE FROM voice_embeddings WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM voice_users WHERE id = ?", (uid,))
        conn.commit()
        self._invalidate_cache()
        print(f"[VoiceAuth] Deleted user '{ulabel}' (role={urole})")
        return True, f"Removed {ulabel}."
