"""
products_saas.py — Tenant-aware Product Master module.

Ports app.py's render_product_master() data-access logic (Add/Edit/Delete
Product, safe-delete-vs-deactivate check, auto-barcode generation) onto
the tenant-aware PostgreSQL layer.

Note on barcode auto-generation: the original app used
f"IN{product_id:08d}" because product_id was a small sequential SQLite
integer. PostgreSQL product_id here is a UUID, so that scheme doesn't
apply directly — next_auto_barcode() below derives a short, still-unique
code straight from the UUID itself (no extra per-tenant counter needed).
"""

import pandas as pd
from sqlalchemy import text

from database_saas import get_engine


def next_auto_barcode(product_id) -> str:
    """Unique because UUIDs are globally unique — no per-tenant counter
    or extra state needed, unlike the old sequential-integer scheme."""
    clean = str(product_id).replace("-", "").upper()
    return f"IN{clean[:10]}"


def add_product(tenant_id: str, name: str, barcode: str = None, category: str = "",
                 brand: str = "", unit: str = "Pcs", purchase_price: float = 0,
                 selling_price: float = 0, gst: float = 0, opening_stock: float = 0,
                 minimum_stock: float = 0, default_discount: float = 0):
    if not name or not name.strip():
        return False, "Product Name is required."

    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO products (tenant_id, name, barcode, category, brand, unit,
                                           purchase_price, selling_price, gst, opening_stock,
                                           minimum_stock, default_discount, is_active)
                    VALUES (:tid, :name, :barcode, :category, :brand, :unit,
                            :pp, :sp, :gst, :stock, :minstock, :disc, TRUE)
                    RETURNING id
                """),
                {"tid": tenant_id, "name": name.strip(), "barcode": barcode or None,
                 "category": category, "brand": brand, "unit": unit,
                 "pp": purchase_price, "sp": selling_price, "gst": gst,
                 "stock": opening_stock, "minstock": minimum_stock, "disc": default_discount},
            ).fetchone()
        return True, str(row[0])
    except Exception as e:
        return False, f"Could not save product: {e}"


def update_product(tenant_id: str, product_id: str, name: str, barcode: str, category: str,
                    brand: str, unit: str, purchase_price: float, selling_price: float,
                    gst: float, opening_stock: float, minimum_stock: float,
                    default_discount: float, is_active: bool):
    if not name or not name.strip():
        return False, "Product Name is required."

    engine = get_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE products SET name=:name, barcode=:barcode, category=:category, brand=:brand,
                           unit=:unit, purchase_price=:pp, selling_price=:sp, gst=:gst,
                           opening_stock=:stock, minimum_stock=:minstock, default_discount=:disc,
                           is_active=:active
                    WHERE tenant_id = :tid AND id = :pid
                """),
                {"name": name.strip(), "barcode": barcode or None, "category": category, "brand": brand,
                 "unit": unit, "pp": purchase_price, "sp": selling_price, "gst": gst,
                 "stock": opening_stock, "minstock": minimum_stock, "disc": default_discount,
                 "active": is_active, "tid": tenant_id, "pid": product_id},
            )
            if result.rowcount == 0:
                return False, "Product not found (or belongs to a different shop)."
        return True, "Product updated successfully!"
    except Exception as e:
        return False, f"Could not update product: {e}"


def get_all_products(tenant_id: str) -> pd.DataFrame:
    """Every product (active + inactive) for this tenant — used by the
    View/Edit and Delete tabs, which need to show deactivated items too."""
    engine = get_engine()
    return pd.read_sql(
        text("SELECT * FROM products WHERE tenant_id = :tid ORDER BY name"),
        engine, params={"tid": tenant_id}
    )


def check_product_in_use(tenant_id: str, product_id: str):
    """Returns (used_in_sales: int, used_in_purchases: int) — mirrors the
    original app's safe-delete check (never hard-delete a product that
    has real transaction history)."""
    engine = get_engine()
    with engine.connect() as conn:
        sales_count = conn.execute(
            text("SELECT COUNT(*) FROM sale_items WHERE tenant_id = :tid AND product_id = :pid"),
            {"tid": tenant_id, "pid": product_id},
        ).scalar()
        purchase_count = conn.execute(
            text("SELECT COUNT(*) FROM purchases WHERE tenant_id = :tid AND product_id = :pid"),
            {"tid": tenant_id, "pid": product_id},
        ).scalar()
    return sales_count, purchase_count


def delete_or_deactivate_product(tenant_id: str, product_id: str):
    """
    Mirrors the original app's exact safety logic:
      - if the product has NO transaction history -> hard DELETE
      - if it DOES -> deactivate (is_active = FALSE) instead, to avoid
        breaking historical sales/purchase records
    Returns (action: 'deleted' | 'deactivated' | 'not_found', message).
    """
    sales_count, purchase_count = check_product_in_use(tenant_id, product_id)
    engine = get_engine()

    if sales_count > 0 or purchase_count > 0:
        with engine.begin() as conn:
            result = conn.execute(
                text("UPDATE products SET is_active = FALSE WHERE tenant_id = :tid AND id = :pid"),
                {"tid": tenant_id, "pid": product_id},
            )
        if result.rowcount == 0:
            return "not_found", "Product not found."
        return "deactivated", (
            f"Product is referenced in {sales_count} sale item(s) and {purchase_count} "
            f"purchase(s) — deactivated instead of deleted to protect historical records."
        )
    else:
        with engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM products WHERE tenant_id = :tid AND id = :pid"),
                {"tid": tenant_id, "pid": product_id},
            )
        if result.rowcount == 0:
            return "not_found", "Product not found."
        return "deleted", "Product permanently deleted."


def assign_auto_barcode_if_missing(tenant_id: str, product_id: str) -> str:
    """If the product has no barcode yet, generates and saves one, then
    returns the (possibly newly-assigned) barcode value."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT barcode FROM products WHERE tenant_id = :tid AND id = :pid"),
            {"tid": tenant_id, "pid": product_id},
        ).fetchone()
        if row and row[0] and row[0].strip():
            return row[0]
        new_code = next_auto_barcode(product_id)
        conn.execute(
            text("UPDATE products SET barcode = :bc WHERE tenant_id = :tid AND id = :pid"),
            {"bc": new_code, "tid": tenant_id, "pid": product_id},
        )
        return new_code
