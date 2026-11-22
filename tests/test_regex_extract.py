"""Regex extraction: bank numbers, countries, holders, phones, keyword quotes."""

from __future__ import annotations

import hashlib

from bankreview import generator, regex_extract
from bankreview.regex_extract import extract
from bankreview.rules import Request


def test_spaced_and_dashed_account_numbers():
    f = extract("Account number: 1234 5678 90\n(as on our cheques: 1234-5678-90)")
    assert f.bank_numbers == ("1234567890",)


def test_iban_mid_sentence_and_country():
    iban = generator.iban("DE", "370400440532013000")
    spaced = generator.spaced(iban)
    f = extract(f"From next month our payments should go to {spaced} at Hanse Sparkasse, "
                "held in the name of Granite Foods GmbH, and the old account will close.")
    assert iban in f.bank_numbers
    assert f.new_bank_countries == ("DE",)
    assert f.account_holder_names == ("Granite Foods GmbH",)


def test_invalid_iban_check_digits_ignored():
    f = extract("Reference DE00 3704 0044 0532 0130 00")
    assert f.new_bank_countries == ()


def test_phones_invoices_money_and_dates_are_not_bank_numbers():
    f = extract("Call +1 646 555 0187. Invoice INV-1015-001 for USD 487,250.00 and 18450.00 "
                "is due 2026-11-06, ref NOA-2026-118, on 7 November 2026.")
    assert f.bank_numbers == ()
    assert f.phone_numbers == ("+1 646 555 0187",)


def test_ifsc_and_account():
    f = extract("IFSC: DECB0123456\nAccount number: 123456789012")
    assert f.bank_numbers == ("DECB0123456", "123456789012")
    assert hashlib.sha256(b"DECB0123456123456789012").hexdigest() in f.account_hashes


def test_routing_plus_account_hash_in_any_order():
    canonical = "021000021" + "1234567890"
    want = hashlib.sha256(canonical.encode()).hexdigest()
    a = extract("Routing number (ABA): 021000021\nAccount number: 1234567890")
    b = extract("the new account is 1234567890 at First Harbor Bank, routing 021000021.")
    assert want in a.account_hashes and want in b.account_hashes


def test_holder_from_table():
    body = ("+------+-----+-----------------+\n| Field | Old | New |\n"
            "| Account name | (on file) | Kestrel Print Ltd |\n+---+")
    assert extract(body).account_holder_names == ("Kestrel Print Ltd",)


def test_urgency_quotes_are_exact_substrings():
    body = "This must be updated TODAY, before Friday's payment run."
    f = extract(body)
    assert f.urgency_quotes
    for q in f.urgency_quotes:
        assert q in body


def test_month_end_is_not_urgency():
    assert extract("Please process by month-end if that suits your normal cycle.") \
        .urgency_quotes == ()
    assert extract("There is no rush on our side.").urgency_quotes == ()


def test_skip_verification_quotes():
    assert extract("there is no need for a call-back.").skip_verification_quotes
    assert extract("Treasury has already verified this change.").skip_verification_quotes
    assert extract("Thank you for verifying our details.").skip_verification_quotes == ()


def test_grey_quotes():
    assert extract("Following the merger of our bank").grey_quotes == ("merger",)
    assert extract("Please update our bank details.").grey_quotes == ()


def test_sender_domains():
    req = Request("T", 1, "email", "Acme Accounts <a@Acme.com>", "b@pay.net", "N", "")
    assert regex_extract.sender_domains(req) == ("acme.com", "pay.net")
    letter = Request("T", 1, "letter", "", "", "N", "")
    assert regex_extract.sender_domains(letter) == ()
