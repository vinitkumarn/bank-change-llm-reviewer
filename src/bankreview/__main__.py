"""Command line.

python -m bankreview build
python -m bankreview run --case F01 --by rules
python -m bankreview eval --by rules [--set main|redteam] [--cases F01 F02] [--limit N]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bankreview import baselines, config, db, evaluate, generator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bankreview")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="write the CSVs and plans, then load DuckDB")
    p_build.add_argument("--out-dir", type=Path, default=config.DATA_DIR)

    p_run = sub.add_parser("run", help="review one case and print the result")
    p_run.add_argument("--case", required=True)
    p_run.add_argument("--by", choices=sorted(baselines.BASELINES), default="rules")
    p_run.add_argument("--data-dir", type=Path, default=config.DATA_DIR)

    p_eval = sub.add_parser("eval", help="score a reviewer against the planted labels")
    p_eval.add_argument("--by", choices=sorted(baselines.BASELINES), default="rules")
    p_eval.add_argument("--set", dest="which", choices=["main", "redteam"], default="main")
    p_eval.add_argument("--cases", nargs="+")
    p_eval.add_argument("--limit", type=int)
    p_eval.add_argument("--data-dir", type=Path, default=config.DATA_DIR)
    p_eval.add_argument("--report-dir", type=Path, default=config.REPORTS_DIR)

    args = parser.parse_args(argv)

    if args.command == "build":
        paths = generator.write(args.out_dir)
        paths.append(db.load(args.out_dir / config.CSV_DIR_NAME, db.db_path(args.out_dir)))
        for path in paths:
            print(f"wrote {path}")
        return 0

    with db.connect(db.db_path(args.data_dir)) as con:
        if args.command == "run":
            review = baselines.run_case(con, args.case, args.by)
            print(json.dumps(review.to_dict(), indent=2))
            return 0

        plan_name = config.PLAN_FILE_NAME if args.which == "main" else config.REDTEAM_PLAN_FILE_NAME
        labels = evaluate.load_labels(args.data_dir / plan_name)
        results = evaluate.run_eval(con, labels, args.by, cases=args.cases, limit=args.limit)
    path = evaluate.write_report(results, args.by, args.which, args.report_dir)
    m = evaluate.metrics(results)
    print(f"fraud recall {evaluate.fmt_rate(m['fraud_recall'])}; "
          f"false alarms {evaluate.fmt_rate(m['false_alarm_rate'])}; "
          f"risk accuracy {evaluate.fmt_rate(m['risk_accuracy'])}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
