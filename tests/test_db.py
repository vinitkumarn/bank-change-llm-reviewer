"""DuckDB load: real types, exact money, bodies intact."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from bankreview import db, generator


def _types(con, table: str) -> dict[str, str]:
    return {r[0]: r[1] for r in con.execute(f"DESCRIBE {table}").fetchall()}


def test_column_types(con):
    assert _types(con, "payment_schedules")["amount_remaining"] == "DECIMAL(18,2)"
    assert _types(con, "payment_schedules")["due_date"] == "DATE"
    assert _types(con, "supplier_contacts")["last_update_date"] == "DATE"
    assert _types(con, "supplier_sites")["last_update_date"] == "DATE"
    assert _types(con, "bank_accounts")["start_date"] == "DATE"
    assert _types(con, "change_requests")["received_at"] == "TIMESTAMP"
    assert _types(con, "suppliers")["vendor_id"] == "INTEGER"


def test_python_values_are_decimal_and_date(con):
    amount, due = con.execute(
        "SELECT amount_remaining, due_date FROM payment_schedules LIMIT 1").fetchone()
    assert isinstance(amount, Decimal) and isinstance(due, date)


def test_row_counts_and_bodies_intact(con):
    built = generator.build()
    for table, rows in built.tables.items():
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == len(rows), table
    bodies = {r["request_id"]: r["body"] for r in built.tables["change_requests"]}
    for rid, body in con.execute("SELECT request_id, body FROM change_requests").fetchall():
        assert body == bodies[rid], rid


def test_f11_dollars_at_risk_in_sql(con, labels):
    vid = labels["F11"]["vendor_id"]
    total = con.execute(
        "SELECT sum(amount_remaining) FROM payment_schedules WHERE vendor_id = ? "
        "AND amount_remaining > 0 AND due_date <= DATE '2026-11-16'", [vid]).fetchone()[0]
    assert total == Decimal("28033.82")


def test_load_case(con):
    req, master = db.load_case(con, "F11")
    assert req.already_applied == "Y"
    assert master.country == "IN"
    assert master.contacts and master.schedules
    assert all(isinstance(s.amount_remaining, Decimal) for s in master.schedules)
