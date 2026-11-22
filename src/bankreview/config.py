"""Project settings in one place. Money is Decimal, never float."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

try:  # .env is optional; a missing file or package must not break anything.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

# --- Paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CSV_DIR_NAME = "csv"
CSV_DIR = DATA_DIR / CSV_DIR_NAME
PLAN_FILE_NAME = "plan.json"
REDTEAM_PLAN_FILE_NAME = "redteam_plan.json"
PLAN_PATH = DATA_DIR / PLAN_FILE_NAME
REDTEAM_PLAN_PATH = DATA_DIR / REDTEAM_PLAN_FILE_NAME
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "bankreview.duckdb"
LOG_DIR = ROOT / "logs"
REPORTS_DIR = ROOT / "reports"

if load_dotenv is not None and (ROOT / ".env").exists():
    load_dotenv(ROOT / ".env")

# --- Data set --------------------------------------------------------------
REFERENCE_DATE = date(2026, 11, 2)
# Dollars at risk = amount_remaining due on or before REFERENCE_DATE + EXPOSURE_DAYS
# (overdue unpaid items count too). Treasury owns this number.
EXPOSURE_DAYS = 14
EXPOSURE_END_DATE = REFERENCE_DATE + timedelta(days=EXPOSURE_DAYS)
RECENT_CHANGE_DAYS = 30  # RECENT_MASTER_CHANGE window
SEED = 20261102
CURRENCY = "USD"
MONEY_QUANT = Decimal("0.01")
SUPPLIER_COUNTRIES = ("US", "CA", "GB", "DE", "IN")

FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.in",
        "yahoo.co.uk",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "aol.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "gmx.de",
        "web.de",
        "mail.com",
        "yandex.com",
        "rediffmail.com",
    }
)

# Signal codes and their levels, exactly as in docs/design.md.
SIGNAL_LEVELS = {
    "DOMAIN_MISMATCH": "HIGH",
    "LOOKALIKE_DOMAIN": "HIGH",
    "COUNTRY_CHANGE": "HIGH",
    "SHARED_ACCOUNT": "HIGH",
    "HOLDER_MISMATCH": "HIGH",
    "SKIP_VERIFICATION": "HIGH",
    "URGENCY": "MEDIUM",
    "RECENT_MASTER_CHANGE": "MEDIUM",
    "NEW_CONTACT": "MEDIUM",
}
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")

# --- Model -----------------------------------------------------------------
MODEL = os.environ.get("BANKREVIEW_MODEL") or "claude-sonnet-5-5"
MAX_TOKENS = 4000
PROMPT_VERSION = "v1"
