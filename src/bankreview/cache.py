"""Response cache: the same packet, prompt and model never pay for a second call.

Key = sha256(model + prompt version + packet). The prompt version is the hash of the system
prompt, so editing the prompt or switching the model makes new calls automatically.
Only answers that validated AND passed every guard are stored; API errors and invalid answers
are never cached. Guards still run on every cache hit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def key(model: str, prompt_version: str, packet_text: str) -> str:
    return hashlib.sha256("\n".join((model, prompt_version, packet_text)).encode("utf-8")
                          ).hexdigest()


class ResponseCache:
    def __init__(self, directory: Path | str, enabled: bool = True):
        self.dir = Path(directory)
        self.enabled = enabled  # False = --no-cache: never read, never write

    def _path(self, k: str) -> Path:
        return self.dir / f"{k}.json"

    def get(self, k: str) -> str | None:
        if not self.enabled or not self._path(k).exists():
            return None
        return json.loads(self._path(k).read_text(encoding="utf-8"))["answer"]

    def put(self, k: str, answer_text: str, model: str, prompt_version: str) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {"model": model, "prompt_version": prompt_version, "answer": answer_text}
        self._path(k).write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
