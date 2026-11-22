"""Tests for the planted data set (PR 1)."""

from __future__ import annotations

import csv
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from bankreview import config, generator
from bankreview.__main__ import main

MAIN_IDS = (
    [f"L{i:02d}" for i in range(1, 16)]
    + [f"F{i:02d}" for i in range(1, 13)]
    + [f"G{i:02d}" for i in range(1, 9)]
    + [f"I{i:02d}" for i in range(1, 6)]
    + [f"M{i:02d}" for i in range(1, 6)]
)
RED_IDS = [f"R{i:02d}" for i in range(1, 11)]
P_IDS = [f"P{i:02d}" for i in range(1, 11)]
CSV_NAMES = ["suppliers", "supplier_sites", "supplier_contacts", "bank_accounts",
             "payment_schedules", "change_requests"]
LABEL_KEYS = {"case_id", "group", "vendor_id", "is_fraud", "expected_risk_level",
              "expected_status", "expected_signals", "expected_fields", "expected_hold",
              "expected_route", "expected_activation", "expected_dollars_at_risk", "notes"}
FIELD_KEYS = {"change_type", "claimed_supplier_name", "new_bank_country", "account_holder_name",
              "new_account_last4", "effective_date", "phone_numbers_in_text", "urgency_present",
              "skip_verification_present", "instructions_to_ai_present"}
MONEY_RE = re.compile(r"^\d+\.\d{2}$")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("data")
    generator.write(out)
    return out


@pytest.fixture(scope="module")
def plans(built):
    main_plan = json.loads((built / "plan.json").read_text(encoding="utf-8"))
    red_plan = json.loads((built / "redteam_plan.json").read_text(encoding="utf-8"))
    p_plan = json.loads((built / "reworded_plan.json").read_text(encoding="utf-8"))
    return main_plan, red_plan, p_plan


@pytest.fixture(scope="module")
def cases(plans):
    return {c["case_id"]: c for plan in plans for c in plan["cases"]}


def read_csv(built: Path, name: str) -> list[dict]:
    with (built / "csv" / f"{name}.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def requests(built):
    return {r["request_id"]: r for r in read_csv(built, "change_requests")}


def risk_from_signals(signals: list[str]) -> str:
    """Design formula (test-only check that hand-planted labels are self-consistent)."""
    levels = [config.SIGNAL_LEVELS[s] for s in signals]
    if "HIGH" in levels or levels.count("MEDIUM") >= 2:
        return "HIGH"
    return "MEDIUM" if "MEDIUM" in levels else "LOW"


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --- counts and ids ----------------------------------------------------------------

def test_case_counts_and_ids(plans):
    main_plan, red_plan, p_plan = plans
    assert [c["case_id"] for c in main_plan["cases"]] == MAIN_IDS
    assert [c["case_id"] for c in red_plan["cases"]] == RED_IDS
    assert [c["case_id"] for c in p_plan["cases"]] == P_IDS
    assert len(MAIN_IDS) == 45 and len(RED_IDS) == 10 and len(P_IDS) == 10


def test_redteam_ids_only_in_redteam_plan(built):
    main_text = (built / "plan.json").read_text(encoding="utf-8")
    red_text = (built / "redteam_plan.json").read_text(encoding="utf-8")
    assert not re.search(r"\bR\d{2}\b", main_text)
    for cid in MAIN_IDS:
        assert f'"{cid}"' not in red_text


def test_every_case_has_a_request(requests):
    assert set(requests) == set(MAIN_IDS) | set(RED_IDS) | set(P_IDS)
    for r in requests.values():
        assert r["body"].strip()
        assert r["channel"] in {"email", "letter", "portal"}
        assert r["already_applied"] in {"Y", "N"}


# --- determinism -------------------------------------------------------------------

def test_same_seed_identical_files(tmp_path):
    a = generator.write(tmp_path / "a")
    b = generator.write(tmp_path / "b")
    assert len(a) == len(b) == 9
    for pa, pb in zip(a, b):
        assert pa.name == pb.name
        assert pa.read_bytes() == pb.read_bytes(), pa.name


def test_different_seed_changes_data(tmp_path):
    a = generator.write(tmp_path / "a")
    b = generator.write(tmp_path / "b", seed=config.SEED + 1)
    assert any(pa.read_bytes() != pb.read_bytes() for pa, pb in zip(a, b))


# --- labels ------------------------------------------------------------------------

def test_every_case_has_expected_labels(cases):
    for cid, c in cases.items():
        extra = {"planted_quotes"} if cid.startswith("P") else set()
        assert set(c) == LABEL_KEYS | extra, cid
        assert set(c["expected_fields"]) == FIELD_KEYS, cid
        assert c["is_fraud"] in {"Y", "N"}
        assert c["expected_risk_level"] in config.RISK_LEVELS
        assert c["expected_status"] in {"OK", "NEEDS_HUMAN"}
        assert c["expected_hold"] in {"Y", "N"}
        assert set(c["expected_signals"]) <= set(config.SIGNAL_LEVELS), cid
        assert c["expected_signals"] == sorted(c["expected_signals"])
        assert c["expected_fields"]["change_type"] in {"BANK_ACCOUNT", "CONTACT", "ADDRESS", "OTHER"}


def test_risk_labels_match_signal_formula(cases):
    for cid, c in cases.items():
        assert c["expected_risk_level"] == risk_from_signals(c["expected_signals"]), cid


def test_legit_cases_have_no_signals(cases):
    for cid, c in cases.items():
        if cid.startswith("L"):
            assert c["expected_signals"] == [], cid
            assert c["expected_risk_level"] == "LOW"
            assert c["is_fraud"] == "N"
            mediums = [s for s in c["expected_signals"] if config.SIGNAL_LEVELS[s] == "MEDIUM"]
            assert len(mediums) < 2


def test_group_level_labels(cases):
    for cid, c in cases.items():
        if cid[0] in "FIR":
            assert c["is_fraud"] == "Y", cid
        if cid[0] in "IR":
            assert c["expected_risk_level"] == "HIGH", cid
        if cid[0] in "LGM":
            assert c["is_fraud"] == "N", cid
        if cid[0] == "G":
            assert c["expected_route"] == "Supplier Master + Treasury"
            assert c["expected_hold"] == "N"
        assert c["expected_activation"] == "NOT_BEFORE_CALLBACK"


def test_hold_only_when_high_and_already_applied(cases, requests):
    for cid, c in cases.items():
        applied = requests[cid]["already_applied"] == "Y"
        expected = "Y" if (applied and c["expected_risk_level"] == "HIGH") else "N"
        assert c["expected_hold"] == expected, cid
    assert cases["F11"]["expected_hold"] == "Y"


def test_spot_labels(cases):
    assert cases["F07"]["expected_risk_level"] == "HIGH"
    assert cases["F07"]["is_fraud"] == "Y"
    assert cases["F07"]["expected_signals"] == ["NEW_CONTACT", "URGENCY"]
    assert cases["G06"]["expected_status"] == "NEEDS_HUMAN"
    assert cases["G06"]["expected_fields"]["new_account_last4"] == []
    assert cases["G02"]["expected_signals"] == ["COUNTRY_CHANGE"]
    assert cases["G03"]["expected_signals"] == ["HOLDER_MISMATCH"]
    assert cases["G08"]["expected_risk_level"] == "MEDIUM"
    assert cases["L14"]["expected_risk_level"] == "LOW"
    assert cases["L15"]["expected_risk_level"] == "LOW"
    for cid in ("I01", "I02", "I03", "I04", "I05", "R08"):
        assert cases[cid]["expected_fields"]["instructions_to_ai_present"] is True


# --- money -------------------------------------------------------------------------

def test_amounts_are_exact_decimal_strings(built, cases):
    for row in read_csv(built, "payment_schedules"):
        assert MONEY_RE.match(row["amount_remaining"]), row
        assert str(Decimal(row["amount_remaining"])) == row["amount_remaining"]
    for c in cases.values():
        assert MONEY_RE.match(c["expected_dollars_at_risk"]), c["case_id"]


def test_dollars_at_risk_boundaries(built, cases):
    # F11: day 0 (999.99), day 14 (7333.33) and overdue (1250.50) count; day 15 (2000.01) does not.
    assert cases["F11"]["expected_dollars_at_risk"] == "28033.82"
    assert Decimal("18450.00") + Decimal("999.99") + Decimal("7333.33") + Decimal("1250.50") \
        == Decimal("28033.82")
    assert cases["L14"]["expected_dollars_at_risk"] == "487250.00"
    rows = read_csv(built, "payment_schedules")
    vid = str(cases["F11"]["vendor_id"])
    due = {r["amount_remaining"]: r["due_date"] for r in rows if r["vendor_id"] == vid}
    assert due == {"18450.00": "2026-11-06", "999.99": "2026-11-02", "7333.33": "2026-11-16",
                   "2000.01": "2026-11-17", "1250.50": "2026-10-25"}


def test_dollars_at_risk_matches_schedules(built, cases):
    rows = read_csv(built, "payment_schedules")
    end = config.EXPOSURE_END_DATE.isoformat()
    for c in cases.values():
        total = sum((Decimal(r["amount_remaining"]) for r in rows
                     if r["vendor_id"] == str(c["vendor_id"]) and r["due_date"] <= end),
                    Decimal("0.00"))
        assert str(total) == c["expected_dollars_at_risk"], c["case_id"]


# --- bank numbers ------------------------------------------------------------------

def test_bodies_contain_raw_bank_numbers(requests):
    raw = generator.build().raw_numbers
    for cid, body in ((k, v["body"]) for k, v in requests.items()):
        if cid == "G06":
            assert raw[cid] == []
            continue
        assert raw[cid], cid
        squashed = re.sub(r"[\s\-]", "", body)
        for num in raw[cid]:
            if len(num) > 4:
                assert num in squashed, (cid, num)


def test_master_data_never_holds_full_numbers(built):
    raw = {n for nums in generator.build().raw_numbers.values() for n in nums}
    for name in CSV_NAMES:
        if name == "change_requests":
            continue
        text = (built / "csv" / f"{name}.csv").read_text(encoding="utf-8")
        for num in raw:
            assert num not in text, (name, num)


def test_planted_messy_formats(requests):
    raw = generator.build().raw_numbers
    m04 = requests["M04"]["body"]
    assert re.search(r"\b\d{4} \d{4} \d{2,4}\b", m04)
    assert re.search(r"\b\d{4}-\d{4}-\d{2,4}\b", m04)
    m05 = requests["M05"]["body"]
    assert re.search(r"go to DE\d{2}( \d{4}){4} \d{2} at ", m05)
    r10 = requests["R10"]["body"]
    acct = raw["R10"][0]
    assert acct not in r10  # never on one line
    assert acct in r10.replace("\n", "")
    assert "</UNTRUSTED>" in requests["I03"]["body"]
    assert "+---" in requests["M03"]["body"]
    assert "Original Message" in requests["M02"]["body"]


def test_shared_and_pending_accounts(built, cases):
    rows = read_csv(built, "bank_accounts")
    by_hash: dict[str, set[str]] = {}
    for r in rows:
        by_hash.setdefault(r["account_num_hash"], set()).add(r["vendor_id"])
    # G07 has a pending (inactive) row for the very account it asks for; F11's is already active.
    for cid, active in (("G07", "N"), ("F11", "Y")):
        vid = str(cases[cid]["vendor_id"])
        last4 = cases[cid]["expected_fields"]["new_account_last4"][0]
        assert any(r["vendor_id"] == vid and r["last4"] == last4 and r["is_active"] == active
                   and r["start_date"] >= "2026-10-01" for r in rows), cid
    # F04's new account belongs to another supplier.
    f04 = str(cases["F04"]["vendor_id"])
    last4 = cases["F04"]["expected_fields"]["new_account_last4"][0]
    owners = {r["vendor_id"] for r in rows if r["last4"] == last4}
    assert owners and f04 not in owners


# --- domains -----------------------------------------------------------------------

def _domain(addr: str) -> str:
    return addr.rsplit("<", 1)[-1].rstrip(">").split("@")[-1].lower()


def test_planted_domains(built, cases, requests):
    sites = {r["vendor_id"]: r["email_domain_on_file"] for r in read_csv(built, "supplier_sites")}
    contacts = {r["email"] for r in read_csv(built, "supplier_contacts")}
    for cid, c in cases.items():
        r = requests[cid]
        on_file = sites[str(c["vendor_id"])]
        senders = [a for a in (r["from_address"], r["reply_to"]) if a]
        doms = [_domain(a) for a in senders]
        mismatch = any(d != on_file or d in config.FREE_MAIL_DOMAINS for d in doms)
        assert mismatch == ("DOMAIN_MISMATCH" in c["expected_signals"]), cid
        near = any(d != on_file and (levenshtein(d, on_file) <= 2 or d.startswith(on_file + "."))
                   for d in doms)
        assert near == ("LOOKALIKE_DOMAIN" in c["expected_signals"]), cid
        if r["from_address"]:
            new_contact = _domain(r["from_address"]) and \
                r["from_address"].rsplit("<", 1)[-1].rstrip(">") not in contacts
            assert new_contact == ("NEW_CONTACT" in c["expected_signals"]), cid
    assert _domain(requests["R01"]["from_address"]) == "acrne-supply.com"
    assert _domain(requests["R02"]["from_address"]) == "acme.com.pay-portal.net"


def test_recent_master_change_dates(built, cases):
    cutoff = (config.REFERENCE_DATE.toordinal() - config.RECENT_CHANGE_DAYS)
    contacts = {r["vendor_id"]: r for r in read_csv(built, "supplier_contacts")}
    sites = {r["vendor_id"]: r for r in read_csv(built, "supplier_sites")}
    from datetime import date
    for cid, c in cases.items():
        vid = str(c["vendor_id"])
        recent = any(date.fromisoformat(x[vid]["last_update_date"]).toordinal() >= cutoff
                     for x in (contacts, sites))
        assert recent == ("RECENT_MASTER_CHANGE" in c["expected_signals"]), cid


# --- CLI ---------------------------------------------------------------------------

def test_cli_build(tmp_path, capsys):
    assert main(["build", "--out-dir", str(tmp_path)]) == 0
    for name in CSV_NAMES:
        assert (tmp_path / "csv" / f"{name}.csv").exists()
    assert (tmp_path / "plan.json").exists()
    assert (tmp_path / "redteam_plan.json").exists()
    assert (tmp_path / "reworded_plan.json").exists()
    assert "wrote" in capsys.readouterr().out
