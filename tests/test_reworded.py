"""Held-out reworded set P01-P10: planted labels, unseen wording, honest baseline scores."""

from __future__ import annotations

import json
import logging
import re

import pytest
from fake_claude import FakeClient

from bankreview import config, evaluate, extractor, regex_extract
from bankreview.__main__ import main

KEYWORD_PATTERNS = (regex_extract.URGENCY_PATTERNS + regex_extract.SKIP_PATTERNS
                    + regex_extract.GREY_PATTERNS)


@pytest.fixture(scope="module")
def plan(data_dir):
    return json.loads((data_dir / config.REWORDED_PLAN_FILE_NAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bodies(data_dir):
    import csv
    with (data_dir / "csv" / "change_requests.csv").open(encoding="utf-8", newline="") as fh:
        return {r["request_id"]: r["body"] for r in csv.DictReader(fh)}


def test_plan_has_seven_fraud_three_legit(plan):
    cases = plan["cases"]
    assert plan["set"] == "reworded"
    assert [c["case_id"] for c in cases] == [f"P{i:02d}" for i in range(1, 11)]
    assert [c["is_fraud"] for c in cases] == ["Y"] * 7 + ["N"] * 3
    for c in cases:
        assert c["group"] == "REWORDED"
        if c["is_fraud"] == "Y":
            assert c["expected_risk_level"] == "HIGH" and c["expected_signals"]
        else:
            assert c["expected_risk_level"] == "LOW" and c["expected_signals"] == []
    by_id = {c["case_id"]: c for c in cases}
    assert by_id["P05"]["expected_signals"] == ["HOLDER_MISMATCH"]
    assert by_id["P05"]["expected_fields"]["account_holder_name"] == "Daniel Reyes"
    assert by_id["P03"]["expected_fields"]["new_bank_country"] == "IN"
    assert by_id["P08"]["expected_fields"]["new_bank_country"] == "IN"  # already IN: no change


def test_no_keyword_pattern_matches_the_planted_quotes(plan, bodies):
    for c in plan["cases"]:
        for quote in c["planted_quotes"]:
            assert quote in bodies[c["case_id"]], c["case_id"]
            for pat in KEYWORD_PATTERNS:
                assert not re.search(pat, quote, re.IGNORECASE), (c["case_id"], pat)
            assert not regex_extract.HOLDER_RE.search(quote), c["case_id"]
    assert all(c["planted_quotes"] for c in plan["cases"] if c["is_fraud"] == "Y")


def test_whole_p_bodies_are_keyword_free(plan, bodies):
    for c in plan["cases"]:
        f = regex_extract.extract(bodies[c["case_id"]])
        assert f.urgency_quotes == f.skip_verification_quotes == f.grey_quotes == ()
        assert f.account_holder_names == ()


def test_p05_is_plain_fraud_not_grey(bodies):
    body = bodies["P05"].lower()
    for word in ("subsidiar", "sister", "merg", "assign", "factor", "group", "affiliate"):
        assert word not in body


def test_bank_numbers_in_digit_form(bodies):
    from bankreview import generator
    raw = generator.build().raw_numbers
    for cid in (f"P{i:02d}" for i in range(1, 11)):
        squashed = re.sub(r"\s", "", bodies[cid])
        assert raw[cid] and all(n in squashed for n in raw[cid]), cid


def test_baselines_on_reworded_set(con, plan, tmp_path):
    quiet = lambda _: None  # noqa: E731
    for by in ("naive", "rules"):
        res = evaluate.run_eval(con, plan["cases"], by, progress=quiet)
        m = evaluate.metrics(res)
        assert m["fraud_recall"]["den"] == 7 and m["false_alarm_rate"]["den"] == 3
        path = evaluate.write_report(res, by, "reworded", tmp_path)
        assert path.name == f"eval_{by}_reworded.md"
        text = path.read_text(encoding="utf-8")
        assert "| P01 |" in text and "Fraud recall" in text
        print(f"{by} on reworded: fraud recall {evaluate.fmt_rate(m['fraud_recall'])}")


def test_cli_eval_reworded_and_run_claude(data_dir, tmp_path, capsys, monkeypatch):
    for by in ("naive", "rules"):
        assert main(["eval", "--by", by, "--set", "reworded", "--data-dir", str(data_dir),
                     "--report-dir", str(tmp_path)]) == 0
        assert (tmp_path / f"eval_{by}_reworded.md").exists()
    assert "fraud recall" in capsys.readouterr().out

    fake = FakeClient()
    monkeypatch.setattr(extractor, "make_client", lambda: fake)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "AUDIT_PATH", tmp_path / "logs" / "audit.jsonl")
    monkeypatch.setattr(config, "APP_LOG_PATH", tmp_path / "logs" / "bankreview.log")
    log = logging.getLogger("bankreview")
    before = list(log.handlers)
    try:
        args = ["--case", "P01", "--by", "claude", "--data-dir", str(data_dir), "--no-cache"]
        assert main(["run", *args]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["by"] == "claude" and out["confidence"] in {"HIGH", "MEDIUM", "LOW"}
        assert main(["eval", "--by", "claude", "--set", "reworded", "--limit", "2",
                     "--no-cache", "--data-dir", str(data_dir),
                     "--report-dir", str(tmp_path)]) == 0
        report = (tmp_path / "eval_claude_reworded.md").read_text(encoding="utf-8")
        assert "## Model run" in report and "Cache hits" in report
        assert len(fake.requests) == 3
        assert len((tmp_path / "logs" / "audit.jsonl").read_text().splitlines()) == 3
    finally:
        for h in list(log.handlers):
            if h not in before:
                log.removeHandler(h)
                h.close()
