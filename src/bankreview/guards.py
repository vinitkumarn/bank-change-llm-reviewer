"""Guards on the model's answer, and the never-downgrade combination.

Guards (any failure -> NEEDS_HUMAN):
- every quote, holder name and phone number is an exact substring of the tokenized body;
- every token the model names exists in the token map;
- every number in the explanation is a TRUSTED number (supplier master, token ids / last 4,
  request metadata) — never one that only appears in the untrusted body.

Python then decides everything with rules.py. Never-downgrade: the final risk is the higher of
the rules-only result and the result using the model's facts; the model can add signals but
never remove one the rules found.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

from bankreview import baselines, regex_extract, rules
from bankreview.packet import escape, trusted_part
from bankreview.rules import Master, Request, Review
from bankreview.schema import Extraction, to_facts
from bankreview.tokenize import Tokenized

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
NEEDS_HUMAN = "NEEDS_HUMAN"


def trusted_numbers(req: Request, master: Master, tok: Tokenized) -> set[str]:
    return set(NUMBER_RE.findall(json.dumps(trusted_part(req, master, tok))))


def check(ext: Extraction, req: Request, master: Master, tok: Tokenized) -> list[str]:
    """Problems with the answer. An empty list means every guard passed."""
    problems = []
    body_forms = (tok.text, escape(tok.text))  # the model saw the escaped form

    def in_body(text: str) -> bool:
        return bool(text) and any(text in form for form in body_forms)

    quoted = [("urgency quote", q) for q in ext.urgency_quotes]
    quoted += [("skip-verification quote", q) for q in ext.skip_verification_quotes]
    quoted += [("grey quote", q) for q in ext.grey_quotes]
    quoted += [("holder name", q) for q in ext.account_holder_names]
    quoted += [("phone number", q) for q in ext.phone_numbers_in_text]
    if ext.new_bank_country_quote is not None:
        quoted.append(("country quote", ext.new_bank_country_quote))
    for what, text in quoted:
        if not in_body(text):
            problems.append(f"{what} is not an exact quote from the body: {text!r}")
    for token in ext.new_account_tokens:
        if token not in tok.token_map:
            problems.append(f"unknown token {token}")
    trusted = trusted_numbers(req, master, tok)
    for number in NUMBER_RE.findall(ext.explanation):
        if number not in trusted:
            problems.append(f"explanation uses a number that is not in a trusted field: "
                            f"{number}")
    return problems


def combine(req: Request, master: Master, ext: Extraction | None, tok: Tokenized,
            problems: list[str], by: str = "claude") -> Review:
    floor = baselines.rules_only(req, master)  # regex + rules only: the never-downgrade floor
    if ext is None or problems:
        return replace(floor, by=by, status=NEEDS_HUMAN, confidence="LOW")

    facts = to_facts(ext, tok)
    model_signals = rules.evaluate_signals(req, master, facts)
    model_level = rules.risk_level(model_signals)
    seen = {s.code for s in floor.signals}
    signals = floor.signals + [s for s in model_signals if s.code not in seen]
    regex_grey = regex_extract.extract(req.body).grey_quotes
    merged = replace(facts, grey_quotes=facts.grey_quotes + regex_grey)
    status = NEEDS_HUMAN if NEEDS_HUMAN in (floor.status, rules.status(facts)) else "OK"
    final_floor = rules.higher_level(floor.risk_level, model_level)
    review = rules.review(req.request_id, by, req, master, signals, merged,
                          case_status=status, floor=final_floor)
    if status == NEEDS_HUMAN:
        review.confidence = "LOW"
    else:
        review.confidence = "HIGH" if model_level == floor.risk_level else "MEDIUM"
    review.explanation = ext.explanation
    return review
