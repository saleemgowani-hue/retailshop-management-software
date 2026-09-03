"""
bulk_import_export_saas.py — Tenant-aware Bulk Product Import/Export.

Ports app.py's Bulk Import/Export tab (Product Master) — download a
template/current-catalog Excel, upload one back with new/updated rows.

Every write here goes through the SAME tenant-scoped INSERT/UPDATE
pattern as products_saas.py (in fact this module reuses it directly for
single-row writes) — bulk import is just "call add_product /
update-by-barcode in a loop", not a separate risky code path.
"""

import io
import pandas as pd
from sqlalchemy import text

from database_saas import get_engine
import products_saas as prod
from barcode_saas import safe_barcode_str

TEMPLATE_COLUMNS = [
    "name", "barcode", "category", "brand", "unit",
    "purchase_price", "selling_price", "gst", "opening_stock", "minimum_stock",
]


def export_products_to_excel(tenant_id: str) -> bytes:
    """Full current catalog, in the same column layout the import
    expects back — round-trippable (export, edit in Excel, re-import)."""
    df = prod.get_all_products(tenant_id)
    if not df.empty:
        df = df[[c for c in TEMPLATE_COLUMNS if c in df.columns]]
    else:
        df = pd.DataFrame(columns=TEMPLATE_COLUMNS)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Products")
    return buffer.getvalue()


def get_blank_template_excel() -> bytes:
    df = pd.DataFrame(columns=TEMPLATE_COLUMNS)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Products")
    return buffer.getvalue()


def import_products_from_excel(tenant_id: str, file_bytes: bytes, update_existing_by_barcode: bool = True):
    """
    Reads an uploaded Excel file and imports/updates products for this
    tenant only. Returns a summary dict:
      {"total": int, "imported": int, "updated": int, "errors": [str, ...]}
    Blank name / negative price rows are skipped with an error message
    rather than silently corrupting the catalog — matches the local
    app's bulk-import safety behaviour.
    """
    summary = {"total": 0, "imported": 0, "updated": 0, "errors": []}
    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception as e:
        summary["errors"].append(f"Could not read Excel file: {e}")
        return summary

    # Tolerant column matching (strip spaces/underscores/case, like the
    # local app's auto-match logic) rather than requiring exact headers.
    col_map = {}
    normalized = {c: str(c).strip().lower().replace(" ", "").replace("_", "") for c in df.columns}
    for target in TEMPLATE_COLUMNS:
        target_norm = target.replace("_", "")
        for orig, norm in normalized.items():
            if norm == target_norm:
                col_map[target] = orig
                break

    if "name" not in col_map:
        summary["errors"].append("Excel me 'name' column nahi mila.")
        return summary

    engine = get_engine()
    summary["total"] = len(df)

    # Pre-fetch existing barcodes for update-by-barcode matching
    existing = prod.get_all_products(tenant_id)
    existing_by_barcode = {}
    if not existing.empty and "barcode" in existing.columns:
        for _, r in existing.iterrows():
            bc = safe_barcode_str(r.get("barcode"))
            if bc:
                existing_by_barcode[bc] = str(r["id"])

    for idx, row in df.iterrows():
        name = str(row.get(col_map.get("name"), "")).strip()
        if not name or name.lower() == "nan":
            summary["errors"].append(f"Row {idx + 2}: naam khaali hai, skip kiya.")
            continue

        def _num(col, default=0.0):
            val = row.get(col_map.get(col))
            try:
                return float(val) if pd.notna(val) else default
            except (TypeError, ValueError):
                return default

        def _str(col, default=""):
            val = row.get(col_map.get(col)) if col in col_map else None
            return str(val).strip() if pd.notna(val) else default

        barcode_val = _str("barcode", "") or None

        if update_existing_by_barcode and barcode_val and barcode_val in existing_by_barcode:
            pid = existing_by_barcode[barcode_val]
            existing_row = existing[existing["id"].astype(str) == pid].iloc[0]
            ok, msg = prod.update_product(
                tenant_id, pid, name, barcode_val, _str("category"), _str("brand"),
                _str("unit", "Pcs"), _num("purchase_price"), _num("selling_price"),
                _num("gst", 5.0), _num("opening_stock"), _num("minimum_stock", 5.0),
                float(existing_row.get("default_discount") or 0), bool(existing_row.get("is_active", True)),
            )
            if ok:
                summary["updated"] += 1
            else:
                summary["errors"].append(f"Row {idx + 2} ({name}): {msg}")
        else:
            ok, result = prod.add_product(
                tenant_id, name, barcode_val, _str("category"), _str("brand"),
                _str("unit", "Pcs"), _num("purchase_price"), _num("selling_price"),
                _num("gst", 5.0), _num("opening_stock"), _num("minimum_stock", 5.0),
            )
            if ok:
                summary["imported"] += 1
            else:
                summary["errors"].append(f"Row {idx + 2} ({name}): {result}")

    return summary
