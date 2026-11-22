"""Guards on the model's answer, and the never-downgrade combination.

Guards (any failure -> NEEDS_HUMAN):
- every quote, holder name and phone number is an exact substring of the tokenized body;
- every token the model names exists in the token map;
- the explanation does not repeat a phone number from the body (compared by digits only),
  unless that phone is ON FILE for this supplier;
- every number with 5+ digits in the explanation is a TRUSTED number (supplier master, token
  ids / last 4, request metadata). Numbers with 4 or fewer digits (days, years, counts, last 4)
  are allowed: the first real run flagged "1 December 2026" on legit cases.

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
PHONE_RUN_RE = re.compile(r"\+?\(?\d[\d ().\-]*\d")  # phone-like digit runs in the explanation
LONG_DIGITS_RE = re.compile(r"\d(?:[ \-.,]?\d){4,}")  # 5+ digits, for masking log lines
MIN_UNTRUSTED_DIGITS = 5  # a number this long in the explanation must be trusted
MIN_PHONE_MATCH_DIGITS = 7  # shortest digit run compared as a phone (country code dropped)
NEEDS_HUMAN = "NEEDS_HUMAN"


def mask_digits(text: str) -> str:
    """Replace every run of 5+ digits with '[N digits ...last4]' so a log line never holds a
    full bank number."""
    def mask(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"[{len(digits)} digits ...{digits[-4:]}]"
    return LONG_DIGITS_RE.sub(mask, text)


def _same_phone(a: str, b: str) -> bool:
    """Digits equal, or one ends with the other (a dropped country code)."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= MIN_PHONE_MATCH_DIGITS and long_.endswith(short)


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
    problems += _explanation_problems(ext, req, master, tok)
    return problems


def _explanation_problems(ext: Extraction, req: Request, master: Master,
                          tok: Tokenized) -> list[str]:
    problems = []
    on_file = {rules.phone_digits(c.phone_on_file) for c in master.contacts} - {""}
    body_phones = [p for p, _ in regex_extract.find_phones(tok.text)]
    body_phones += list(ext.phone_numbers_in_text)
    phone_digits = {rules.phone_digits(p) for p in body_phones} - on_file - {""}
    runs = [rules.phone_digits(r) for r in PHONE_RUN_RE.findall(ext.explanation)]
    for phone in sorted(phone_digits):
        if any(_same_phone(phone, run) for run in runs):
            problems.append(f"explanation repeats a phone number from the body: "
                            f"...{phone[-4:]}")
    trusted = trusted_numbers(req, master, tok)
    for number in NUMBER_RE.findall(ext.explanation):
        digits = sum(ch.isdigit() for ch in number)
        if digits >= MIN_UNTRUSTED_DIGITS and number not in trusted:
            problems.append(f"explanation uses a {MIN_UNTRUSTED_DIGITS}+ digit number that is "
                            f"not in a trusted field: {number}")
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
