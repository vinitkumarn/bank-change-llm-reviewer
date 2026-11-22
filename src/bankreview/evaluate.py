"""Score a reviewer against the PLANTED labels (plan.json / redteam_plan.json).

Expected values come only from the plan files, never from the rules code. The red-team set
is reported separately and is never used to tune anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

import duckdb

from bankreview import baselines, config
from bankreview.rules import Review


@dataclass
class CaseResult:
    label: dict
    review: Review

    @property
    def case_id(self) -> str:
        return self.label["case_id"]

    @property
    def risk_ok(self) -> bool:
        return self.review.risk_level == self.label["expected_risk_level"]

    @property
    def missed_signals(self) -> list[str]:
        return sorted(set(self.label["expected_signals"]) - set(self.review.signal_codes))

    @property
    def ok(self) -> bool:
        return (self.risk_ok and not self.missed_signals
                and self.review.hold == self.label["expected_hold"]
                and self.review.status == self.label["expected_status"])


def load_labels(path: Path | str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def run_eval(con: duckdb.DuckDBPyConnection, labels: list[dict], by: str,
             cases: list[str] | None = None, limit: int | None = None,
             progress: Callable[[str], None] = print) -> list[CaseResult]:
    chosen = [c for c in labels if not cases or c["case_id"] in cases]
    if limit is not None:
        chosen = chosen[:limit]
    results = []
    for i, label in enumerate(chosen, 1):
        review = baselines.run_case(con, label["case_id"], by)
        res = CaseResult(label, review)
        results.append(res)
        progress(f"[{i}/{len(chosen)}] {res.case_id} got {review.risk_level} expected "
                 f"{label['expected_risk_level']} {'ok' if res.ok else 'MISS'}")
    return results


def _rate(num: int, den: int) -> dict:
    return {"num": num, "den": den, "rate": (num / den) if den else None}


def metrics(results: list[CaseResult]) -> dict:
    fraud = [r for r in results if r.label["is_fraud"] == "Y"]
    caught = [r for r in fraud if r.review.risk_level == "HIGH"]
    legit = [r for r in results if r.label["group"] == "LEGIT"]
    grey_high = [r for r in results
                 if r.label["group"] == "GREY" and r.label["expected_risk_level"] == "HIGH"]
    expected_sigs = sum(len(r.label["expected_signals"]) for r in results)
    missed_sigs = sum(len(r.missed_signals) for r in results)
    return {
        "fraud_recall": _rate(len(caught), len(fraud)),
        "false_alarm_rate": _rate(sum(r.review.risk_level == "HIGH" for r in legit), len(legit)),
        "grey_expected_high": _rate(sum(r.review.risk_level == "HIGH" for r in grey_high),
                                    len(grey_high)),
        "risk_accuracy": _rate(sum(r.risk_ok for r in results), len(results)),
        "signal_recall": _rate(expected_sigs - missed_sigs, expected_sigs),
        "hold_accuracy": _rate(sum(r.review.hold == r.label["expected_hold"] for r in results),
                               len(results)),
        "status_accuracy": _rate(
            sum(r.review.status == r.label["expected_status"] for r in results), len(results)),
        "dollars_match_label": _rate(
            sum(r.review.dollars_at_risk == Decimal(r.label["expected_dollars_at_risk"])
                for r in results), len(results)),
        "dollars_on_caught_fraud": sum((r.review.dollars_at_risk for r in caught),
                                       Decimal("0.00")),
        "dollars_on_all_fraud": sum((r.review.dollars_at_risk for r in fraud), Decimal("0.00")),
        "missed_fraud": [r.case_id for r in fraud if r.review.risk_level != "HIGH"],
        "grey_ids": [r.case_id for r in grey_high],
    }


def fmt_rate(m: dict) -> str:
    if m["rate"] is None:
        return "n/a (no cases)"
    return f"{m['rate']:.1%} ({m['num']}/{m['den']})"


def _usd(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def report_path(report_dir: Path | str, by: str, which: str) -> Path:
    suffix = "" if which == "main" else f"_{which}"
    return Path(report_dir) / f"eval_{by}{suffix}.md"


def write_report(results: list[CaseResult], by: str, which: str,
                 report_dir: Path | str = config.REPORTS_DIR) -> Path:
    m = metrics(results)
    grey = ", ".join(m["grey_ids"]) or "none"
    lines = [
        f"# Eval: {by} on the {which} set",
        "",
        f"Reference date {config.REFERENCE_DATE.isoformat()}, exposure window "
        f"{config.EXPOSURE_DAYS} days, {len(results)} cases. Labels from the planted plan file.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fraud recall (fraud flagged HIGH) | {fmt_rate(m['fraud_recall'])} |",
        f"| False-alarm rate (L cases flagged HIGH) | {fmt_rate(m['false_alarm_rate'])} |",
        f"| Grey cases labelled HIGH ({grey}) flagged HIGH | "
        f"{fmt_rate(m['grey_expected_high'])} |",
        f"| Risk-level accuracy | {fmt_rate(m['risk_accuracy'])} |",
        f"| Signal recall | {fmt_rate(m['signal_recall'])} |",
        f"| Hold accuracy | {fmt_rate(m['hold_accuracy'])} |",
        f"| Status accuracy | {fmt_rate(m['status_accuracy'])} |",
        f"| Dollars at risk matches label | {fmt_rate(m['dollars_match_label'])} |",
        f"| Dollars at risk on caught fraud | {_usd(m['dollars_on_caught_fraud'])} of "
        f"{_usd(m['dollars_on_all_fraud'])} |",
        f"| Missed fraud cases | {', '.join(m['missed_fraud']) or 'none'} |",
        "",
        "## Per case",
        "",
        "| Case | Group | Fraud | Expected | Got | Missed signals | Extra signals | Hold exp/got "
        "| Status exp/got | Dollars at risk | Result |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lb, rv = r.label, r.review
        extra = sorted(set(rv.signal_codes) - set(lb["expected_signals"]))
        lines.append(
            f"| {r.case_id} | {lb['group']} | {lb['is_fraud']} | {lb['expected_risk_level']} | "
            f"{rv.risk_level} | {', '.join(r.missed_signals) or '-'} | {', '.join(extra) or '-'} "
            f"| {lb['expected_hold']}/{rv.hold} | {lb['expected_status']}/{rv.status} | "
            f"{_usd(rv.dollars_at_risk)} | {'ok' if r.ok else 'MISS'} |")
    path = report_path(report_dir, by, which)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
