"""
pos_saas.py — Tenant-aware POS/Billing module.

Ports app.py's render_pos() data-access logic (product search, barcode
scan, cart, split Cash/UPI payment, stock decrement, sale save) onto the
tenant-aware PostgreSQL layer from database_saas.py.

Every function takes tenant_id explicitly — this is the module flagged as
highest-risk in SaaS_Migration_Analysis.md (Section I): the barcode-scan
lookup in particular was the one CONCRETE cross-tenant leak vector found
in the original code.
"""

import uuid
from datetime import datetime, date
import pandas as pd
from sqlalchemy import text

from database_saas import get_engine


# ---------------------------------------------------------------------------
# Product lookups (tenant-scoped) — replaces app.py's
# fetch_active_products()/get_live_stock()/barcode-scan SELECT.
# ---------------------------------------------------------------------------
def fetch_active_products(tenant_id: str) -> pd.DataFrame:
    """Equivalent of the original @st.cache_data fetch_active_products() —
    caching itself is a Streamlit-layer concern (added back when this is
    wired into app.py, keyed on tenant_id as the analysis's Section L
    requires); this function is the tenant-safe query underneath it."""
    engine = get_engine()
    return pd.read_sql(
        text("SELECT * FROM products WHERE tenant_id = :tid AND is_active = TRUE ORDER BY name"),
        engine, params={"tid": tenant_id}
    )


def get_live_stock(tenant_id: str, product_id: str) -> float:
    """Always-fresh (uncached) stock check, tenant-scoped — used right
    before any write that depends on current stock."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT opening_stock FROM products WHERE tenant_id = :tid AND id = :pid"),
            {"tid": tenant_id, "pid": product_id},
        ).fetchone()
        return float(row[0]) if row else 0.0


def scan_barcode(tenant_id: str, barcode: str):
    """THE critical fix from the analysis: tenant_id is in the WHERE
    clause, so an identical barcode string in two different tenants'
    catalogs can never cross-match. Returns a dict or None."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT * FROM products
                WHERE tenant_id = :tid AND barcode = :bc AND is_active = TRUE
            """),
            {"tid": tenant_id, "bc": barcode},
        ).mappings().fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Customer upsert (tenant-scoped) — replaces the POS "save this as a new
# customer" checkbox logic.
# ---------------------------------------------------------------------------
def upsert_customer(tenant_id: str, name: str, mobile: str, address: str = ""):
    if not mobile:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO customers (tenant_id, name, mobile, address, is_active)
                VALUES (:tid, :name, :mobile, :address, TRUE)
                ON CONFLICT (tenant_id, mobile) DO NOTHING
            """),
            {"tid": tenant_id, "name": name, "mobile": mobile, "address": address},
        )


# ---------------------------------------------------------------------------
# Save a completed sale — full atomic transaction:
#   1. Re-verify stock live for every cart line (never trust a stale value)
#   2. Validate Mixed payment split == grand_total
#   3. Insert sale + sale_items
#   4. Decrement stock
#   5. Optionally save the customer
# Everything happens inside ONE database transaction (engine.begin()) —
# if any step fails, nothing is partially saved (matches the original
# app's try/except + conn.rollback() pattern, but now via SQLAlchemy).
# ---------------------------------------------------------------------------
def save_sale(tenant_id: str, cust_name: str, cust_mobile: str, pay_mode: str,
              cart_items: list, discount_amt: float, cash_amount: float,
              upi_amount: float, save_customer: bool = False):
    """
    cart_items: list of dicts, each with product_id, name, qty,
                selling_price, gst, total.
    Returns (success: bool, result: str) — result is either the new
    bill_number on success, or an error message on failure.
    """
    if not cart_items:
        return False, "Cart is empty."

    subtotal = sum(i["total"] for i in cart_items)
    gst_total = sum(i["total"] * i["gst"] / 100 for i in cart_items)
    discount_amt = min(discount_amt, subtotal)
    grand_total = round(subtotal - discount_amt + gst_total, 2)

    # Mixed-payment validation (mirrors app.py's split_valid check)
    if pay_mode == "Mixed":
        split_total = round((cash_amount or 0) + (upi_amount or 0), 2)
        if abs(grand_total - split_total) > 0.01:
            return False, f"Cash + UPI (₹{split_total:.2f}) Grand Total (₹{grand_total:.2f}) se match nahi karta."
        final_cash, final_upi = cash_amount, upi_amount
    elif pay_mode == "Cash":
        final_cash, final_upi = grand_total, 0.0
    elif pay_mode == "UPI":
        final_cash, final_upi = 0.0, grand_total
    else:  # Card
        final_cash, final_upi = 0.0, 0.0

    engine = get_engine()
    bill_no = "BILL-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()
    bill_date = date.today()

    try:
        with engine.begin() as conn:
            # 1) Re-verify stock live, per item, INSIDE the transaction
            for item in cart_items:
                row = conn.execute(
                    text("SELECT opening_stock FROM products WHERE tenant_id = :tid AND id = :pid FOR UPDATE"),
                    {"tid": tenant_id, "pid": item["product_id"]},
                ).fetchone()
                live_stock = float(row[0]) if row else 0.0
                if item["qty"] > live_stock:
                    raise ValueError(f"Insufficient stock for {item['name']} (need {item['qty']}, have {live_stock})")

            # 2) Insert sale
            sale_row = conn.execute(
                text("""
                    INSERT INTO sales (tenant_id, bill_number, bill_date, customer_name, customer_mobile,
                                        payment_mode, subtotal, discount, gst, grand_total, cash_amount, upi_amount)
                    VALUES (:tid, :bn, :bd, :cn, :cm, :pm, :sub, :disc, :gst, :gt, :cash, :upi)
                    RETURNING id
                """),
                {"tid": tenant_id, "bn": bill_no, "bd": bill_date, "cn": cust_name or "Walk-in Customer",
                 "cm": cust_mobile or "0000000000", "pm": pay_mode, "sub": subtotal, "disc": discount_amt,
                 "gst": gst_total, "gt": grand_total, "cash": final_cash, "upi": final_upi},
            ).fetchone()
            sale_id = sale_row[0]

            # 3) Insert sale_items + decrement stock (tenant-scoped WHERE, defends
            #    against ever touching another tenant's product row even by accident)
            for item in cart_items:
                conn.execute(
                    text("""
                        INSERT INTO sale_items (tenant_id, sale_id, product_id, quantity, selling_price, total)
                        VALUES (:tid, :sid, :pid, :qty, :price, :total)
                    """),
                    {"tid": tenant_id, "sid": sale_id, "pid": item["product_id"], "qty": item["qty"],
                     "price": item["selling_price"], "total": item["total"]},
                )
                result = conn.execute(
                    text("""
                        UPDATE products SET opening_stock = opening_stock - :qty
                        WHERE tenant_id = :tid AND id = :pid AND opening_stock >= :qty
                    """),
                    {"qty": item["qty"], "tid": tenant_id, "pid": item["product_id"]},
                )
                if result.rowcount == 0:
                    raise ValueError(f"Stock changed concurrently for {item['name']}. Bill aborted.")

            # 4) Optionally save new customer
            if save_customer and cust_mobile:
                conn.execute(
                    text("""
                        INSERT INTO customers (tenant_id, name, mobile, address, is_active)
                        VALUES (:tid, :name, :mobile, '', TRUE)
                        ON CONFLICT (tenant_id, mobile) DO NOTHING
                    """),
                    {"tid": tenant_id, "name": cust_name or "Customer", "mobile": cust_mobile},
                )

        return True, bill_no
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Could not save bill: {e}"


def get_sales_for_date(tenant_id: str, target_date: date) -> pd.DataFrame:
    """Tenant-scoped equivalent of app.py's Day Summary sales query."""
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT bill_number, customer_name, payment_mode, cash_amount, upi_amount, grand_total
            FROM sales WHERE tenant_id = :tid AND bill_date = :d ORDER BY bill_number DESC
        """),
        engine, params={"tid": tenant_id, "d": target_date}
    )
