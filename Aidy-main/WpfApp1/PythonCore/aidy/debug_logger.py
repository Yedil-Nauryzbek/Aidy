"""debug_logger.py – Performance debugging and timing measurements."""

import os
import time
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Dict, Any


class DebugLogger:
    """Tracks timing and performance metrics for voice authentication."""

    def __init__(self, debug_file: str = "KILLER_BUG.txt"):
        self.debug_file = debug_file
        self.session_start = time.time()
        self.timings: Dict[str, list] = {}
        self.enabled = True
        self._write_session_start()

    def _write_session_start(self) -> None:
        """Log session startup."""
        timestamp = datetime.now().isoformat()
        with open(self.debug_file, "a") as f:
            f.write(f"\n{'='*70}\n")
            f.write(f"SESSION START: {timestamp}\n")
            f.write(f"{'='*70}\n")

    @contextmanager
    def measure(self, operation_name: str):
        """Context manager to measure operation timing.

        Usage:
            with debug.measure("voice_comparison"):
                # ... do work ...
        """
        if not self.enabled:
            yield
            return

        start = time.time()
        try:
            yield
        finally:
            elapsed_ms = (time.time() - start) * 1000
            self._record_timing(operation_name, elapsed_ms)

    def _record_timing(self, operation: str, elapsed_ms: float) -> None:
        """Record and log a timing measurement."""
        if operation not in self.timings:
            self.timings[operation] = []
        self.timings[operation].append(elapsed_ms)

        timestamp = datetime.now().isoformat()
        session_elapsed = (time.time() - self.session_start) * 1000

        with open(self.debug_file, "a") as f:
            f.write(f"[{timestamp}] (+{session_elapsed:.1f}ms total)\n")
            f.write(f"  OP: {operation}\n")
            f.write(f"  TIME: {elapsed_ms:.2f}ms\n")

    def log_voice_comparison(
        self,
        comparison_ms: float,
        num_embeddings: int,
        num_users: int,
        best_similarity: float,
        matched_user: Optional[str] = None,
        confidence: str = "none",
    ) -> None:
        """Log detailed voice comparison metrics.

        Args:
            comparison_ms: Time spent comparing embeddings (milliseconds)
            num_embeddings: Total embeddings compared
            num_users: Number of enrolled users checked
            best_similarity: Highest similarity score found
            matched_user: Label of matched user (if any)
            confidence: Confidence level (high/medium/low/none)
        """
        if not self.enabled:
            return

        timestamp = datetime.now().isoformat()
        session_elapsed = (time.time() - self.session_start) * 1000

        data = {
            "timestamp": timestamp,
            "session_elapsed_ms": round(session_elapsed, 2),
            "operation": "voice_comparison",
            "comparison_ms": round(comparison_ms, 2),
            "num_embeddings_compared": num_embeddings,
            "num_users_checked": num_users,
            "best_similarity": round(best_similarity, 4),
            "matched_user": matched_user,
            "confidence": confidence,
        }

        with open(self.debug_file, "a") as f:
            f.write(f"\n[VOICE COMPARISON]\n")
            f.write(json.dumps(data, indent=2))
            f.write(f"\n")

    def log_extraction(
        self, extraction_ms: float, audio_bytes: int, method: str = "unknown"
    ) -> None:
        """Log embedding extraction timing.

        Args:
            extraction_ms: Time to extract embedding
            audio_bytes: Size of audio data processed
            method: Method used (resemblyzer/spectral)
        """
        if not self.enabled:
            return

        timestamp = datetime.now().isoformat()
        session_elapsed = (time.time() - self.session_start) * 1000

        data = {
            "timestamp": timestamp,
            "session_elapsed_ms": round(session_elapsed, 2),
            "operation": "embedding_extraction",
            "extraction_ms": round(extraction_ms, 2),
            "audio_bytes": audio_bytes,
            "method": method,
            "throughput_mb_per_s": round(
                (audio_bytes / 1024 / 1024) / (extraction_ms / 1000), 2
            ),
        }

        with open(self.debug_file, "a") as f:
            f.write(f"\n[EMBEDDING EXTRACTION]\n")
            f.write(json.dumps(data, indent=2))
            f.write(f"\n")

    def log_command(
        self,
        text: str,
        total_ms: float,
        success: bool,
        phase_times: Optional[Dict[str, float]] = None,
    ) -> None:
        """Log end-to-end command execution speed metrics.

        Args:
            text: The recognized command text
            total_ms: Total wall-clock time from receive to completion
            success: Whether the command completed successfully
            phase_times: Optional breakdown of phases (e.g. auth, intent_api, execution)
        """
        if not self.enabled:
            return

        timestamp = datetime.now().isoformat()
        session_elapsed = (time.time() - self.session_start) * 1000

        data: Dict[str, Any] = {
            "timestamp": timestamp,
            "session_elapsed_ms": round(session_elapsed, 2),
            "operation": "command_complete",
            "command_text": text,
            "total_ms": round(total_ms, 2),
            "success": success,
        }
        if phase_times:
            data["phases"] = {k: round(v, 2) for k, v in phase_times.items()}

        with open(self.debug_file, "a") as f:
            f.write(f"\n[COMMAND COMPLETE]\n")
            f.write(json.dumps(data, indent=2))
            f.write(f"\n")

    def summary(self) -> None:
        """Write performance summary to debug file."""
        if not self.enabled:
            return

        total_elapsed = (time.time() - self.session_start) * 1000
        timestamp = datetime.now().isoformat()

        with open(self.debug_file, "a") as f:
            f.write(f"\n{'='*70}\n")
            f.write(f"SESSION SUMMARY: {timestamp}\n")
            f.write(f"Total elapsed: {total_elapsed:.2f}ms\n")
            f.write(f"{'='*70}\n")
            f.write("Operation Timings:\n")

            for op, times in sorted(self.timings.items()):
                if times:
                    avg = sum(times) / len(times)
                    min_t = min(times)
                    max_t = max(times)
                    f.write(
                        f"  {op:30s}: "
                        f"calls={len(times):3d} "
                        f"avg={avg:7.2f}ms "
                        f"min={min_t:7.2f}ms "
                        f"max={max_t:7.2f}ms\n"
                    )


# Global debug instance
_debug_instance: Optional[DebugLogger] = None


def get_debug_logger(debug_file: str = "KILLER_BUG.txt") -> DebugLogger:
    """Get or create the global debug logger."""
    global _debug_instance
    if _debug_instance is None:
        _debug_instance = DebugLogger(debug_file)
    return _debug_instance
