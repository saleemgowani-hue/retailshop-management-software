"""
stock_purchase_saas.py — Tenant-aware Stock Purchase module.

Ports app.py's render_stock_purchase() — New Purchase Entry (with
immediate Cash/UPI split payment), Purchase History, and Edit/Delete
Entry (with stock reversal) — onto the tenant-aware PostgreSQL layer.

Bill-photo storage: the original app saved photos to a local
bill_photos/ folder. On Streamlit Cloud that's ephemeral — this module
just stores whatever path/URL string it's given (bill_photo_ref), on
the assumption the caller has already uploaded the actual file to
object storage (S3/Cloud Storage/etc., per Section M of the analysis)
and is passing back a reference, not a local path.
"""

import pandas as pd
from datetime import date
from sqlalchemy import text

from database_saas import get_engine, read_sql_df


def save_purchase(tenant_id: str, supplier_id: str, product_id: str, quantity: float,
                   purchase_price: float, discount: float, gst: float, transport: float,
                   cash_now: float = 0, upi_now: float = 0, update_product_price: bool = False,
                   bill_photo_ref: str = None):
    """
    Full atomic transaction: validate the Cash+UPI split doesn't exceed
    the purchase total, insert the purchase, record the immediate
    payment (if any) in supplier_payments, bump the product's stock,
    and optionally update its purchase_price.
    Returns (success: bool, result: str) — purchase_id on success, error
    message on failure.
    """
    base = quantity * purchase_price
    taxable = max(base - discount, 0)
    gst_amt = taxable * gst / 100
    total_amount = round(taxable + gst_amt + transport, 2)

    paid_now = round((cash_now or 0) + (upi_now or 0), 2)
    if paid_now > total_amount + 0.01:
        return False, f"Cash + UPI (₹{paid_now:.2f}) Total Amount (₹{total_amount:.2f}) se zyada hai."
    paid_now = min(paid_now, total_amount)

    engine = get_engine()
    purchase_date = date.today()
    try:
        with engine.begin() as conn:
            # Defense-in-depth: confirm supplier/product actually belong to
            # this tenant before inserting — a stale/tampered ID from a
            # different tenant must never silently attach to this purchase.
            supplier_ok = conn.execute(
                text("SELECT 1 FROM suppliers WHERE tenant_id = :tid AND id = :sid"),
                {"tid": tenant_id, "sid": supplier_id},
            ).fetchone()
            product_row = conn.execute(
                text("SELECT id FROM products WHERE tenant_id = :tid AND id = :pid"),
                {"tid": tenant_id, "pid": product_id},
            ).fetchone()
            if not supplier_ok or not product_row:
                return False, "Supplier or Product not found (or belongs to a different shop)."

            row = conn.execute(
                text("""
                    INSERT INTO purchases (tenant_id, purchase_date, supplier_id, product_id, quantity,
                                            purchase_price, discount, gst, transport, total_amount,
                                            paid_amount, bill_photo_path)
                    VALUES (:tid, :pd, :sid, :pid, :qty, :pp, :disc, :gst, :transport, :total, :paid, :photo)
                    RETURNING id
                """),
                {"tid": tenant_id, "pd": purchase_date, "sid": supplier_id, "pid": product_id,
                 "qty": quantity, "pp": purchase_price, "disc": discount, "gst": gst,
                 "transport": transport, "total": total_amount, "paid": paid_now, "photo": bill_photo_ref},
            ).fetchone()
            purchase_id = row[0]

            conn.execute(
                text("UPDATE products SET opening_stock = opening_stock + :qty WHERE tenant_id = :tid AND id = :pid"),
                {"qty": quantity, "tid": tenant_id, "pid": product_id},
            )
            if update_product_price:
                conn.execute(
                    text("UPDATE products SET purchase_price = :pp WHERE tenant_id = :tid AND id = :pid"),
                    {"pp": purchase_price, "tid": tenant_id, "pid": product_id},
                )

            if paid_now > 0:
                conn.execute(
                    text("""
                        INSERT INTO supplier_payments (tenant_id, purchase_id, supplier_id, payment_date,
                                                         cash_amount, upi_amount, total_amount)
                        VALUES (:tid, :pid, :sid, :pd, :cash, :upi, :total)
                    """),
                    {"tid": tenant_id, "pid": purchase_id, "sid": supplier_id, "pd": purchase_date,
                     "cash": cash_now or 0, "upi": upi_now or 0, "total": paid_now},
                )

        return True, str(purchase_id)
    except Exception as e:
        return False, f"Could not save purchase: {e}"


def _split_amount(total: float, weights: list) -> list:
    """Distributes `total` across `weights` (e.g. each bill line's total_amount)
    proportionally, rounded to 2 decimals, with the rounding remainder pushed
    onto the last item so the parts sum EXACTLY to `total` -- used to split one
    Cash/UPI payment across a multi-item bill's purchase rows."""
    n = len(weights)
    total = round(total or 0, 2)
    weight_sum = sum(weights)
    if total <= 0 or n == 0 or weight_sum <= 0:
        return [0.0] * n
    shares = [round(total * w / weight_sum, 2) for w in weights]
    diff = round(total - sum(shares), 2)
    shares[-1] = round(shares[-1] + diff, 2)
    return shares


def save_purchase_bill(tenant_id: str, supplier_id: str, bill_number: str, items: list,
                        cash_now: float = 0, upi_now: float = 0,
                        update_product_price: bool = False, bill_photo_ref: str = None):
    """
    One supplier bill with multiple product lines. The schema has no
    separate bill-header table, so this saves one `purchases` row PER LINE
    (same as save_purchase() above), all tagged with the same bill_number
    so they can be filtered/grouped together afterwards.

    The bill's total Cash/UPI payment is split across lines proportional to
    each line's own total_amount (via _split_amount), so the Supplier
    Ledger balance per product comes out correct without the user having to
    split the payment manually line by line.

    items: list of {"product_id", "quantity", "purchase_price", "discount",
    "gst", "transport"} dicts, one per bill line ("discount"/"gst"/
    "transport" default to 0 if omitted).
    Returns (success: bool, result): list of purchase_ids on success, an
    error message on failure.
    """
    if not items:
        return False, "Bill me kam se kam ek product line honi chahiye."

    line_totals = []
    for item in items:
        base = float(item["quantity"]) * float(item["purchase_price"])
        taxable = max(base - float(item.get("discount") or 0), 0)
        gst_amt = taxable * float(item.get("gst") or 0) / 100
        line_totals.append(round(taxable + gst_amt + float(item.get("transport") or 0), 2))

    grand_total = round(sum(line_totals), 2)
    paid_now = round((cash_now or 0) + (upi_now or 0), 2)
    if paid_now > grand_total + 0.01:
        return False, f"Cash + UPI (₹{paid_now:.2f}) Bill Total (₹{grand_total:.2f}) se zyada hai."

    cash_shares = _split_amount(cash_now, line_totals)
    upi_shares = _split_amount(upi_now, line_totals)

    engine = get_engine()
    purchase_date = date.today()
    purchase_ids = []
    try:
        with engine.begin() as conn:
            supplier_ok = conn.execute(
                text("SELECT 1 FROM suppliers WHERE tenant_id = :tid AND id = :sid"),
                {"tid": tenant_id, "sid": supplier_id},
            ).fetchone()
            if not supplier_ok:
                return False, "Supplier not found (or belongs to a different shop)."

            for item, line_total, cash_share, upi_share in zip(items, line_totals, cash_shares, upi_shares):
                product_row = conn.execute(
                    text("SELECT id FROM products WHERE tenant_id = :tid AND id = :pid"),
                    {"tid": tenant_id, "pid": item["product_id"]},
                ).fetchone()
                if not product_row:
                    return False, f"Product not found (or belongs to a different shop): {item.get('product_id')}"

                paid_share = round(cash_share + upi_share, 2)
                row = conn.execute(
                    text("""
                        INSERT INTO purchases (tenant_id, purchase_date, supplier_id, product_id, quantity,
                                                purchase_price, discount, gst, transport, total_amount,
                                                paid_amount, bill_photo_path, bill_number)
                        VALUES (:tid, :pd, :sid, :pid, :qty, :pp, :disc, :gst, :transport, :total,
                                :paid, :photo, :bill_number)
                        RETURNING id
                    """),
                    {"tid": tenant_id, "pd": purchase_date, "sid": supplier_id, "pid": item["product_id"],
                     "qty": item["quantity"], "pp": item["purchase_price"], "disc": item.get("discount") or 0,
                     "gst": item.get("gst") or 0, "transport": item.get("transport") or 0, "total": line_total,
                     "paid": paid_share, "photo": bill_photo_ref, "bill_number": (bill_number or None)},
                ).fetchone()
                purchase_id = row[0]
                purchase_ids.append(str(purchase_id))

                conn.execute(
                    text("UPDATE products SET opening_stock = opening_stock + :qty WHERE tenant_id = :tid AND id = :pid"),
                    {"qty": item["quantity"], "tid": tenant_id, "pid": item["product_id"]},
                )
                if update_product_price:
                    conn.execute(
                        text("UPDATE products SET purchase_price = :pp WHERE tenant_id = :tid AND id = :pid"),
                        {"pp": item["purchase_price"], "tid": tenant_id, "pid": item["product_id"]},
                    )

                if paid_share > 0:
                    conn.execute(
                        text("""
                            INSERT INTO supplier_payments (tenant_id, purchase_id, supplier_id, payment_date,
                                                             cash_amount, upi_amount, total_amount)
                            VALUES (:tid, :pid, :sid, :pd, :cash, :upi, :total)
                        """),
                        {"tid": tenant_id, "pid": purchase_id, "sid": supplier_id, "pd": purchase_date,
                         "cash": cash_share, "upi": upi_share, "total": paid_share},
                    )

        return True, purchase_ids
    except Exception as e:
        return False, f"Could not save purchase bill: {e}"


def get_purchase_history(tenant_id: str, start_date: date, end_date: date, supplier_id: str = None) -> pd.DataFrame:
    engine = get_engine()
    query = """
        SELECT pu.id, pu.purchase_date, s.name AS supplier, p.name AS product, pu.quantity,
               pu.purchase_price, pu.discount, pu.gst, pu.transport, pu.total_amount,
               pu.paid_amount, pu.bill_photo_path, pu.supplier_id, pu.product_id, pu.bill_number
        FROM purchases pu
        LEFT JOIN suppliers s ON s.id = pu.supplier_id AND s.tenant_id = pu.tenant_id
        LEFT JOIN products p ON p.id = pu.product_id AND p.tenant_id = pu.tenant_id
        WHERE pu.tenant_id = :tid AND pu.purchase_date BETWEEN :sd AND :ed
    """
    params = {"tid": tenant_id, "sd": start_date, "ed": end_date}
    if supplier_id:
        query += " AND pu.supplier_id = :sid"
        params["sid"] = supplier_id
    query += " ORDER BY pu.purchase_date DESC"
    return read_sql_df(text(query), engine, params=params)


def get_purchase_by_id(tenant_id: str, purchase_id: str):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM purchases WHERE tenant_id = :tid AND id = :pid"),
            {"tid": tenant_id, "pid": purchase_id},
        ).mappings().fetchone()
        return dict(row) if row else None


def update_purchase_quantity(tenant_id: str, purchase_id: str, new_quantity: float):
    """Adjusts the product's stock by the DIFFERENCE between old and new
    quantity — mirrors the original app's edit-entry stock-adjustment
    logic, now tenant-scoped throughout."""
    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT product_id, quantity FROM purchases WHERE tenant_id = :tid AND id = :pid FOR UPDATE"),
                {"tid": tenant_id, "pid": purchase_id},
            ).fetchone()
            if not row:
                return False, "Purchase not found (or belongs to a different shop)."
            product_id, old_qty = row[0], float(row[1])
            qty_diff = new_quantity - old_qty

            conn.execute(
                text("UPDATE purchases SET quantity = :qty WHERE tenant_id = :tid AND id = :pid"),
                {"qty": new_quantity, "tid": tenant_id, "pid": purchase_id},
            )
            conn.execute(
                text("UPDATE products SET opening_stock = opening_stock + :diff WHERE tenant_id = :tid AND id = :pid"),
                {"diff": qty_diff, "tid": tenant_id, "pid": product_id},
            )
        return True, "Purchase quantity updated, stock adjusted."
    except Exception as e:
        return False, f"Could not update purchase: {e}"


def delete_purchase(tenant_id: str, purchase_id: str):
    """Reverses the stock this purchase added, then removes the purchase
    record. Any supplier_payments tied to this purchase are removed in
    the same transaction (a payment record without its purchase doesn't
    make sense to keep standalone) — this does mean deleting a purchase
    also deletes its payment history, which is a deliberate, documented
    trade-off matching what a mistaken-entry correction should do."""
    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT product_id, quantity FROM purchases WHERE tenant_id = :tid AND id = :pid FOR UPDATE"),
                {"tid": tenant_id, "pid": purchase_id},
            ).fetchone()
            if not row:
                return False, "Purchase not found (or belongs to a different shop)."
            product_id, qty = row[0], float(row[1])

            conn.execute(
                text("DELETE FROM supplier_payments WHERE tenant_id = :tid AND purchase_id = :pid"),
                {"tid": tenant_id, "pid": purchase_id},
            )
            conn.execute(
                text("DELETE FROM purchases WHERE tenant_id = :tid AND id = :pid"),
                {"tid": tenant_id, "pid": purchase_id},
            )
            conn.execute(
                text("UPDATE products SET opening_stock = GREATEST(opening_stock - :qty, 0) WHERE tenant_id = :tid AND id = :pid"),
                {"qty": qty, "tid": tenant_id, "pid": product_id},
            )
        return True, "Purchase deleted, stock reversed."
    except Exception as e:
        return False, f"Could not delete purchase: {e}"
