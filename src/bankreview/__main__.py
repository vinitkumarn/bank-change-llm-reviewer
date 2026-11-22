"""Command line: python -m bankreview build"""

from __future__ import annotations

import argparse
from pathlib import Path

from bankreview import config, generator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bankreview")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="write the synthetic CSVs and both plan files")
    p_build.add_argument("--out-dir", type=Path, default=config.DATA_DIR)
    args = parser.parse_args(argv)

    if args.command == "build":
        paths = generator.write(args.out_dir)
        for path in paths:
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
