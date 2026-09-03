"""
suppliers_saas.py — Tenant-aware Supplier Management + Supplier Ledger module.

Ports app.py's render_supplier_management() — Add/Edit/Delete Supplier,
safe-delete-vs-deactivate, and the Supplier Ledger (Cash/UPI payment
tracking, per-supplier balance, payment history) onto the tenant-aware
PostgreSQL layer.
"""

import pandas as pd
from sqlalchemy import text

from database_saas import get_engine, read_sql_df


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------
def add_supplier(tenant_id: str, name: str, mobile: str = "", address: str = "", gst_number: str = ""):
    if not name or not name.strip():
        return False, "Supplier Name is required."
    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO suppliers (tenant_id, name, mobile, address, gst_number, is_active)
                    VALUES (:tid, :name, :mobile, :address, :gst, TRUE) RETURNING id
                """),
                {"tid": tenant_id, "name": name.strip(), "mobile": mobile, "address": address, "gst": gst_number},
            ).fetchone()
        return True, str(row[0])
    except Exception as e:
        return False, f"Could not save supplier: {e}"


def update_supplier(tenant_id: str, supplier_id: str, name: str, mobile: str,
                     address: str, gst_number: str, is_active: bool):
    if not name or not name.strip():
        return False, "Supplier Name is required."
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE suppliers SET name=:name, mobile=:mobile, address=:address,
                       gst_number=:gst, is_active=:active
                WHERE tenant_id = :tid AND id = :sid
            """),
            {"name": name.strip(), "mobile": mobile, "address": address, "gst": gst_number,
             "active": is_active, "tid": tenant_id, "sid": supplier_id},
        )
        if result.rowcount == 0:
            return False, "Supplier not found (or belongs to a different shop)."
    return True, "Supplier updated successfully!"


def get_all_suppliers(tenant_id: str) -> pd.DataFrame:
    engine = get_engine()
    return read_sql_df(
        text("SELECT * FROM suppliers WHERE tenant_id = :tid ORDER BY name"),
        engine, params={"tid": tenant_id}
    )


def get_active_suppliers(tenant_id: str) -> pd.DataFrame:
    engine = get_engine()
    return read_sql_df(
        text("SELECT * FROM suppliers WHERE tenant_id = :tid AND is_active = TRUE ORDER BY name"),
        engine, params={"tid": tenant_id}
    )


def check_supplier_in_use(tenant_id: str, supplier_id: str) -> int:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM purchases WHERE tenant_id = :tid AND supplier_id = :sid"),
            {"tid": tenant_id, "sid": supplier_id},
        ).scalar()


def delete_or_deactivate_supplier(tenant_id: str, supplier_id: str):
    """Same safety pattern as products: only hard-delete if the supplier
    has no purchase history in this tenant."""
    purchase_count = check_supplier_in_use(tenant_id, supplier_id)
    engine = get_engine()

    if purchase_count > 0:
        with engine.begin() as conn:
            result = conn.execute(
                text("UPDATE suppliers SET is_active = FALSE WHERE tenant_id = :tid AND id = :sid"),
                {"tid": tenant_id, "sid": supplier_id},
            )
        if result.rowcount == 0:
            return "not_found", "Supplier not found."
        return "deactivated", f"Supplier has {purchase_count} purchase record(s) — deactivated instead of deleted."
    else:
        with engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM suppliers WHERE tenant_id = :tid AND id = :sid"),
                {"tid": tenant_id, "sid": supplier_id},
            )
        if result.rowcount == 0:
            return "not_found", "Supplier not found."
        return "deleted", "Supplier permanently deleted."


# ---------------------------------------------------------------------------
# Supplier Ledger — Cash/UPI payment tracking
# ---------------------------------------------------------------------------
def get_ledger_summary(tenant_id: str) -> pd.DataFrame:
    """All-suppliers overview: Total Purchased, Total Paid, Balance Due."""
    engine = get_engine()
    return read_sql_df(
        text("""
            SELECT s.id, s.name,
                   COALESCE(SUM(pu.total_amount), 0) AS total_purchased,
                   COALESCE(SUM(pu.paid_amount), 0) AS total_paid,
                   COALESCE(SUM(pu.total_amount - pu.paid_amount), 0) AS balance_due
            FROM suppliers s
            LEFT JOIN purchases pu ON pu.supplier_id = s.id AND pu.tenant_id = s.tenant_id
            WHERE s.tenant_id = :tid
            GROUP BY s.id, s.name
            ORDER BY balance_due DESC
        """),
        engine, params={"tid": tenant_id}
    )


def get_supplier_transactions(tenant_id: str, supplier_id: str) -> pd.DataFrame:
    """Full purchase history for one supplier, with computed balance/status."""
    engine = get_engine()
    df = read_sql_df(
        text("""
            SELECT pu.id, pu.purchase_date, p.name AS product, pu.quantity,
                   pu.total_amount, pu.paid_amount,
                   (pu.total_amount - pu.paid_amount) AS balance
            FROM purchases pu
            LEFT JOIN products p ON p.id = pu.product_id AND p.tenant_id = pu.tenant_id
            WHERE pu.tenant_id = :tid AND pu.supplier_id = :sid
            ORDER BY pu.purchase_date DESC
        """),
        engine, params={"tid": tenant_id, "sid": supplier_id}
    )
    if not df.empty:
        df["status"] = df["balance"].apply(lambda b: "Paid" if b <= 0.005 else "Pending/Partial")
    return df


def record_supplier_payment(tenant_id: str, purchase_id: str, supplier_id: str,
                             cash_amount: float, upi_amount: float):
    """Records a Cash/UPI split payment against a specific pending
    purchase — updates purchases.paid_amount and inserts a
    supplier_payments row (this is what powers the date-wise Cash/UPI
    report). Rejects overpayment beyond the actual balance due."""
    pay_amount = round((cash_amount or 0) + (upi_amount or 0), 2)
    if pay_amount <= 0:
        return False, "Cash ya UPI me se kam se kam ek amount daalein."

    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT total_amount, paid_amount FROM purchases
                    WHERE tenant_id = :tid AND id = :pid AND supplier_id = :sid
                    FOR UPDATE
                """),
                {"tid": tenant_id, "pid": purchase_id, "sid": supplier_id},
            ).fetchone()
            if not row:
                return False, "Purchase not found (or belongs to a different shop/supplier)."

            balance = float(row[0]) - float(row[1])
            if pay_amount > balance + 0.01:
                return False, f"Payment (₹{pay_amount:.2f}) balance (₹{balance:.2f}) se zyada hai."

            conn.execute(
                text("UPDATE purchases SET paid_amount = paid_amount + :amt WHERE tenant_id = :tid AND id = :pid"),
                {"amt": pay_amount, "tid": tenant_id, "pid": purchase_id},
            )
            conn.execute(
                text("""
                    INSERT INTO supplier_payments (tenant_id, purchase_id, supplier_id, payment_date,
                                                     cash_amount, upi_amount, total_amount)
                    VALUES (:tid, :pid, :sid, CURRENT_DATE, :cash, :upi, :total)
                """),
                {"tid": tenant_id, "pid": purchase_id, "sid": supplier_id,
                 "cash": cash_amount or 0, "upi": upi_amount or 0, "total": pay_amount},
            )
        return True, f"Payment of ₹{pay_amount:.2f} recorded."
    except Exception as e:
        return False, f"Could not record payment: {e}"


def get_payment_history(tenant_id: str, supplier_id: str = None) -> pd.DataFrame:
    """Date-wise Cash/UPI payment history — for ONE supplier if given,
    else all suppliers combined (Reports Hub view)."""
    engine = get_engine()
    if supplier_id:
        return read_sql_df(
            text("""
                SELECT payment_date, cash_amount, upi_amount, total_amount
                FROM supplier_payments WHERE tenant_id = :tid AND supplier_id = :sid
                ORDER BY payment_date DESC
            """),
            engine, params={"tid": tenant_id, "sid": supplier_id}
        )
    return read_sql_df(
        text("""
            SELECT sp.payment_date, s.name AS supplier, sp.cash_amount, sp.upi_amount, sp.total_amount
            FROM supplier_payments sp
            LEFT JOIN suppliers s ON s.id = sp.supplier_id AND s.tenant_id = sp.tenant_id
            WHERE sp.tenant_id = :tid
            ORDER BY sp.payment_date DESC
        """),
        engine, params={"tid": tenant_id}
    )
