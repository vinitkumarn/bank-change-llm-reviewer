"""Shared fixtures: one generated data set + DuckDB file per test session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bankreview import config, db, generator


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pr2data")
    generator.write(out)
    db.load(out / config.CSV_DIR_NAME, db.db_path(out))
    return out


@pytest.fixture()
def con(data_dir):
    with db.connect(db.db_path(data_dir)) as c:
        yield c


@pytest.fixture(scope="session")
def labels(data_dir) -> dict[str, dict]:
    out = {}
    for name in config.PLAN_FILES.values():
        for c in json.loads((data_dir / name).read_text(encoding="utf-8"))["cases"]:
            out[c["case_id"]] = c
    return out
