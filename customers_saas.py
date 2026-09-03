"""
customers_saas.py — Tenant-aware Customer Management module.

Ports app.py's render_customer_management() — Add/Edit/Delete Customer,
safe-delete-vs-deactivate (checked against sales history), and Purchase
History lookup by mobile — onto the tenant-aware PostgreSQL layer.

Note: customers.mobile is UNIQUE(tenant_id, mobile) in the new schema —
two different tenants WILL legitimately share a customer's phone number
in the real world, which the old global UNIQUE(mobile) could never allow.
"""

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from database_saas import get_engine, read_sql_df


def add_customer(tenant_id: str, name: str, mobile: str = "", address: str = ""):
    if not name or not name.strip():
        return False, "Customer Name is required."
    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO customers (tenant_id, name, mobile, address, is_active)
                    VALUES (:tid, :name, :mobile, :address, TRUE) RETURNING id
                """),
                {"tid": tenant_id, "name": name.strip(), "mobile": mobile or None, "address": address},
            ).fetchone()
        return True, str(row[0])
    except IntegrityError:
        return False, "Ye mobile number pehle se ek customer ke naam hai (isi shop me)."
    except Exception as e:
        return False, f"Could not save customer: {e}"


def update_customer(tenant_id: str, customer_id: str, name: str, mobile: str,
                     address: str, is_active: bool):
    if not name or not name.strip():
        return False, "Customer Name is required."
    engine = get_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE customers SET name=:name, mobile=:mobile, address=:address, is_active=:active
                    WHERE tenant_id = :tid AND id = :cid
                """),
                {"name": name.strip(), "mobile": mobile or None, "address": address,
                 "active": is_active, "tid": tenant_id, "cid": customer_id},
            )
            if result.rowcount == 0:
                return False, "Customer not found (or belongs to a different shop)."
        return True, "Customer updated successfully!"
    except IntegrityError:
        return False, "Ye mobile number pehle se ek doosre customer ke naam hai (isi shop me)."
    except Exception as e:
        return False, f"Could not update customer: {e}"


def get_all_customers(tenant_id: str) -> pd.DataFrame:
    engine = get_engine()
    return read_sql_df(
        text("SELECT * FROM customers WHERE tenant_id = :tid ORDER BY name"),
        engine, params={"tid": tenant_id}
    )


def get_active_customers(tenant_id: str) -> pd.DataFrame:
    engine = get_engine()
    return read_sql_df(
        text("SELECT * FROM customers WHERE tenant_id = :tid AND is_active = TRUE ORDER BY name"),
        engine, params={"tid": tenant_id}
    )


def check_customer_in_use(tenant_id: str, mobile: str) -> int:
    """Mirrors the original app's check: does this mobile number have any
    sales recorded against it in THIS tenant?"""
    if not mobile:
        return 0
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM sales WHERE tenant_id = :tid AND customer_mobile = :mob"),
            {"tid": tenant_id, "mob": mobile},
        ).scalar()


def delete_or_deactivate_customer(tenant_id: str, customer_id: str):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT mobile FROM customers WHERE tenant_id = :tid AND id = :cid"),
            {"tid": tenant_id, "cid": customer_id},
        ).fetchone()
    if not row:
        return "not_found", "Customer not found."

    mobile = row[0]
    bill_count = check_customer_in_use(tenant_id, mobile)

    if bill_count > 0:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE customers SET is_active = FALSE WHERE tenant_id = :tid AND id = :cid"),
                {"tid": tenant_id, "cid": customer_id},
            )
        return "deactivated", f"Customer has {bill_count} bill(s) on record — deactivated instead of deleted."
    else:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM customers WHERE tenant_id = :tid AND id = :cid"),
                {"tid": tenant_id, "cid": customer_id},
            )
        return "deleted", "Customer permanently deleted."


def get_purchase_history(tenant_id: str, mobile: str) -> pd.DataFrame:
    """Mobile-wise purchase history — tenant-scoped, so searching a mobile
    number can never surface another shop's bills for that same number."""
    engine = get_engine()
    return read_sql_df(
        text("""
            SELECT bill_number, bill_date, payment_mode, subtotal, discount, gst, grand_total
            FROM sales WHERE tenant_id = :tid AND customer_mobile = :mob
            ORDER BY bill_date DESC
        """),
        engine, params={"tid": tenant_id, "mob": mobile}
    )
