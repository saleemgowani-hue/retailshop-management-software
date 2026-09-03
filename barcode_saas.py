"""
barcode_saas.py — Barcode generation, auto-barcode assignment, and
printable label-sheet HTML — ported from app.py's generate_barcode_png_base64
/ build_barcode_label_sheet_html / render_print_button.

No tenant-specific logic lives here (barcode IMAGE generation from a
string doesn't touch the database at all) — the tenant-safety work
already happened in products_saas.py's next_auto_barcode() /
assign_auto_barcode_if_missing(), which THIS module's callers use to
get a safe barcode value before calling generate_barcode_png_base64().
"""

import io
import base64

try:
    import barcode as barcode_lib
    from barcode.writer import ImageWriter
    BARCODE_LIB_AVAILABLE = True
except ImportError:
    BARCODE_LIB_AVAILABLE = False


def safe_barcode_str(value):
    """Same defensive helper as the local app — pandas can hand back NaN
    (float) for a NULL/empty TEXT column when other rows look numeric."""
    if value is None:
        return ""
    if isinstance(value, float):
        try:
            import math
            if math.isnan(value):
                return ""
        except Exception:
            pass
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value).strip()


def generate_barcode_png_base64(value):
    """Generates a real, scannable CODE128 barcode image (PNG, base64).
    Returns None if the library isn't installed or the value is empty."""
    clean_value = safe_barcode_str(value)
    if not BARCODE_LIB_AVAILABLE or not clean_value:
        return None
    try:
        code_class = barcode_lib.get_barcode_class("code128")
        writer = ImageWriter()
        writer.set_options({"write_text": False, "module_height": 10, "module_width": 0.35, "quiet_zone": 1.5})
        buffer = io.BytesIO()
        code_obj = code_class(clean_value, writer=writer)
        code_obj.write(buffer)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


LABEL_SIZE_PRESETS = {
    "50x25mm (Small)": {"w": 50, "h": 25, "img_h": 8, "name_size": 8, "addr_size": 6, "price_size": 9},
    "75x38mm (Medium)": {"w": 75, "h": 38, "img_h": 13, "name_size": 10.5, "addr_size": 7.5, "price_size": 13},
    "100x50mm (Large)": {"w": 100, "h": 50, "img_h": 18, "name_size": 13, "addr_size": 9, "price_size": 16},
}


def build_barcode_label_sheet_html(label_items, shop_name="", shop_address="", shop_mobile="",
                                    disclaimer="", label_size_key="75x38mm (Medium)"):
    """label_items: list of dicts with name, price, barcode, copies, img_b64.
    Identical design to the local app's version — Shop Name (bold),
    Address, Mobile, Barcode image, Barcode number, Product Name, Price
    (underlined), optional disclaimer."""
    preset = LABEL_SIZE_PRESETS.get(label_size_key, LABEL_SIZE_PRESETS["75x38mm (Medium)"])
    label_w, label_h = preset["w"], preset["h"]
    usable_width_mm = 190
    gap_mm = 3
    cols = max(1, int((usable_width_mm + gap_mm) // (label_w + gap_mm)))

    cells = []
    for item in label_items:
        for _ in range(item["copies"]):
            img_tag = (
                f'<img src="data:image/png;base64,{item["img_b64"]}" />'
                if item["img_b64"] else '<div class="no-img">Barcode nahi bana</div>'
            )
            shop_block = ""
            if shop_name:
                shop_block += f'<div class="shopname">{shop_name}</div>'
            if shop_address:
                shop_block += f'<div class="shopaddr">{shop_address}</div>'
            if shop_mobile:
                shop_block += f'<div class="shopmobile">Mo.{shop_mobile}</div>'
            cells.append(f"""
                <div class="label">
                    {shop_block}
                    {img_tag}
                    <div class="barcodenum">{item['barcode'] or ''}</div>
                    <div class="pname">{item['name']}</div>
                    <div class="pprice">Rs. {item['price']:.2f}</div>
                    {f'<div class="disclaimer">{disclaimer}</div>' if disclaimer else ''}
                </div>
            """)
    cells_html = "".join(cells)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
        @page {{ size: A4; margin: 8mm; }}
        * {{ box-sizing: border-box; }}
        body {{ font-family: Arial, sans-serif; margin: 0; }}
        .sheet {{ display: grid; grid-template-columns: repeat({cols}, 1fr); gap: {gap_mm}mm; }}
        .label {{
            border: 1px dashed #999; border-radius: 1.5mm; padding: 1.5mm;
            text-align: center; width: 100%; height: {label_h}mm;
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            overflow: hidden; page-break-inside: avoid;
        }}
        .shopname {{ font-size: {preset['name_size']}px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }}
        .shopaddr {{ font-size: {preset['addr_size']}px; color: #444; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }}
        .shopmobile {{ font-size: {preset['addr_size']}px; color: #444; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; margin-bottom: 0.5mm; }}
        .label img {{ width: 94%; height: {preset['img_h']}mm; margin: 0.5mm 0; object-fit: fill; display: block; }}
        .barcodenum {{ font-size: {max(preset['addr_size'] - 0.5, 5)}px; color: #333; letter-spacing: 0.3px; }}
        .pname {{ font-size: {max(preset['name_size'] - 1.5, 6)}px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; margin-top: 0.5mm; }}
        .pprice {{ font-size: {preset['price_size']}px; font-weight: bold; margin-top: 0.5mm; text-decoration: underline; }}
        .disclaimer {{ font-size: {max(preset['addr_size'] - 1.5, 4.5)}px; color: #555; margin-top: 0.5mm; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }}
        .no-img {{ font-size: 8px; color: #c0392b; margin: 1mm 0; }}
    </style></head>
    <body><div class="sheet">{cells_html}</div></body></html>"""
    return html


def render_print_button_html(receipt_html, key, label="🖨️ Print"):
    """Returns the HTML/JS for an embedded print button (same mechanism
    as the local app: base64-encode the receipt, open a new window,
    call print() on it — works identically whether the Streamlit app is
    local or Cloud-hosted, since printing happens in the USER's browser,
    not on the server)."""
    encoded = base64.b64encode(receipt_html.encode("utf-8")).decode("ascii")
    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif;">
      <button onclick="printReceipt_{key}()" style="
          background: linear-gradient(135deg, #00c6ff, #0072ff); color: white; border: none;
          border-radius: 8px; padding: 10px 22px; font-weight: 700; font-size: 14px; cursor: pointer;
          box-shadow: 0 2px 5px rgba(0,0,0,0.15); width: 100%;">
        {label}
      </button>
    </div>
    <script>
    function printReceipt_{key}() {{
        var html = atob("{encoded}");
        var w = window.open('', '_blank', 'width=400,height=600');
        if (!w) {{ alert('Popup blocked hai — browser settings me popups allow karein.'); return; }}
        w.document.write(html);
        w.document.close();
        w.onload = function() {{ setTimeout(function() {{ w.focus(); w.print(); }}, 300); }};
    }}
    </script>
    """
