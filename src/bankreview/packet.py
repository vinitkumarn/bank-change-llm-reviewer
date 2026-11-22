"""One JSON packet per request: the only thing the model ever sees (besides the system prompt).

- request metadata (trusted: it comes from our mail system, not from the body),
- the TOKENIZED body, wrapped as <UNTRUSTED>...</UNTRUSTED> with < and > escaped so the body
  cannot close the wrapper (I03),
- the bank tokens with their last 4 digits,
- the supplier's master summary: country, domain on file, contact emails and this supplier's
  accounts on file as token + last 4 only. No full bank number anywhere.
"""

from __future__ import annotations

import json

from bankreview.rules import Master, Request
from bankreview.tokenize import Tokenized

UNTRUSTED_OPEN, UNTRUSTED_CLOSE = "<UNTRUSTED>", "</UNTRUSTED>"


def escape(text: str) -> str:
    return text.replace("<", "&lt;").replace(">", "&gt;")


def wrap_body(tokenized_text: str) -> str:
    return UNTRUSTED_OPEN + escape(tokenized_text) + UNTRUSTED_CLOSE


def trusted_part(req: Request, master: Master, tok: Tokenized) -> dict:
    """Everything in the packet except the untrusted body."""
    return {
        "request": {
            "request_id": req.request_id,
            "channel": req.channel,
            "from_address": escape(req.from_address),
            "reply_to": escape(req.reply_to),
            "already_applied": req.already_applied,
        },
        "bank_tokens": [{"token": t, "last4": tok.last4[t]} for t in tok.last4],
        "supplier_master": {
            "vendor_name": master.vendor_name,
            "country": master.country,
            "domain_on_file": master.domain_on_file,
            "contact_emails": [c.email for c in master.contacts],
            "accounts_on_file": [
                {"account_token": a.account_token, "last4": a.last4, "is_active": a.is_active}
                for a in master.bank_accounts if a.vendor_id == master.vendor_id],
        },
    }


def build_packet(req: Request, master: Master, tok: Tokenized) -> dict:
    return {**trusted_part(req, master, tok), "body": wrap_body(tok.text)}


def packet_json(packet: dict) -> str:
    """Stable text: the user message and part of the cache key."""
    return json.dumps(packet, sort_keys=True, ensure_ascii=False, indent=1)

