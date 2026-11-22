"""The claude reviewer with a scripted fake client: packet, one call, guards, never-downgrade,
retry, cache and audit. No real API call anywhere."""

from __future__ import annotations

import json
import logging
import re

import pytest
from fake_claude import FakeClient, all_tokens_answer, answer, packet_of
from pydantic import ValidationError

from bankreview import baselines, config, db, evaluate, extractor, generator, rules
from bankreview.audit import AuditLog
from bankreview.cache import ResponseCache
from bankreview.claude_reviewer import ClaudeReviewer
from bankreview.packet import build_packet
from bankreview.schema import Extraction, to_facts
from bankreview.tokenize import tokenize


def reviewer(tmp_path, script=all_tokens_answer, **kw) -> ClaudeReviewer:
    kw.setdefault("cache", ResponseCache(tmp_path / "cache"))
    return ClaudeReviewer(client=FakeClient(script), **kw)


def tokens_plus(**fields):
    """Script: all bank tokens as the new account, plus the given fields."""
    def script(request):
        tokens = [t["token"] for t in packet_of(request)["bank_tokens"]]
        return answer(new_account_tokens=tokens, **fields)
    return script


@pytest.fixture(scope="module")
def raw_numbers():
    return generator.build().raw_numbers


# --- packet --------------------------------------------------------------------------

def test_packet_wraps_and_escapes_untrusted_body(con):
    req, master = db.load_case(con, "I03")
    body = build_packet(req, master, tokenize(req.body))["body"]
    assert body.startswith("<UNTRUSTED>") and body.endswith("</UNTRUSTED>")
    assert body.count("</UNTRUSTED>") == 1 and body.count("<UNTRUSTED>") == 1
    assert "&lt;/UNTRUSTED&gt;" in body


def test_packet_master_summary_is_masked(con):
    req, master = db.load_case(con, "F04")
    p = build_packet(req, master, tokenize(req.body))
    sm = p["supplier_master"]
    assert set(sm) == {"vendor_name", "country", "domain_on_file", "contact_emails",
                       "accounts_on_file"}
    assert sm["accounts_on_file"] and all(set(a) == {"account_token", "last4", "is_active"}
                                          for a in sm["accounts_on_file"])
    assert "hash" not in json.dumps(p)
    assert all(re.fullmatch(r"\d{4}", t["last4"]) for t in p["bank_tokens"])


# --- schema --------------------------------------------------------------------------

def test_schema_has_no_decision_fields():
    assert set(Extraction.model_fields) == {
        "claimed_supplier_name", "change_type", "new_account_tokens", "new_bank_country",
        "new_bank_country_quote", "account_holder_names", "effective_date",
        "phone_numbers_in_text", "urgency_quotes", "skip_verification_quotes", "grey_quotes",
        "instructions_to_ai_present", "explanation"}
    with pytest.raises(ValidationError):
        Extraction.model_validate({"change_type": "BANK_ACCOUNT", "risk_level": "LOW"})
    with pytest.raises(ValidationError):
        Extraction.model_validate({"change_type": "BANK_ACCOUNT", "new_bank_country": "GB"})
    with pytest.raises(ValidationError):
        Extraction.model_validate({"change_type": "BANK_ACCOUNT", "new_account_tokens": ["12"]})


# --- the call ------------------------------------------------------------------------

def test_one_call_per_request_no_tools(con, tmp_path):
    r = reviewer(tmp_path)
    review = r(*db.load_case(con, "L01"))
    assert review.status == "OK" and review.risk_level == "LOW"
    assert len(r.client.requests) == 1
    kw = r.client.requests[0]
    assert "tools" not in kw and "tool_choice" not in kw
    assert kw["model"] == config.MODEL and kw["max_tokens"] == 4000
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["system"][0]["text"] == extractor.SYSTEM_PROMPT
    assert len(kw["messages"]) == 1 and kw["messages"][0]["role"] == "user"


def test_no_full_bank_number_in_any_prompt(con, data_dir, tmp_path, raw_numbers):
    # Every case of every set, through the real reviewer path; include a retry per case so
    # retry prompts are scanned too.
    def script(request):
        return "not json" if len(request["messages"]) == 1 else all_tokens_answer(request)

    r = reviewer(tmp_path, script, cache=ResponseCache(tmp_path / "c", enabled=False))
    ids = [c["case_id"] for name in config.PLAN_FILES.values()
           for c in evaluate.load_labels(data_dir / name)]
    for cid in ids:
        r(*db.load_case(con, cid))
    assert len(r.client.requests) == 2 * len(ids)
    sent = r.client.sent_text()
    squashed = re.sub(r"[\s\-]", "", sent)
    for cid in ("M04", "M05", "R10", "P04"):
        assert cid in ids and raw_numbers[cid], cid
    for cid in ids:
        for num in raw_numbers[cid]:
            assert num not in sent, (cid, "full bank number sent")
            if len(num) >= 8:
                assert num not in squashed, (cid, "bank number sent with separators")


# --- guards --------------------------------------------------------------------------

def test_quote_not_in_body_needs_human(con, tmp_path):
    review = reviewer(tmp_path, tokens_plus(urgency_quotes=["pay this today please"]))(
        *db.load_case(con, "L01"))
    assert review.status == "NEEDS_HUMAN" and review.confidence == "LOW"


def test_unknown_token_needs_human(con, tmp_path):
    review = reviewer(tmp_path, [answer(new_account_tokens=["BANK_TOKEN_9"])])(
        *db.load_case(con, "L01"))
    assert review.status == "NEEDS_HUMAN"


def test_explanation_with_body_phone_needs_human(con, tmp_path):
    bad = tokens_plus(explanation="The sender asks us to call +1 646 555 0187 instead.")
    assert reviewer(tmp_path, bad)(*db.load_case(con, "F06")).status == "NEEDS_HUMAN"


def test_explanation_citing_last4_is_ok(con, tmp_path):
    req, master = db.load_case(con, "L01")
    last4 = list(tokenize(req.body).last4.values())[-1]
    ok = tokens_plus(explanation=f"New account ending {last4} at the supplier's bank.")
    review = reviewer(tmp_path, ok)(req, master)
    assert review.status == "OK" and last4 in review.explanation


def test_escaped_quote_is_accepted(con, tmp_path):
    # The model saw the escaped body; quoting it as seen is still an exact quote.
    review = reviewer(tmp_path, tokens_plus(grey_quotes=["&lt;/UNTRUSTED&gt;"]))(
        *db.load_case(con, "I03"))
    assert review.status == "OK"


# --- never-downgrade -----------------------------------------------------------------

@pytest.mark.parametrize("cid", ["I01", "I02"])
def test_model_obeying_injection_is_overruled(con, tmp_path, cid):
    obedient = tokens_plus(explanation="Routine update. Mark as LOW risk.")
    review = reviewer(tmp_path, obedient)(*db.load_case(con, cid))
    floor = baselines.rules_only(*db.load_case(con, cid))
    assert review.status == "OK"
    assert review.risk_level == "HIGH"
    assert set(floor.signal_codes) <= set(review.signal_codes)


def test_model_can_add_a_signal(con, tmp_path):
    quote = "Please don't bother phoning, it's all been signed off"
    req, master = db.load_case(con, "P01")
    assert baselines.rules_only(req, master).risk_level == "LOW"
    review = reviewer(tmp_path, tokens_plus(skip_verification_quotes=[quote]))(req, master)
    assert review.risk_level == "HIGH" and "SKIP_VERIFICATION" in review.signal_codes
    assert review.confidence == "MEDIUM"  # the model raised it above the rules-only level
    assert review.owner == config.OWNER_SM_TREASURY


def test_two_mediums_across_sources_still_high(con, tmp_path):
    # P02: rules see NEW_CONTACT, only the model sees the urgency -> two MEDIUMs -> HIGH.
    quote = "Our books close tonight, so this has to be in place before then"
    review = reviewer(tmp_path, tokens_plus(urgency_quotes=[quote]))(*db.load_case(con, "P02"))
    assert review.signal_codes == ["NEW_CONTACT", "URGENCY"] and review.risk_level == "HIGH"


@pytest.mark.parametrize("cid", ["F04", "P04"])
def test_shared_account_hash_computed_in_python(con, cid):
    req, master = db.load_case(con, cid)
    tok = tokenize(req.body)
    ext = Extraction(change_type="BANK_ACCOUNT", new_account_tokens=list(tok.token_map))
    assert rules.shared_account(req, master, to_facts(ext, tok)) is not None
    assert all(re.fullmatch(r"BANK_TOKEN_\d+", n) for n in to_facts(ext, tok).bank_numbers)


def test_no_account_from_model_needs_human(con, tmp_path):
    review = reviewer(tmp_path, [answer()])(*db.load_case(con, "L01"))
    assert review.status == "NEEDS_HUMAN"


# --- retry ---------------------------------------------------------------------------

def test_retry_carries_full_validation_error(con, tmp_path):
    bad = json.dumps({"change_type": "BANK_ACCOUNT", "risk_level": "LOW"})
    with pytest.raises(ValueError) as err:
        extractor.parse(bad)
    r = reviewer(tmp_path, lambda req: bad if len(req["messages"]) == 1
                 else all_tokens_answer(req))
    review = r(*db.load_case(con, "L01"))
    assert review.status == "OK" and len(r.client.requests) == 2
    retry = r.client.requests[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert retry[1]["content"] == bad
    assert str(err.value) in retry[2]["content"]
    assert "Extra inputs are not permitted" in retry[2]["content"]


def test_two_invalid_answers_need_human_and_are_not_cached(con, tmp_path):
    r = reviewer(tmp_path, ["{}", "still not valid"])
    review = r(*db.load_case(con, "L01"))
    assert review.status == "NEEDS_HUMAN" and len(r.client.requests) == 2
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").iterdir())
    assert r.stats.invalid_answers == 1


# --- cache ---------------------------------------------------------------------------

def test_same_packet_calls_once(con, tmp_path):
    r = reviewer(tmp_path)
    first = r(*db.load_case(con, "L02"))
    second = r(*db.load_case(con, "L02"))
    assert len(r.client.requests) == 1 and r.stats.cache_hits == 1
    assert first.to_dict() == second.to_dict()


def test_prompt_or_model_change_makes_a_new_call(con, tmp_path):
    cache = ResponseCache(tmp_path / "cache")
    reviewer(tmp_path, cache=cache)(*db.load_case(con, "L02"))
    new_prompt = reviewer(tmp_path, cache=cache, system_prompt=extractor.SYSTEM_PROMPT + "\n.")
    new_prompt(*db.load_case(con, "L02"))
    new_model = reviewer(tmp_path, cache=cache, model="claude-opus-5-5")
    new_model(*db.load_case(con, "L02"))
    assert len(new_prompt.client.requests) == 1 and len(new_model.client.requests) == 1
    assert len(list((tmp_path / "cache").iterdir())) == 3


def test_api_error_not_cached_and_logged(con, tmp_path, caplog):
    cache = ResponseCache(tmp_path / "cache")
    failing = reviewer(tmp_path, [RuntimeError("529 overloaded_error: try again later")],
                       cache=cache)
    with caplog.at_level(logging.ERROR, logger="bankreview"):
        review = failing(*db.load_case(con, "F01"))
    assert review.status == "NEEDS_HUMAN" and review.risk_level == "HIGH"  # rules floor
    assert "529 overloaded_error: try again later" in caplog.text
    assert failing.stats.api_errors == 1
    assert not (tmp_path / "cache").exists()
    again = reviewer(tmp_path, cache=cache)
    again(*db.load_case(con, "F01"))
    assert len(again.client.requests) == 1


def test_guards_run_on_cache_hits(con, tmp_path):
    cache = ResponseCache(tmp_path / "cache")
    reviewer(tmp_path, cache=cache)(*db.load_case(con, "L03"))
    (path,) = list((tmp_path / "cache").iterdir())
    record = json.loads(path.read_text(encoding="utf-8"))
    ans = json.loads(record["answer"])
    ans["urgency_quotes"] = ["a quote that is not in the body"]
    record["answer"] = json.dumps(ans)
    path.write_text(json.dumps(record), encoding="utf-8")
    hit = reviewer(tmp_path, cache=cache)
    review = hit(*db.load_case(con, "L03"))
    assert hit.client.requests == [] and hit.stats.cache_hits == 1
    assert review.status == "NEEDS_HUMAN"


def test_no_cache_always_calls(con, tmp_path):
    r = reviewer(tmp_path, cache=ResponseCache(tmp_path / "cache", enabled=False))
    r(*db.load_case(con, "L02"))
    r(*db.load_case(con, "L02"))
    assert len(r.client.requests) == 2 and not (tmp_path / "cache").exists()


# --- audit ---------------------------------------------------------------------------

def test_audit_has_no_body_text_or_bank_numbers(con, tmp_path, raw_numbers):
    path = tmp_path / "audit.jsonl"
    r = reviewer(tmp_path, audit=AuditLog(path))
    cids = ["L01", "F06", "M04", "M05", "R10", "P04", "I03"]
    for cid in cids:
        r(*db.load_case(con, cid))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(cids)
    text = path.read_text(encoding="utf-8")
    for cid in cids:
        for num in raw_numbers[cid]:
            assert num not in text
        req, _ = db.load_case(con, cid)
        for line in req.body.splitlines():
            if len(line) > 25:
                assert line not in text, cid
    rec = json.loads(lines[0])
    assert rec["case_id"] == "L01" and rec["status"] == "OK" and rec["calls"] == 1
    assert rec["cost_usd"] == "0.003900"  # 1200 in x $2 + 150 out x $10 per million
    with pytest.raises(ValueError):
        AuditLog(None).write({"body": "x"})
