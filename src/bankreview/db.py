"""Load the CSVs into a local DuckDB file with real types, and read one case back.

Money is DECIMAL(18,2) and dates are DATE, so Python gets Decimal and datetime.date back
(never float, never text).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from bankreview import config
from bankreview.rules import BankAccount, Contact, Master, Request, Schedule

COLUMNS: dict[str, dict[str, str]] = {
    "suppliers": {"vendor_id": "INTEGER", "vendor_name": "VARCHAR", "segment1": "VARCHAR",
                  "country": "VARCHAR"},
    "supplier_sites": {"vendor_site_id": "INTEGER", "vendor_id": "INTEGER",
                       "site_code": "VARCHAR", "country": "VARCHAR",
                       "email_domain_on_file": "VARCHAR", "last_update_date": "DATE"},
    "supplier_contacts": {"vendor_id": "INTEGER", "contact_name": "VARCHAR", "email": "VARCHAR",
                          "phone_on_file": "VARCHAR", "last_update_date": "DATE"},
    "bank_accounts": {"account_token": "VARCHAR", "last4": "VARCHAR",
                      "account_num_hash": "VARCHAR", "bank_name": "VARCHAR",
                      "country": "VARCHAR", "account_holder_name": "VARCHAR",
                      "vendor_id": "INTEGER", "vendor_site_id": "INTEGER",
                      "start_date": "DATE", "is_active": "VARCHAR"},
    "payment_schedules": {"vendor_id": "INTEGER", "invoice_num": "VARCHAR",
                          "amount_remaining": "DECIMAL(18,2)", "due_date": "DATE"},
    "change_requests": {"request_id": "VARCHAR", "vendor_id": "INTEGER", "channel": "VARCHAR",
                        "from_address": "VARCHAR", "reply_to": "VARCHAR",
                        "received_at": "TIMESTAMP", "already_applied": "VARCHAR",
                        "body": "VARCHAR"},
}


def db_path(data_dir: Path | str = config.DATA_DIR) -> Path:
    return Path(data_dir) / config.DB_PATH.name


def load(csv_dir: Path | str, path: Path | str) -> Path:
    """(Re)create every table from its CSV with explicit column types."""
    csv_dir, path = Path(csv_dir), Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        for table, cols in COLUMNS.items():
            csv_path = (csv_dir / f"{table}.csv").as_posix()
            con.execute(
                f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv(?, header=true, "
                f"auto_detect=false, quote='\"', escape='\"', delim=',', "
                f"columns={_struct(cols)})", [csv_path])
    return path


def _struct(cols: dict[str, str]) -> str:
    return "{" + ", ".join(f"'{k}': '{v}'" for k, v in cols.items()) + "}"


def connect(path: Path | str, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run: python -m bankreview build")
    return duckdb.connect(str(path), read_only=read_only)


def load_case(con: duckdb.DuckDBPyConnection, request_id: str) -> tuple[Request, Master]:
    row = con.execute(
        "SELECT request_id, vendor_id, channel, from_address, reply_to, already_applied, body "
        "FROM change_requests WHERE request_id = ?", [request_id]).fetchone()
    if row is None:
        raise KeyError(f"no change request {request_id}")
    req = Request(row[0], row[1], row[2], row[3] or "", row[4] or "", row[5], row[6] or "")
    vid = req.vendor_id
    sup = con.execute(
        "SELECT s.vendor_name, s.country, t.email_domain_on_file, t.last_update_date "
        "FROM suppliers s JOIN supplier_sites t USING (vendor_id) WHERE s.vendor_id = ?",
        [vid]).fetchone()
    contacts = tuple(Contact(*r) for r in con.execute(
        "SELECT email, phone_on_file, last_update_date FROM supplier_contacts "
        "WHERE vendor_id = ? ORDER BY email", [vid]).fetchall())
    accounts = tuple(BankAccount(*r) for r in con.execute(
        "SELECT account_token, last4, account_num_hash, vendor_id, is_active "
        "FROM bank_accounts ORDER BY account_token").fetchall())
    schedules = tuple(Schedule(*r) for r in con.execute(
        "SELECT invoice_num, amount_remaining, due_date FROM payment_schedules "
        "WHERE vendor_id = ? ORDER BY invoice_num", [vid]).fetchall())
    master = Master(vid, sup[0], sup[1], sup[2], sup[3], contacts, accounts, schedules)
    return req, master
