"""Two no-LLM baselines the model must beat.

- naive: domain match only (the check many AP teams do by eye).
- rules: regex extraction + every Python rule.
"""

from __future__ import annotations

import duckdb

from bankreview import db, regex_extract, rules
from bankreview.rules import Master, Request, Review


def naive(req: Request, master: Master) -> Review:
    facts = rules.Facts()
    signals = [s for s in [rules.domain_mismatch(req, master, facts)] if s]
    return rules.review(req.request_id, "naive", req, master, signals, facts, case_status="OK")


def rules_only(req: Request, master: Master) -> Review:
    facts = regex_extract.extract(req.body)
    signals = rules.evaluate_signals(req, master, facts)
    return rules.review(req.request_id, "rules", req, master, signals, facts)


BASELINES = {"naive": naive, "rules": rules_only}


def run_case(con: duckdb.DuckDBPyConnection, case_id: str, by: str) -> Review:
    req, master = db.load_case(con, case_id)
    return BASELINES[by](req, master)
