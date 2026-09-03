"""
expenses_saas.py — Tenant-aware Expense Management module.

Ports app.py's render_expense_management() — Add Expense, View/Delete
Expenses (date-range filtered) — onto the tenant-aware PostgreSQL layer.

Expenses aren't referenced by any other table (unlike products/suppliers/
customers), so unlike those modules there's no "safe delete vs deactivate"
concern here — a direct DELETE is correct, matching the original app.
"""

import pandas as pd
from datetime import date
from sqlalchemy import text

from database_saas import get_engine


def add_expense(tenant_id: str, expense_date: date, expense_type: str, amount: float, remarks: str = ""):
    if not expense_type or not expense_type.strip():
        return False, "Expense type is required."
    if amount is None or amount <= 0:
        return False, "Amount must be greater than zero."

    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO expenses (tenant_id, expense_date, expense_type, amount, remarks)
                    VALUES (:tid, :d, :type, :amt, :remarks) RETURNING id
                """),
                {"tid": tenant_id, "d": expense_date, "type": expense_type.strip(),
                 "amt": amount, "remarks": remarks},
            ).fetchone()
        return True, str(row[0])
    except Exception as e:
        return False, f"Could not save expense: {e}"


def get_expenses(tenant_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT id, expense_date, expense_type, amount, remarks
            FROM expenses WHERE tenant_id = :tid AND expense_date BETWEEN :sd AND :ed
            ORDER BY expense_date DESC
        """),
        engine, params={"tid": tenant_id, "sd": start_date, "ed": end_date}
    )


def delete_expense(tenant_id: str, expense_id: str):
    """Tenant-scoped delete — even if an attacker somehow knew another
    tenant's expense id, the WHERE tenant_id = :tid means the DELETE
    simply matches zero rows rather than deleting the wrong tenant's data."""
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM expenses WHERE tenant_id = :tid AND id = :eid"),
            {"tid": tenant_id, "eid": expense_id},
        )
    if result.rowcount == 0:
        return False, "Expense not found (or belongs to a different shop)."
    return True, "Expense deleted."
