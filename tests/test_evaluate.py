"""Eval: labels from the plan files, metrics, report, CLI."""

from __future__ import annotations

import json

import pytest

from bankreview import config, evaluate
from bankreview.__main__ import main


def _labels(data_dir, name=config.PLAN_FILE_NAME):
    return evaluate.load_labels(data_dir / name)


def test_rules_beat_naive_and_no_legit_false_alarms(con, data_dir):
    labels = _labels(data_dir)
    quiet = lambda _: None  # noqa: E731
    naive = evaluate.metrics(evaluate.run_eval(con, labels, "naive", progress=quiet))
    rules = evaluate.metrics(evaluate.run_eval(con, labels, "rules", progress=quiet))
    assert rules["fraud_recall"]["rate"] > naive["fraud_recall"]["rate"]
    assert rules["false_alarm_rate"]["num"] == 0
    assert rules["false_alarm_rate"]["den"] == 15  # L cases only


def test_grey_high_cases_not_counted_as_false_alarms(con, data_dir):
    labels = _labels(data_dir)
    m = evaluate.metrics(evaluate.run_eval(con, labels, "rules", progress=lambda _: None))
    assert {"G02", "G03"} <= set(m["grey_ids"])
    assert m["grey_expected_high"]["den"] == len(m["grey_ids"])


def test_labels_come_from_plan_file(con, data_dir, tmp_path):
    plan = json.loads((data_dir / config.PLAN_FILE_NAME).read_text(encoding="utf-8"))
    for c in plan["cases"]:
        if c["case_id"] == "F01":
            c["expected_risk_level"] = "LOW"
    edited = tmp_path / "plan.json"
    edited.write_text(json.dumps(plan), encoding="utf-8")
    quiet = lambda _: None  # noqa: E731
    before = evaluate.run_eval(con, _labels(data_dir), "rules", cases=["F01"], progress=quiet)
    after = evaluate.run_eval(con, evaluate.load_labels(edited), "rules", cases=["F01"],
                              progress=quiet)
    assert before[0].risk_ok and not after[0].risk_ok


def test_cases_and_limit_and_progress(con, data_dir):
    lines: list[str] = []
    res = evaluate.run_eval(con, _labels(data_dir), "rules", cases=["F01", "L15", "G02"],
                            limit=2, progress=lines.append)
    assert [r.case_id for r in res] == ["L15", "F01"]  # plan order, then the limit
    assert len(lines) == 2 and lines[0].startswith("[1/2] ")


def test_redteam_report_is_separate(con, data_dir, tmp_path):
    res = evaluate.run_eval(con, _labels(data_dir, config.REDTEAM_PLAN_FILE_NAME), "rules",
                            progress=lambda _: None)
    assert all(r.case_id.startswith("R") for r in res)
    path = evaluate.write_report(res, "rules", "redteam", tmp_path)
    assert path.name == "eval_rules_redteam.md"
    text = path.read_text(encoding="utf-8")
    assert "n/a" in text  # no L cases in the red-team set
    assert "| R01 |" in text


def test_cli_run_and_eval(data_dir, tmp_path, capsys):
    assert main(["run", "--case", "F11", "--by", "rules", "--data-dir", str(data_dir)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["risk_level"] == "HIGH" and out["hold"] == "Y"
    assert out["dollars_at_risk"] == "28033.82"
    for by in ("naive", "rules"):
        assert main(["eval", "--by", by, "--data-dir", str(data_dir),
                     "--report-dir", str(tmp_path)]) == 0
        report = (tmp_path / f"eval_{by}.md").read_text(encoding="utf-8")
        assert "Fraud recall" in report and "| L15 |" in report
    assert main(["eval", "--by", "rules", "--set", "redteam", "--limit", "3",
                 "--data-dir", str(data_dir), "--report-dir", str(tmp_path)]) == 0
    assert (tmp_path / "eval_rules_redteam.md").exists()
    assert "[3/3]" in capsys.readouterr().out


@pytest.mark.parametrize("by", ["naive", "rules"])
def test_each_set_writes_its_own_report(data_dir, tmp_path, by):
    """main, redteam and reworded each get their own file; none overwrites another."""
    expected = {"main": f"eval_{by}.md", "redteam": f"eval_{by}_redteam.md",
                "reworded": f"eval_{by}_reworded.md"}
    first_case = {"main": "| L01 |", "redteam": "| R01 |", "reworded": "| P01 |"}
    for which in expected:
        assert main(["eval", "--by", by, "--set", which, "--data-dir", str(data_dir),
                     "--report-dir", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.glob("*.md")) == sorted(expected.values())
    for which, name in expected.items():
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert f"# Eval: {by} on the {which} set" in text
        assert first_case[which] in text
        others = [c for w, c in first_case.items() if w != which]
        assert not any(c in text for c in others)
