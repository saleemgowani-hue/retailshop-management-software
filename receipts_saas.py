"""
receipts_saas.py — 80mm Thermal / A4 / A5 invoice HTML builders.

Ported verbatim from app.py's build_80mm_receipt_html / build_a4_invoice_html
/ build_a5_invoice_html. No tenant logic needed here at all — these are pure
functions that take already-fetched, already-tenant-scoped data (shop
settings + sale + cart items) and produce printable HTML.

Printing itself happens in the user's own browser (window.print()) via
barcode_saas.render_print_button_html — this was already correctly
architected in the local app to be cloud-compatible (Section M of the
analysis: "printing already happens via the user's own browser, not the
server — no redesign needed").
"""

from datetime import datetime


def build_80mm_receipt_html(shop_name, address, mobile, gst_number, bill_no, bill_date,
                             cust_name, cust_mobile, pay_mode, cart_items,
                             subtotal, discount, gst_total, grand_total, footer_message,
                             cash_amount=0.0, upi_amount=0.0):
    rows_html = "".join(
        f"<div class='row'><span>{item['name']} x{item['qty']}</span>"
        f"<span>₹{item['total']:.2f}</span></div>"
        for item in cart_items
    )
    split_html = ""
    if pay_mode == "Mixed":
        split_html = (
            f"<div class=\"row\"><span>Cash Paid</span><span>₹{cash_amount:.2f}</span></div>"
            f"<div class=\"row\"><span>UPI Paid</span><span>₹{upi_amount:.2f}</span></div>"
        )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
        @page {{ size: 80mm auto; margin: 2mm; }}
        * {{ box-sizing: border-box; }}
        body {{ width: 76mm; margin: 0 auto; font-family: 'Courier New', monospace; font-size: 12px; color:#000; }}
        .center {{ text-align: center; }}
        .bold {{ font-weight: bold; }}
        .line {{ border-top: 1px dashed #000; margin: 4px 0; }}
        .row {{ display: flex; justify-content: space-between; margin: 2px 0; }}
        .big {{ font-size: 14px; }}
    </style></head>
    <body>
        <div class="center bold big">{shop_name or 'Retail Shop'}</div>
        <div class="center">{address or ''}</div>
        <div class="center">{('Mob: ' + mobile) if mobile else ''} {('GST: ' + gst_number) if gst_number else ''}</div>
        <div class="line"></div>
        <div class="row"><span>Bill No:</span><span>{bill_no}</span></div>
        <div class="row"><span>Date:</span><span>{bill_date}</span></div>
        <div class="row"><span>Customer:</span><span>{cust_name or 'Walk-in'}</span></div>
        <div class="row"><span>Payment:</span><span>{pay_mode}</span></div>
        <div class="line"></div>
        {rows_html}
        <div class="line"></div>
        <div class="row"><span>Subtotal</span><span>₹{subtotal:.2f}</span></div>
        <div class="row"><span>Discount</span><span>₹{discount:.2f}</span></div>
        <div class="row"><span>GST</span><span>₹{gst_total:.2f}</span></div>
        <div class="line"></div>
        <div class="row bold big"><span>Grand Total</span><span>₹{grand_total:.2f}</span></div>
        {split_html}
        <div class="line"></div>
        <div class="center">{footer_message or 'Thank You, Visit Again!'}</div>
    </body></html>"""


def build_a4_invoice_html(shop_name, address, mobile, gst_number, bill_no, bill_date,
                           cust_name, cust_mobile, pay_mode, cart_items,
                           subtotal, discount, gst_total, grand_total, footer_message, terms,
                           cash_amount=0.0, upi_amount=0.0):
    item_rows = "".join(
        f"<tr><td>{item['name']}</td><td class='c'>{item['qty']}</td>"
        f"<td class='r'>₹{item['selling_price']:.2f}</td><td class='r'>₹{item['total']:.2f}</td></tr>"
        for item in cart_items
    )
    split_html = ""
    if pay_mode == "Mixed":
        split_html = (
            f'<div><span>Cash Paid</span><span>₹{cash_amount:.2f}</span></div>'
            f'<div><span>UPI Paid</span><span>₹{upi_amount:.2f}</span></div>'
        )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
        @page {{ size: A4; margin: 15mm; }}
        * {{ box-sizing: border-box; }}
        body {{ font-family: Arial, sans-serif; color: #222; }}
        .header {{ text-align: center; border-bottom: 3px solid #0072ff; padding-bottom: 10px; margin-bottom: 16px; }}
        .header h1 {{ margin: 0; font-size: 24px; color: #2b2d42; }}
        .header p {{ margin: 3px 0; color: #555; }}
        .meta {{ display: flex; justify-content: space-between; margin-bottom: 16px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; }}
        th {{ background: #0072ff; color: white; text-align: left; padding: 8px; }}
        td {{ padding: 8px; border-bottom: 1px solid #eee; }}
        td.c {{ text-align: center; }} td.r, th.r {{ text-align: right; }}
        .totals {{ width: 45%; margin-left: auto; }}
        .totals div {{ display: flex; justify-content: space-between; padding: 4px 0; }}
        .totals .grand {{ font-size: 18px; font-weight: bold; border-top: 2px solid #333; padding-top: 8px; margin-top: 4px; }}
        .footer {{ margin-top: 24px; text-align: center; color: #555; }}
        .terms {{ margin-top: 16px; font-size: 11px; color: #777; }}
    </style></head>
    <body>
        <div class="header">
            <h1>{shop_name or 'Retail Shop'}</h1>
            <p>{address or ''}</p>
            <p>{('Mobile: ' + mobile) if mobile else ''} {('  |  GSTIN: ' + gst_number) if gst_number else ''}</p>
        </div>
        <div class="meta">
            <div><b>Invoice No:</b> {bill_no}<br><b>Date:</b> {bill_date}<br><b>Payment Mode:</b> {pay_mode}</div>
            <div style="text-align:right;"><b>Bill To:</b><br>{cust_name or 'Walk-in Customer'}<br>
                {('Mobile: ' + cust_mobile) if cust_mobile else ''}</div>
        </div>
        <table>
            <tr><th>Item</th><th class="c">Qty</th><th class="r">Rate</th><th class="r">Total</th></tr>
            {item_rows}
        </table>
        <div class="totals">
            <div><span>Subtotal</span><span>₹{subtotal:.2f}</span></div>
            <div><span>Discount</span><span>₹{discount:.2f}</span></div>
            <div><span>GST</span><span>₹{gst_total:.2f}</span></div>
            <div class="grand"><span>Grand Total</span><span>₹{grand_total:.2f}</span></div>
            {split_html}
        </div>
        <div class="footer">{footer_message or 'Thank You, Visit Again!'}</div>
        {f'<div class="terms">Terms &amp; Conditions: {terms}</div>' if terms else ''}
    </body></html>"""


def build_a5_invoice_html(shop_name, address, mobile, gst_number, bill_no, bill_date,
                           cust_name, cust_mobile, pay_mode, cart_items,
                           subtotal, discount, gst_total, grand_total, footer_message, terms,
                           cash_amount=0.0, upi_amount=0.0):
    now_time = datetime.now().strftime("%I:%M %p")
    item_rows = "".join(
        f"<tr><td>{item['name']}</td><td class='c'>{item['qty']}</td>"
        f"<td class='r'>₹{item['selling_price']:.2f}</td><td class='r'>₹{item['total']:.2f}</td></tr>"
        for item in cart_items
    )
    split_rows = ""
    if pay_mode == "Mixed":
        split_rows = (
            f"<div><span>Cash Amount</span><span>₹{cash_amount:.2f}</span></div>"
            f"<div><span>UPI Amount</span><span>₹{upi_amount:.2f}</span></div>"
        )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
        @page {{ size: A5 portrait; margin: 8mm; }}
        * {{ box-sizing: border-box; }}
        body {{ font-family: Arial, sans-serif; color:#222; font-size: 10.5px; }}
        .header {{ text-align: center; border-bottom: 2px solid #0072ff; padding-bottom: 6px; margin-bottom: 10px; }}
        .header h1 {{ margin: 0; font-size: 16px; color: #2b2d42; }}
        .header p {{ margin: 2px 0; color: #555; font-size: 9.5px; }}
        .meta {{ display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 9.5px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 9.5px; }}
        th {{ background: #0072ff; color: white; text-align: left; padding: 4px 6px; }}
        td {{ padding: 4px 6px; border-bottom: 1px solid #eee; }}
        td.c {{ text-align: center; }} td.r, th.r {{ text-align: right; }}
        .totals {{ width: 60%; margin-left: auto; font-size: 9.5px; }}
        .totals div {{ display: flex; justify-content: space-between; padding: 2px 0; }}
        .totals .grand {{ font-size: 12px; font-weight: bold; border-top: 1.5px solid #333; padding-top: 5px; margin-top: 3px; }}
        .footer {{ margin-top: 16px; text-align: center; color: #555; font-size: 9.5px; }}
        .terms {{ margin-top: 10px; font-size: 8.5px; color: #777; border-top: 1px solid #eee; padding-top: 6px; }}
    </style></head>
    <body>
        <div class="header">
            <h1>{shop_name or 'Retail Shop'}</h1>
            <p>{address or ''}</p>
            <p>{('Mobile: ' + mobile) if mobile else ''} {('  |  GSTIN: ' + gst_number) if gst_number else ''}</p>
        </div>
        <div class="meta">
            <div><b>Invoice No:</b> {bill_no}<br><b>Date:</b> {bill_date}  <b>Time:</b> {now_time}<br><b>Payment Mode:</b> {pay_mode}</div>
            <div style="text-align:right;"><b>Customer:</b> {cust_name or 'Walk-in Customer'}<br>
                {('Mobile: ' + cust_mobile) if cust_mobile else ''}</div>
        </div>
        <table>
            <tr><th>Item</th><th class="c">Qty</th><th class="r">Rate</th><th class="r">Total</th></tr>
            {item_rows}
        </table>
        <div class="totals">
            <div><span>Subtotal</span><span>₹{subtotal:.2f}</span></div>
            <div><span>Discount</span><span>₹{discount:.2f}</span></div>
            <div><span>Tax (GST)</span><span>₹{gst_total:.2f}</span></div>
            <div class="grand"><span>Total Amount</span><span>₹{grand_total:.2f}</span></div>
            {split_rows}
        </div>
        <div class="footer">{footer_message or 'Thank You, Visit Again!'}</div>
        {f'<div class="terms">Terms &amp; Conditions: {terms}</div>' if terms else ''}
    </body></html>"""
