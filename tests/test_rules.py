"""Rules: each signal fires / does not fire, risk formula, money and hold edges."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bankreview import baselines, config, db, rules
from bankreview.rules import BankAccount, Contact, Facts, Master, Request, Schedule, Signal

REF = config.REFERENCE_DATE
OLD = date(2025, 1, 15)
HASH_A = "a" * 64


def make_request(**kw) -> Request:
    base = dict(request_id="T01", vendor_id=1, channel="email",
                from_address="anna.adams@northwind-tools.com", reply_to="",
                already_applied="N", body="Please update our bank details.")
    base.update(kw)
    return Request(**base)


def make_master(**kw) -> Master:
    base = dict(
        vendor_id=1, vendor_name="Northwind Tools Inc", country="US",
        domain_on_file="northwind-tools.com", site_last_update_date=OLD,
        contacts=(Contact("anna.adams@northwind-tools.com", "+1 212 555 0101", OLD),),
        bank_accounts=(BankAccount("ACCT-000001", "1234", HASH_A, 2, "Y"),),
        schedules=())
    base.update(kw)
    return Master(**base)


def codes(req: Request, master: Master, facts: Facts = Facts()) -> set[str]:
    return {s.code for s in rules.evaluate_signals(req, master, facts)}


# --- each signal fires and does not fire --------------------------------------------

def test_clean_request_has_no_signals():
    assert codes(make_request(), make_master()) == set()


def test_domain_mismatch():
    m = make_master()
    assert rules.domain_mismatch(make_request(), m, Facts()) is None
    assert rules.domain_mismatch(make_request(from_address="a@remit-services.net"), m, Facts())
    assert rules.domain_mismatch(make_request(from_address="anna@gmail.com"), m, Facts())
    # Reply-to counts too.
    assert rules.domain_mismatch(make_request(reply_to="x@northwind-finance.com"), m, Facts())
    # Display name does not fool it.
    assert rules.domain_mismatch(
        make_request(from_address="Northwind Tools <a@remit-services.net>"), m, Facts())


def test_lookalike_domain():
    m = make_master()
    assert rules.lookalike_domain(make_request(), m, Facts()) is None
    assert rules.lookalike_domain(make_request(from_address="a@nortwind-tools.com"), m, Facts())
    assert rules.lookalike_domain(make_request(from_address="a@remit-services.net"), m,
                                  Facts()) is None


def test_lookalike_homoglyph_and_subdomain_trick():
    m = make_master(domain_on_file="acme.com")
    assert rules.lookalike_domain(make_request(from_address="a@acme.com.pay-portal.net"), m,
                                  Facts())
    m2 = make_master(domain_on_file="mammoth-supplies.com")
    # 'rnarnrnoth' is 3+ edits away but reads the same as 'mammoth'.
    assert rules.lookalike_domain(make_request(from_address="a@rnarnrnoth-supplies.com"), m2,
                                  Facts())


def test_lookalike_edit_distance_exactly_2_vs_3():
    m = make_master(domain_on_file="northwind-tools.com")
    two = "a@nrothwind-tools.com"  # two letters swapped = distance 2
    three = "a@nrothwind-tool.com"  # swap + one dropped = distance 3
    assert rules.levenshtein("nrothwind-tools.com", "northwind-tools.com") == 2
    assert rules.levenshtein("nrothwind-tool.com", "northwind-tools.com") == 3
    assert rules.lookalike_domain(make_request(from_address=two), m, Facts())
    assert rules.lookalike_domain(make_request(from_address=three), m, Facts()) is None
    # Distance 3 is still a different domain.
    assert rules.domain_mismatch(make_request(from_address=three), m, Facts())


def test_country_change():
    m = make_master()
    assert rules.country_change(make_request(), m, Facts(new_bank_countries=("US",))) is None
    assert rules.country_change(make_request(), m, Facts(new_bank_countries=("GB",)))
    assert rules.country_change(make_request(), m, Facts()) is None


def test_shared_account():
    m = make_master()  # HASH_A belongs to vendor 2
    assert rules.shared_account(make_request(), m, Facts(account_hashes=(HASH_A,)))
    assert rules.shared_account(make_request(), m, Facts(account_hashes=("b" * 64,))) is None
    own = make_master(bank_accounts=(BankAccount("ACCT-000002", "1234", HASH_A, 1, "N"),))
    assert rules.shared_account(make_request(), own, Facts(account_hashes=(HASH_A,))) is None


def test_holder_mismatch():
    m = make_master()
    assert rules.holder_mismatch(make_request(), m,
                                 Facts(account_holder_names=("NORTHWIND TOOLS, INC.",))) is None
    assert rules.holder_mismatch(make_request(), m,
                                 Facts(account_holder_names=("Adams Holdings LLC",)))


def test_skip_verification():
    m = make_master()
    assert rules.skip_verification(make_request(), m, Facts()) is None
    assert rules.skip_verification(make_request(), m,
                                   Facts(skip_verification_quotes=("no need to call",)))
    assert rules.skip_verification(make_request(), m, Facts(phone_numbers=("+1 646 555 0187",)))


def test_phone_compared_by_digits_only():
    m = make_master(contacts=(Contact("anna.adams@northwind-tools.com", "+14085550101", OLD),))
    facts = Facts(phone_numbers=("+1 408-555-0101",))
    assert rules.skip_verification(make_request(), m, facts) is None
    assert rules.phone_digits("+1 (408) 555-0101") == "14085550101"


def test_urgency():
    m = make_master()
    assert rules.urgency(make_request(), m, Facts()) is None
    assert rules.urgency(make_request(), m, Facts(urgency_quotes=("today",)))


def test_recent_master_change_30_day_window():
    req = make_request()
    for days, fires in ((0, True), (10, True), (30, True), (31, False)):
        c = (Contact("anna.adams@northwind-tools.com", "+1 212 555 0101",
                     REF - timedelta(days=days)),)
        assert bool(rules.recent_master_change(req, make_master(contacts=c), Facts())) is fires
        s = make_master(site_last_update_date=REF - timedelta(days=days))
        assert bool(rules.recent_master_change(req, s, Facts())) is fires


def test_new_contact():
    m = make_master()
    assert rules.new_contact(make_request(), m, Facts()) is None
    assert rules.new_contact(make_request(from_address="ben.brandt@northwind-tools.com"), m,
                             Facts())


def test_letters_have_no_sender_signals():
    letter = make_request(channel="letter", from_address="", reply_to="")
    m = make_master()
    for fn in (rules.domain_mismatch, rules.lookalike_domain, rules.new_contact):
        assert fn(letter, m, Facts()) is None
    assert codes(letter, m) == set()


# --- risk level ---------------------------------------------------------------------

def sig(code: str) -> Signal:
    return Signal(code, config.SIGNAL_LEVELS[code], "test")


def test_risk_level_formula():
    assert rules.risk_level([]) == "LOW"
    assert rules.risk_level([sig("URGENCY")]) == "MEDIUM"
    assert rules.risk_level([sig("URGENCY"), sig("NEW_CONTACT")]) == "HIGH"  # two MEDIUMs
    assert rules.risk_level([sig("COUNTRY_CHANGE")]) == "HIGH"


def test_higher_level_never_downgrades():
    assert rules.higher_level("HIGH", "LOW") == "HIGH"
    assert rules.higher_level("LOW", "HIGH") == "HIGH"
    assert rules.higher_level("LOW", "MEDIUM") == "MEDIUM"
    assert rules.higher_level("MEDIUM", "LOW") == "MEDIUM"
    assert rules.higher_level("LOW", "LOW") == "LOW"


# --- dollars at risk ----------------------------------------------------------------

def sched(amount: str, days: int) -> Schedule:
    return Schedule(f"INV-{days}", Decimal(amount), REF + timedelta(days=days))


def test_dollars_day_14_in_day_15_out():
    assert rules.dollars_at_risk([sched("100.00", 14)]) == Decimal("100.00")
    assert rules.dollars_at_risk([sched("100.00", 15)]) == Decimal("0.00")


def test_dollars_overdue_counted_and_zero_excluded():
    rows = [sched("1250.50", -8), sched("999.99", 0), sched("0.00", 3), sched("-50.00", 2)]
    assert rules.dollars_at_risk(rows) == Decimal("2250.49")


def test_dollars_exact_decimal():
    total = rules.dollars_at_risk([sched("0.10", 1), sched("0.20", 2)])
    assert total == Decimal("0.30") and isinstance(total, Decimal)


def test_dates_come_from_reference_date_not_clock():
    """Same data -> same answer, whatever today is."""
    rows = [sched("10.00", 14)]
    assert rules.dollars_at_risk(rows) == rules.dollars_at_risk(rows, REF, config.EXPOSURE_DAYS)
    assert rules.dollars_at_risk(rows, REF + timedelta(days=-1)) == Decimal("0.00")


def test_no_clock_reads_in_source():
    src = Path(rules.__file__).parent
    for path in src.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(?:date|datetime)\.today\(|\bdatetime\.now\(|\btime\.time\(",
                             text), path.name


# --- hold, owner, actions -----------------------------------------------------------

def test_hold_only_when_high_and_already_applied():
    assert rules.hold("HIGH", "Y") == "Y"
    assert rules.hold("HIGH", "N") == "N"
    assert rules.hold("MEDIUM", "Y") == "N"
    assert rules.hold("LOW", "Y") == "N"


def test_owner():
    assert rules.owner("LOW", False) == "Supplier Master"
    assert rules.owner("MEDIUM", False) == "Supplier Master"
    assert rules.owner("HIGH", False) == "Supplier Master + Treasury"
    assert rules.owner("LOW", True) == "Supplier Master + Treasury"


def test_actions_always_call_back_on_file_and_no_activation():
    for level in ("LOW", "MEDIUM", "HIGH"):
        acts = rules.actions(level, "N", "+1 212 555 0101", False)
        assert "+1 212 555 0101" in acts[0] and "ON FILE" in acts[0]
        assert any("Do not activate" in a for a in acts)
        assert not any("Hold" in a for a in acts)
    assert any("Hold" in a for a in rules.actions("HIGH", "Y", "x", False))


def test_review_already_applied_y_vs_n():
    req_n = make_request(from_address="anna@gmail.com", body="Account number: 123456789")
    req_y = replace(req_n, already_applied="Y")
    m = make_master()
    assert baselines.rules_only(req_n, m).hold == "N"
    r = baselines.rules_only(req_y, m)
    assert r.risk_level == "HIGH" and r.hold == "Y"
    assert r.owner == "Supplier Master + Treasury"


def test_status_needs_human_without_bank_number():
    m = make_master()
    assert baselines.rules_only(make_request(body="Details follow by letter."), m).status \
        == "NEEDS_HUMAN"
    assert baselines.rules_only(make_request(body="Account number: 123456789"), m).status == "OK"


# --- on the generated data ----------------------------------------------------------

def test_l15_month_end_stays_low(con):
    r = baselines.run_case(con, "L15", "rules")
    assert "month-end" in db.load_case(con, "L15")[0].body
    assert r.risk_level == "LOW"
    assert "URGENCY" not in r.signal_codes


def test_f11_hold_and_dollars(con):
    r = baselines.run_case(con, "F11", "rules")
    assert r.risk_level == "HIGH" and r.hold == "Y"
    assert r.dollars_at_risk == Decimal("28033.82")


@pytest.mark.parametrize("by", ["naive", "rules"])
def test_run_case_returns_review(con, by):
    r = baselines.run_case(con, "F01", by)
    assert r.risk_level == "HIGH" and r.by == by
