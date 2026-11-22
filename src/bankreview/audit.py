"""Audit log: one JSON line per reviewed request.

Only ids, hashes, tokens, cost and status. Never the body text, never a quote, never a bank
number (tokens and last 4 digits only).
"""

from __future__ import annotations

import json
from pathlib import Path

ALLOWED_KEYS = {
    "case_id", "by", "model", "prompt_version", "packet_sha256", "cache_key", "cache_hit",
    "calls", "tokens", "account_hash_candidates", "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens", "cost_usd", "latency_ms",
    "status", "risk_level", "signals", "confidence", "error_kind", "guard_problems",
}


class AuditLog:
    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None  # None = do not write (tests, dry runs)

    def write(self, record: dict) -> dict:
        unknown = set(record) - ALLOWED_KEYS
        if unknown:
            raise ValueError(f"audit fields not allowed: {sorted(unknown)}")
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record
