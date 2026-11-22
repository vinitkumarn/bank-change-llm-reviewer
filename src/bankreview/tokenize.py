"""Replace bank numbers with tokens BEFORE any text is sent to the model.

"Account 1234 5678 90" becomes "Account BANK_TOKEN_1 (...7890)". The token map (token -> full
number) stays inside Python: it is never logged, cached or sent anywhere.

What counts as a bank number (safety first: when in doubt, tokenize):
- IBANs, written compact, with spaces or with dashes (mod-97 check must hold);
- Indian IFSC codes;
- runs of 5+ digits, optionally grouped with single spaces or dashes (account, ABA routing,
  sort code 12-34-56, CA transit, BLZ);
- a digit run that ends a line and continues on the next line (line-split numbers);
- as a last safety net, any remaining run of 8+ digits.
Phone numbers (+CC ... or (408) 555-0101) and ISO dates are left alone so the model can
still report them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from itertools import permutations

from bankreview import regex_extract
from bankreview.regex_extract import DIGITS_RE, IFSC_RE, ISO_DATE_RE, MIN_BANK_DIGITS

TOKEN_RE = re.compile(r"BANK_TOKEN_\d+")
# IBAN with optional single space or dash between groups (regex_extract only allows spaces).
IBAN_ANY_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ \-]?[A-Z0-9]{4}){2,7}(?:[ \-]?[A-Z0-9]{1,3})?\b")
SAFETY_NET_RE = re.compile(r"\d{8,}")
# A continuation line: optional quote markers, then only a digit run.
CONTINUATION_RE = re.compile(r"[ \t>]*(\d+(?:[ \-]\d+)*)[ \t]*$")


@dataclass
class Tokenized:
    text: str  # the body with every bank number replaced
    token_map: dict[str, str] = field(default_factory=dict)  # token -> full compact number
    last4: dict[str, str] = field(default_factory=dict)  # token -> last 4 characters

    def __repr__(self) -> str:  # never print the full numbers by accident
        return f"Tokenized(tokens={sorted(self.last4)})"


def _compact(raw: str) -> str:
    return re.sub(r"[\s\-]", "", raw)


def _find_ibans(text: str) -> list[tuple[int, int]]:
    spans = []
    for m in IBAN_ANY_RE.finditer(text):
        raw = m.group(0)
        for end in range(len(raw), 14, -1):  # drop trailing groups until mod-97 holds
            if regex_extract.iban_is_valid(_compact(raw[:end])):
                spans.append((m.start(), m.start() + end))
                break
    return spans


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        chars[a:b] = " " * (b - a)
    return "".join(chars)


def _line_split(text: str, span: tuple[int, int]) -> tuple[int, int]:
    """Extend a digit run that ends its line with a next line holding only digits."""
    a, b = span
    nl = b + len(text[b:]) - len(text[b:].lstrip(" \t"))
    if nl >= len(text) or text[nl] != "\n":
        return span
    next_end = text.find("\n", nl + 1)
    next_end = len(text) if next_end == -1 else next_end
    m = CONTINUATION_RE.fullmatch(text, nl + 1, next_end)
    if not m:
        return span
    return a, m.end(1)


def find_bank_spans(text: str) -> list[tuple[int, int]]:
    """Spans of every bank number in the text, in order, not overlapping."""
    rest = _blank(text, [span for _, span in regex_extract.find_phones(text)])
    rest = _blank(rest, [m.span() for m in ISO_DATE_RE.finditer(rest)])
    spans: list[tuple[int, int]] = []

    def take(found: list[tuple[int, int]]) -> None:
        nonlocal rest
        spans.extend(found)
        rest = _blank(rest, found)

    take(_find_ibans(rest))
    take([m.span() for m in IFSC_RE.finditer(rest)])
    runs = []
    for m in DIGITS_RE.finditer(rest):
        span = _line_split(rest, m.span())
        if len(re.sub(r"\D", "", rest[span[0]:span[1]])) >= MIN_BANK_DIGITS:
            runs.append(span)
    take(_merge(runs))
    take([m.span() for m in SAFETY_NET_RE.finditer(rest)])
    return sorted(spans)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A line-split run swallows the next run; keep the outer one."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a < out[-1][1]:
            continue
        out.append((a, b))
    return out


def tokenize(text: str) -> Tokenized:
    tok = Tokenized(text="")
    by_raw: dict[str, str] = {}
    parts, pos = [], 0
    for a, b in find_bank_spans(text):
        raw = re.sub(r"[\s\->]", "", text[a:b])
        if raw not in by_raw:
            token = f"BANK_TOKEN_{len(by_raw) + 1}"
            by_raw[raw] = token
            tok.token_map[token] = raw
            tok.last4[token] = raw[-4:]
        token = by_raw[raw]
        parts += [text[pos:a], f"{token} (...{tok.last4[token]})"]
        pos = b
    parts.append(text[pos:])
    tok.text = "".join(parts)
    return tok


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def hash_candidates(tokens: list[str] | tuple[str, ...], tok: Tokenized) -> tuple[str, ...]:
    """Hashes Python computes for SHARED_ACCOUNT from the model's tokens.

    The model only names tokens. Python maps them back to the full numbers and hashes each one
    and every ordered pair / triple (routing + account), the same scheme the master data uses.
    """
    raws = [tok.token_map[t] for t in dict.fromkeys(tokens) if t in tok.token_map]
    hashes = set()
    for k in (1, 2, 3):
        hashes.update(_sha("".join(p)) for p in permutations(raws, k))
    return tuple(sorted(hashes))
