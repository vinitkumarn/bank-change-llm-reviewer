"""The claude reviewer: tokenize -> packet -> (cache | one call) -> guards -> rules.

The model only extracts. Python checks the answer (guards), maps it into rules.Facts and lets
rules.py decide, never below the rules-only result.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from bankreview import cache as cache_mod
from bankreview import config, extractor, guards
from bankreview.audit import AuditLog
from bankreview.packet import build_packet, packet_json
from bankreview.rules import Master, Request, Review
from bankreview.schema import Extraction
from bankreview.tokenize import hash_candidates, tokenize

log = logging.getLogger("bankreview")


@dataclass
class RunStats:
    cases: int = 0
    calls: int = 0
    cache_hits: int = 0
    needs_human_guard: int = 0  # guard failures (unsupported quotes, unknown tokens, numbers)
    invalid_answers: int = 0
    api_errors: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    cost_known: bool = True

    def lines(self, model: str) -> list[str]:
        cost = f"${self.cost_usd:.4f}" if self.cost_known else "unknown (model not in PRICES)"
        per = (self.latency_ms // self.cases) if self.cases else 0
        rate = f"{self.cache_hits}/{self.cases}" if self.cases else "0/0"
        return [
            "## Model run",
            "",
            "| Item | Value |",
            "|---|---|",
            f"| Model | {model} |",
            f"| Prompt version | {extractor.PROMPT_VERSION} |",
            f"| API calls (incl. retries) | {self.calls} |",
            f"| Cache hits | {rate} |",
            f"| Guard failures -> NEEDS_HUMAN | {self.needs_human_guard} |",
            f"| Invalid answers after retry | {self.invalid_answers} |",
            f"| API errors | {self.api_errors} |",
            f"| Cost | {cost} |",
            f"| Average latency per case (ms) | {per} |",
        ]


@dataclass
class ClaudeReviewer:
    client: object
    cache: cache_mod.ResponseCache
    audit: AuditLog = field(default_factory=lambda: AuditLog(None))
    model: str = config.MODEL
    system_prompt: str = extractor.SYSTEM_PROMPT
    stats: RunStats = field(default_factory=RunStats)

    def __call__(self, req: Request, master: Master) -> Review:
        tok = tokenize(req.body)
        text = packet_json(build_packet(req, master, tok))
        version = extractor.prompt_version(self.system_prompt)
        key = cache_mod.key(self.model, version, text)

        ext: Extraction | None = None
        result = extractor.CallResult(extraction=None)
        cached = self.cache.get(key)
        if cached is not None:
            try:
                ext = extractor.parse(cached)
            except ValueError:
                ext = None  # a broken cache file is treated as a miss
        hit = ext is not None
        if not hit:
            result = extractor.call(self.client, text, self.model, self.system_prompt)
            ext = result.extraction

        problems = guards.check(ext, req, master, tok) if ext is not None else []
        for problem in problems:  # case id + message only; digits masked, no body text
            log.warning("guard problem %s: %s", req.request_id, guards.mask_digits(problem))
        if ext is not None and not problems and not hit:
            self.cache.put(key, result.answer_text, self.model, version)
        review = guards.combine(req, master, ext, tok, problems)

        self._count(result, hit, problems)
        cost = result.usage.cost(self.model)
        self.audit.write({
            "case_id": req.request_id, "by": "claude", "model": self.model,
            "prompt_version": version,
            "packet_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "cache_key": key, "cache_hit": hit, "calls": result.calls,
            "tokens": [f"{t} (...{tok.last4[t]})" for t in tok.last4],
            "account_hash_candidates": len(hash_candidates(ext.new_account_tokens, tok))
            if ext else 0,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cache_creation_input_tokens": result.usage.cache_creation_input_tokens,
            "cache_read_input_tokens": result.usage.cache_read_input_tokens,
            "cost_usd": str(cost) if cost is not None else None,
            "latency_ms": result.latency_ms, "status": review.status,
            "risk_level": review.risk_level, "signals": review.signal_codes,
            "confidence": review.confidence,
            "error_kind": "guard_failed" if problems else result.error_kind,
            "guard_problems": len(problems),
        })
        return review

    def _count(self, result: extractor.CallResult, hit: bool, problems: list[str]) -> None:
        s = self.stats
        s.cases += 1
        s.calls += result.calls
        s.cache_hits += hit
        s.needs_human_guard += bool(problems)
        s.invalid_answers += result.error_kind == "invalid_answer"
        s.api_errors += result.error_kind == "api_error"
        s.latency_ms += result.latency_ms
        cost = result.usage.cost(self.model)
        if cost is None:
            s.cost_known = s.cost_known and result.calls == 0
        else:
            s.cost_usd += cost
