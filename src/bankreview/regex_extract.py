"""A no-LLM extraction: regexes and keyword lists that fill a `Facts` object.

Used by the rules-only baseline, and later by the never-downgrade check (the rules-only
risk is the floor that the model's extraction can never lower).

The patterns are generic (bank number shapes, common pressure phrases). They are not tuned
to the planted cases, and the red-team set is never used to tune them.
"""

from __future__ import annotations

import hashlib
import re
from itertools import permutations

from bankreview.rules import Facts, Request, email_address, email_domain

__all__ = ["extract", "sender_domains", "email_address", "email_domain"]

# Phones: international (+CC ...) or North-American "(408) 555-0101".
PHONE_RE = re.compile(r"\+\d{1,3}(?:[ \-.]?\(?\d{1,5}\)?){2,6}|\(\d{3}\)\s?\d{3}[ \-.]\d{4}")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# IBAN: 2 letters, 2 check digits, then groups of up to 4 letters/digits (optionally spaced).
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
# Digit groups joined by single spaces or dashes on one line. Not part of a word or code
# (INV-1015-001), not a phone (+...), not money (487,250.00).
DIGITS_RE = re.compile(r"(?<![\w+\-.,])\d+(?:[ \-]\d+)*(?!\w|[.,]\d)")
MIN_BANK_DIGITS = 5  # shortest bank number we tokenize (CA transit number)
MIN_MATCH_DIGITS = 3  # CA institution number, used only to rebuild the full account
MAX_MATCH_ITEMS = 12

HOLDER_RE = re.compile(
    r"\b(?:account holder(?: name)?|account name|beneficiary(?: name)?|in the name of)\b"
    r"[ \t]*[:\-]?[ \t]*([^\n|,;]+)", re.IGNORECASE)
HOLDER_LABELS = {"account holder", "account holder name", "account name", "beneficiary",
                 "beneficiary name"}

URGENCY_PATTERNS = [
    r"\btoday\b", r"\burgent(?:ly)?\b", r"\bimmediately\b", r"\basap\b",
    r"\bas soon as possible\b", r"\bright away\b", r"\bwithout delay\b", r"\bpayment run\b",
    r"\bbefore (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\bwithin \d+ hours\b", r"\b(?:by )?end of (?:the )?day\b",
]
SKIP_PATTERNS = [
    r"\bno need (?:for|of) (?:a )?call[- ]?back\b", r"\bno need to call\b",
    r"\bno call[- ]?back\b", r"\bwithout (?:a )?call[- ]?back\b",
    r"\b(?:do not|don't) call\b", r"\bskip (?:the )?(?:call[- ]?back|verification)\b",
    r"\bno verification\b", r"\balready (?:been )?(?:approved|verified|confirmed)\b",
    r"\bcall me (?:on|at)\b", r"\bcall my\b",
]
GREY_PATTERNS = [
    r"\bmerg(?:er|ed)\b", r"\brelocat\w*", r"\bfactor(?:ing|s|ed)?\b",
    r"\bassign(?:ed|ment)\b", r"\bsubsidiary\b", r"\bon behalf of\b",
]


def _quotes(patterns: list[str], text: str) -> tuple[str, ...]:
    """Exact substrings of the text that match any pattern, in order of appearance."""
    found = []
    for pat in patterns:
        found += [(m.start(), m.group(0)) for m in re.finditer(pat, text, re.IGNORECASE)]
    return tuple(q for _, q in sorted(found))


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        chars[a:b] = " " * (b - a)
    return "".join(chars)


def iban_is_valid(candidate: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", candidate):
        return False
    rearranged = candidate[4:] + candidate[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def find_ibans(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Valid IBANs (compact) with their spans. Trailing groups are dropped until mod-97 holds."""
    out = []
    for m in IBAN_RE.finditer(text):
        raw = m.group(0)
        for end in range(len(raw), 14, -1):
            compact = raw[:end].replace(" ", "")
            if iban_is_valid(compact):
                out.append((compact, (m.start(), m.start() + end)))
                break
    return out


def find_phones(text: str) -> list[tuple[str, tuple[int, int]]]:
    return [(m.group(0).strip(), m.span()) for m in PHONE_RE.finditer(text)]


def _holder_names(text: str) -> tuple[str, ...]:
    names = []
    for line in text.splitlines():
        if line.count("|") >= 2:  # text table: | Account name | old | new |
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0].lower() in HOLDER_LABELS and cells[-1]:
                names.append(cells[-1])
            continue
        for m in HOLDER_RE.finditer(line):
            name = m.group(1).strip().rstrip(".").strip()
            if name and not name.startswith("("):
                names.append(name)
    return tuple(dict.fromkeys(names))


def sender_domains(req: Request) -> tuple[str, ...]:
    """Domains of the From and Reply-To addresses (letters have none)."""
    return tuple(d for d in (email_domain(req.from_address), email_domain(req.reply_to)) if d)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def extract(body: str) -> Facts:
    """Regex-only Facts for one request body (raw, before tokenizing)."""
    phones = find_phones(body)
    rest = _blank(body, [span for _, span in phones])
    rest = _blank(rest, [m.span() for m in ISO_DATE_RE.finditer(rest)])
    ibans = find_ibans(rest)
    rest = _blank(rest, [span for _, span in ibans])
    ifscs = [(m.group(0), m.span()) for m in IFSC_RE.finditer(rest)]
    rest = _blank(rest, [span for _, span in ifscs])
    runs = [re.sub(r"[ \-]", "", m.group(0)) for m in DIGITS_RE.finditer(rest)]

    bank_numbers = [i for i, _ in ibans] + [c for c, _ in ifscs] + [
        r for r in runs if len(r) >= MIN_BANK_DIGITS]
    # Rebuild "routing + account" (and CA transit + institution + account) in any order so
    # SHARED_ACCOUNT can compare against the master data's hashes.
    items = list(dict.fromkeys([c for c, _ in ifscs] + [r for r in runs
                                                        if len(r) >= MIN_MATCH_DIGITS]))
    items = items[:MAX_MATCH_ITEMS]
    hashes = {_sha(i) for i, _ in ibans}
    for k in (2, 3):
        hashes.update(_sha("".join(p)) for p in permutations(items, k))

    return Facts(
        bank_numbers=tuple(dict.fromkeys(bank_numbers)),
        account_hashes=tuple(sorted(hashes)),
        new_bank_countries=tuple(dict.fromkeys(i[:2] for i, _ in ibans)),
        account_holder_names=_holder_names(body),
        phone_numbers=tuple(p for p, _ in phones),
        urgency_quotes=_quotes(URGENCY_PATTERNS, body),
        skip_verification_quotes=_quotes(SKIP_PATTERNS, body),
        grey_quotes=_quotes(GREY_PATTERNS, body),
    )
