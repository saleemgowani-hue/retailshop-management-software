"""
tally_import_saas.py — Tenant-aware Tally (Prime/ERP9) -> Products import.

Tally runs on the shop's own computer; this app runs on Streamlit Cloud,
so there's no live network path between them (Tally's ODBC/HTTP server
only ever listens on localhost). The only practical bridge is a file:
the shop owner exports their Stock Items from Tally as XML --

    Gateway of Tally -> Masters -> Inventory Info -> Stock Items
    -> select the items (or Alt+A for all) -> Alt+E (Export)
    -> Format: XML (Data Interchange)

-- and uploads that .xml file here.

Tally's XML export varies a fair amount by version/configuration (GST
may be set at item level, stock-group level, or not at all; quantities
and rates are often compound strings like "40 Pcs" or "220/Pcs"). This
parser is deliberately forgiving: a field it can't confidently read is
left at a safe default (0, or the unit "Pcs") rather than raising, and
the caller always gets a DataFrame back to review/edit in the UI BEFORE
anything is written to the database -- the same "extraction is a
reading aid, not an auto-save" posture as ocr_saas.py's bill-photo OCR.
"""

import re
import xml.etree.ElementTree as ET

import pandas as pd

import products_saas as prod
from barcode_saas import safe_barcode_str

PREVIEW_COLUMNS = ["name", "unit", "opening_stock", "purchase_price", "selling_price", "gst"]


def _parse_qty(text):
    """Tally quantities are often compound, e.g. '40 Pcs' or '40.00 Nos' —
    take the leading numeric part only."""
    if not text:
        return 0.0
    match = re.match(r"[-\d.]+", text.strip())
    return float(match.group()) if match else 0.0


def _parse_rate(text):
    """Tally rates are usually 'AMOUNT/UNIT', e.g. '220/Pcs'."""
    if not text:
        return 0.0
    head = text.strip().split("/")[0]
    match = re.match(r"[-\d.]+", head)
    return float(match.group()) if match else 0.0


def _parse_gst_rate(stock_item_el) -> float:
    """Sums CGST+SGST (or IGST alone) rate entries under GSTDETAILS.LIST,
    if present — covers both of Tally's common intra-state/inter-state
    GST configurations. Returns 0.0 if GST isn't set at the item level at
    all (common when it's configured at Stock Group/Company level
    instead) — the user fills that in manually after import in that case."""
    total = 0.0
    found = False
    for rate_el in stock_item_el.iter("RATEDETAILS.LIST"):
        head = (rate_el.findtext("GSTRATEDUTYHEAD") or "").strip().upper()
        if head in ("CGST", "SGST", "IGST"):
            rate_text = rate_el.findtext("GSTRATE")
            if rate_text:
                try:
                    total += float(rate_text.strip())
                    found = True
                except ValueError:
                    pass
    return total if found else 0.0


def parse_tally_stock_items_xml(file_bytes: bytes):
    """
    Returns (df, warnings). df has columns PREVIEW_COLUMNS, one row per
    <STOCKITEM> found anywhere in the file (searched regardless of the
    surrounding ENVELOPE/TALLYMESSAGE wrapper, since that varies by
    export method/Tally version). warnings is a list of human-readable
    strings about rows that were skipped or fields that were defaulted —
    shown in the UI alongside the editable preview, never silently.
    """
    try:
        root = ET.fromstring(file_bytes)
    except ET.ParseError as e:
        return pd.DataFrame(columns=PREVIEW_COLUMNS), [
            f"XML parse nahi ho payi: {e}. Sahi Tally export file hai ya check karein."
        ]

    stock_items = root.findall(".//STOCKITEM")
    if not stock_items:
        return pd.DataFrame(columns=PREVIEW_COLUMNS), [
            "File me koi Stock Item nahi mila. Tally me Gateway of Tally -> Masters -> "
            "Inventory Info -> Stock Items se, format 'XML (Data Interchange)' me export karein."
        ]

    warnings = []
    rows = []
    for item in stock_items:
        name = (item.get("NAME") or "").strip()
        if not name:
            warnings.append("Ek Stock Item ka naam khaali tha, skip kiya.")
            continue

        unit = (item.findtext("BASEUNITS") or "Pcs").strip() or "Pcs"
        opening_stock = _parse_qty(item.findtext("OPENINGBALANCE"))
        purchase_price = _parse_rate(item.findtext("OPENINGRATE"))

        if purchase_price == 0.0:
            opening_value_text = item.findtext("OPENINGVALUE")
            if opening_value_text and opening_stock:
                try:
                    purchase_price = round(abs(float(opening_value_text.strip())) / opening_stock, 2)
                except (ValueError, ZeroDivisionError):
                    pass

        rows.append({
            "name": name,
            "unit": unit,
            "opening_stock": opening_stock,
            "purchase_price": purchase_price,
            # Tally doesn't track a retail selling price on the Stock Item
            # master by default -- default it to purchase price so it's
            # never left at zero, and flag it below for the user to check.
            "selling_price": purchase_price,
            "gst": _parse_gst_rate(item),
        })

    if rows:
        warnings.append(
            f"{len(rows)} item(s) mile. Selling Price abhi Purchase Price ke barabar rakhi gayi hai "
            f"(Tally generally selling price track nahi karta) — neeche table me edit karke, "
            f"phir 'Products Me Save Karein' dabayein."
        )

    return pd.DataFrame(rows, columns=PREVIEW_COLUMNS), warnings


def import_products_from_tally_df(tenant_id: str, df: pd.DataFrame):
    """
    Commits a (possibly user-edited) preview DataFrame — same shape as
    parse_tally_stock_items_xml's output — to the database.

    Matches existing products BY NAME (case-insensitive), not barcode:
    Tally's Stock Item master doesn't carry a barcode, so matching on it
    would treat every Tally import as brand-new products. Name matching
    means re-importing an updated Tally export later updates the same
    rows (fresh stock/price) instead of creating duplicates each time.

    Returns {"total": int, "imported": int, "updated": int, "errors": [...]}.
    """
    summary = {"total": len(df), "imported": 0, "updated": 0, "errors": []}

    existing = prod.get_all_products(tenant_id)
    existing_by_name = {}
    if not existing.empty:
        for _, r in existing.iterrows():
            existing_by_name[str(r["name"]).strip().lower()] = str(r["id"])

    for idx, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name or name.lower() == "nan":
            summary["errors"].append(f"Row {idx + 1}: naam khaali hai, skip kiya.")
            continue

        def _num(col, default=0.0):
            val = row.get(col)
            try:
                return float(val) if pd.notna(val) else default
            except (TypeError, ValueError):
                return default

        unit = str(row.get("unit") or "Pcs").strip() or "Pcs"
        existing_id = existing_by_name.get(name.lower())

        if existing_id:
            existing_row = existing[existing["id"].astype(str) == existing_id].iloc[0]
            ok, msg = prod.update_product(
                tenant_id, existing_id, name, safe_barcode_str(existing_row.get("barcode")) or None,
                existing_row.get("category") or "", existing_row.get("brand") or "", unit,
                _num("purchase_price"), _num("selling_price"), _num("gst", 5.0),
                _num("opening_stock"), float(existing_row.get("minimum_stock") or 5.0),
                float(existing_row.get("default_discount") or 0), bool(existing_row.get("is_active", True)),
            )
            if ok:
                summary["updated"] += 1
            else:
                summary["errors"].append(f"Row {idx + 1} ({name}): {msg}")
        else:
            ok, result = prod.add_product(
                tenant_id, name, None, "", "", unit,
                _num("purchase_price"), _num("selling_price"), _num("gst", 5.0),
                _num("opening_stock"), 5.0,
            )
            if ok:
                summary["imported"] += 1
            else:
                summary["errors"].append(f"Row {idx + 1} ({name}): {result}")

    return summary
