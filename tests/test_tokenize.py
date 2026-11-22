"""Bank-number tokens: every planted form is replaced; phones, dates and amounts are not."""

from __future__ import annotations

import hashlib

from bankreview import generator
from bankreview.tokenize import TOKEN_RE, hash_candidates, tokenize


def test_spaced_and_dashed_account_same_token():
    t = tokenize("Account number: 1234 5678 90\n(as on our cheques: 1234-5678-90)")
    assert t.token_map == {"BANK_TOKEN_1": "1234567890"}
    assert t.text == ("Account number: BANK_TOKEN_1 (...7890)\n"
                      "(as on our cheques: BANK_TOKEN_1 (...7890))")


def test_iban_spaced_dashed_and_mid_sentence():
    iban = generator.iban("DE", "370400440532013000")
    dashed = generator.spaced(iban).replace(" ", "-")
    t = tokenize(f"Pay {generator.spaced(iban)} or {dashed} or {iban}, thanks.")
    assert list(t.token_map.values()) == [iban]
    assert t.text == "Pay BANK_TOKEN_1 (...3000) or BANK_TOKEN_1 (...3000) or " \
                     "BANK_TOKEN_1 (...3000), thanks."


def test_line_split_number_is_one_token():
    t = tokenize("Account number: 12345\n678901\nThanks")
    assert t.token_map == {"BANK_TOKEN_1": "12345678901"}
    assert t.text == "Account number: BANK_TOKEN_1 (...8901)\nThanks"
    quoted = tokenize("> account 123456\n> 7890123\n> bye")
    assert quoted.token_map == {"BANK_TOKEN_1": "1234567890123"}


def test_ifsc_sort_code_routing():
    t = tokenize("IFSC DECB0123456, sort code 12-34-56, routing 021000021.")
    assert sorted(t.token_map.values()) == ["021000021", "123456", "DECB0123456"]
    assert "12-34-56" not in t.text and "DECB0123456" not in t.text


def test_phones_dates_amounts_and_codes_untouched():
    text = ("Call +1 212 555 0101 or (408) 555-0101. Due 2026-11-16 for USD 487,250.00, "
            "invoice INV-1015-001, notice NOA-2026-118, institution 001.")
    t = tokenize(text)
    assert t.token_map == {} and t.text == text


def test_safety_net_catches_glued_numbers():
    t = tokenize("acct no.123456789012 ok")
    assert "123456789012" not in t.text
    assert TOKEN_RE.search(t.text)


def test_every_planted_body_is_clean_after_tokenizing():
    built = generator.build()
    for row in built.tables["change_requests"]:
        t = tokenize(row["body"])
        squashed = "".join(t.text.split()).replace("-", "")
        for num in built.raw_numbers[row["request_id"]]:
            assert num not in t.text, row["request_id"]
            if len(num) >= 8:
                assert num not in squashed, row["request_id"]


def test_hash_candidates_rebuild_routing_plus_account():
    t = tokenize("routing 021000021, account 1234567890")
    want = hashlib.sha256(b"0210000211234567890").hexdigest()
    assert want in hash_candidates(list(t.token_map), t)
    assert hash_candidates(["BANK_TOKEN_9"], t) == ()


def test_repr_hides_numbers():
    assert "1234567890" not in repr(tokenize("account 1234567890"))
