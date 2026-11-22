"""A scripted stand-in for anthropic.Anthropic(). No network, ever.

It records every request (so tests can scan what would have been sent) and answers from a
script: a list of answers used in order, or a function of the request. An answer that is an
Exception is raised, like an API error.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace


def answer(**fields) -> str:
    base = {"change_type": "BANK_ACCOUNT", "explanation": "Facts extracted from the request."}
    base.update(fields)
    return json.dumps(base)


def packet_of(request: dict) -> dict:
    return json.loads(request["messages"][0]["content"])


def all_tokens_answer(request: dict) -> str:
    """A plain, valid answer: every bank token in the packet is the new account."""
    return answer(new_account_tokens=[t["token"] for t in packet_of(request)["bank_tokens"]])


class FakeClient:
    def __init__(self, script=all_tokens_answer):
        self.script = script
        self.requests: list[dict] = []
        self.messages = self  # client.messages.create(...)

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if callable(self.script):
            reply = self.script(kwargs)
        else:
            reply = self.script[len(self.requests) - 1]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=reply)], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1200, output_tokens=150,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))

    def sent_text(self) -> str:
        """Everything that would have left the machine: system prompts + all messages."""
        parts = []
        for r in self.requests:
            parts += [b["text"] for b in r["system"]]
            parts += [m["content"] for m in r["messages"]]
        return "\n".join(parts)
