"""Command line.

python -m bankreview build
python -m bankreview run --case F01 --by rules|naive|claude [--no-cache]
python -m bankreview eval --by rules [--set main|redteam|reworded] [--cases F01 F02] [--limit N]
                          [--no-cache]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from bankreview import baselines, config, db, evaluate, extractor, generator
from bankreview.audit import AuditLog
from bankreview.cache import ResponseCache
from bankreview.claude_reviewer import ClaudeReviewer

BY_CHOICES = sorted(baselines.BASELINES) + ["claude"]


def _setup_logging() -> None:
    """Full error messages (API errors, validation errors) go to logs/bankreview.log."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("bankreview")
    if not log.handlers:
        handler = logging.FileHandler(config.APP_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)


def _claude(data_dir: Path, no_cache: bool) -> ClaudeReviewer:
    _setup_logging()
    return ClaudeReviewer(
        client=extractor.make_client(),
        cache=ResponseCache(data_dir / config.CACHE_DIR_NAME, enabled=not no_cache),
        audit=AuditLog(config.AUDIT_PATH))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bankreview")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="write the CSVs and plans, then load DuckDB")
    p_build.add_argument("--out-dir", type=Path, default=config.DATA_DIR)

    p_run = sub.add_parser("run", help="review one case and print the result")
    p_run.add_argument("--case", required=True)
    p_run.add_argument("--by", choices=BY_CHOICES, default="rules")
    p_run.add_argument("--data-dir", type=Path, default=config.DATA_DIR)
    p_run.add_argument("--no-cache", action="store_true", help="force fresh Claude calls")

    p_eval = sub.add_parser("eval", help="score a reviewer against the planted labels")
    p_eval.add_argument("--by", choices=BY_CHOICES, default="rules")
    p_eval.add_argument("--set", dest="which", choices=list(config.PLAN_FILES), default="main")
    p_eval.add_argument("--cases", nargs="+")
    p_eval.add_argument("--limit", type=int)
    p_eval.add_argument("--data-dir", type=Path, default=config.DATA_DIR)
    p_eval.add_argument("--report-dir", type=Path, default=config.REPORTS_DIR)
    p_eval.add_argument("--no-cache", action="store_true", help="force fresh Claude calls")

    args = parser.parse_args(argv)

    if args.command == "build":
        paths = generator.write(args.out_dir)
        paths.append(db.load(args.out_dir / config.CSV_DIR_NAME, db.db_path(args.out_dir)))
        for path in paths:
            print(f"wrote {path}")
        return 0

    reviewer = _claude(args.data_dir, args.no_cache) if args.by == "claude" else None
    with db.connect(db.db_path(args.data_dir)) as con:
        if args.command == "run":
            req, master = db.load_case(con, args.case)
            review = (reviewer or baselines.BASELINES[args.by])(req, master)
            print(json.dumps(review.to_dict(), indent=2))
            return 0

        labels = evaluate.load_labels(args.data_dir / config.PLAN_FILES[args.which])
        results = evaluate.run_eval(con, labels, args.by, cases=args.cases, limit=args.limit,
                                    reviewer=reviewer)
    extra = reviewer.stats.lines(reviewer.model) if reviewer else None
    path = evaluate.write_report(results, args.by, args.which, args.report_dir, extra)
    m = evaluate.metrics(results)
    print(f"fraud recall {evaluate.fmt_rate(m['fraud_recall'])}; "
          f"false alarms {evaluate.fmt_rate(m['false_alarm_rate'])}; "
          f"risk accuracy {evaluate.fmt_rate(m['risk_accuracy'])}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
