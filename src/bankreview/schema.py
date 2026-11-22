"""What the model may return: facts and exact quotes only.

There is deliberately NO field for risk level, signals, owner, hold, status or dollars at risk.
`extra="forbid"` makes any such field (or any other unknown field) a validation error.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bankreview import rules
from bankreview.tokenize import Tokenized, hash_candidates

TokenId = Annotated[str, Field(pattern=r"^BANK_TOKEN_\d+$")]


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claimed_supplier_name: str | None = None
    change_type: Literal["BANK_ACCOUNT", "CONTACT", "ADDRESS", "OTHER"]
    new_account_tokens: list[TokenId] = []
    new_bank_country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    new_bank_country_quote: str | None = None
    account_holder_names: list[str] = []
    effective_date: str | None = None
    phone_numbers_in_text: list[str] = []
    urgency_quotes: list[str] = []
    skip_verification_quotes: list[str] = []
    grey_quotes: list[str] = []
    instructions_to_ai_present: bool = False
    explanation: str = ""

    @model_validator(mode="after")
    def _country_needs_evidence(self) -> "Extraction":
        if self.new_bank_country and not self.new_bank_country_quote:
            raise ValueError("new_bank_country needs new_bank_country_quote (an exact quote)")
        return self


def to_facts(ext: Extraction, tok: Tokenized) -> rules.Facts:
    """Map the model's facts into the rules' Facts. Hashes are computed here, in Python."""
    return rules.Facts(
        bank_numbers=tuple(ext.new_account_tokens),  # token ids only; never full numbers
        account_hashes=hash_candidates(ext.new_account_tokens, tok),
        new_bank_countries=(ext.new_bank_country,) if ext.new_bank_country else (),
        account_holder_names=tuple(ext.account_holder_names),
        phone_numbers=tuple(ext.phone_numbers_in_text),
        urgency_quotes=tuple(ext.urgency_quotes),
        skip_verification_quotes=tuple(ext.skip_verification_quotes),
        grey_quotes=tuple(ext.grey_quotes),
    )


def json_schema_text() -> str:
    return json.dumps(Extraction.model_json_schema(), indent=1, sort_keys=True)
