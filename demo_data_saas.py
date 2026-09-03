"""
demo_data_saas.py — Seeds a realistic, ready-to-demo dataset for one tenant.

Exactly 25 records total, spread across every module so a fresh demo
shows a full Dashboard/Reports instead of a wall of zeros:
    8 products, 2 suppliers, 3 customers, 3 purchases, 6 sales, 3 expenses
    = 25 records

This mirrors the local app's load_demo_data()/clear_all_business_data()
pair (Phase 13 of the analysis: "these already do exactly what a demo
tenant needs — adapt them to a fixed tenant_id"). Safe to call multiple
times against a fresh tenant only — it does not check for/skip existing
data, so call clear_tenant_business_data() first if re-seeding.
"""

import secrets
from datetime import date, timedelta
from sqlalchemy import text

from database_saas import get_engine
import pos_saas as pos


def seed_demo_data(tenant_id: str) -> int:
    engine = get_engine()
    today = date.today()
    count = 0

    with engine.begin() as conn:
        # ---- 8 Products ----
        products = [
            ("Rice 5kg", "Grocery", "India Gate", "Kg", 220, 260, 5, 40, 10, 0, "DEMO0001"),
            ("Sunflower Oil 1L", "Grocery", "Fortune", "Litre", 130, 150, 5, 25, 8, 0, "DEMO0002"),
            ("Kurti", "Clothing", "Generic", "Pcs", 300, 500, 5, 15, 3, 20, "DEMO0003"),
            ("Shirt", "Clothing", "Generic", "Pcs", 250, 450, 5, 20, 5, 0, "DEMO0004"),
            ("Jeans", "Clothing", "Generic", "Pcs", 400, 700, 5, 10, 2, 50, "DEMO0005"),
            ("Notebook", "Stationery", "Generic", "Pcs", 15, 25, 0, 100, 20, 0, "DEMO0006"),
            ("Toothpaste", "Personal Care", "Colgate", "Pcs", 60, 85, 12, 30, 10, 0, "DEMO0007"),
            ("Biscuits Pack", "Grocery", "Parle", "Pcs", 20, 30, 5, 60, 15, 0, "DEMO0008"),
        ]
        product_ids = {}
        for name, cat, brand, unit, pp, sp, gst, stock, minstock, disc, bc in products:
            row = conn.execute(text("""
                INSERT INTO products (tenant_id, name, barcode, category, brand, unit,
                                       purchase_price, selling_price, gst, opening_stock,
                                       minimum_stock, default_discount, is_active)
                VALUES (:tid,:name,:bc,:cat,:brand,:unit,:pp,:sp,:gst,:stock,:minstock,:disc,TRUE)
                RETURNING id
            """), {"tid": tenant_id, "name": name, "bc": bc, "cat": cat, "brand": brand, "unit": unit,
                   "pp": pp, "sp": sp, "gst": gst, "stock": stock, "minstock": minstock, "disc": disc}).fetchone()
            product_ids[name] = row[0]
            count += 1

        # ---- 2 Suppliers ----
        suppliers = [
            ("ABC Traders", "9990000010", "APMC Market, City", "27ABCDE0001A1Z5"),
            ("Patel Wholesale", "9990000011", "Main Bazaar, City", "27PQRST0002A1Z5"),
        ]
        supplier_ids = {}
        for name, mob, addr, gstn in suppliers:
            row = conn.execute(text("""
                INSERT INTO suppliers (tenant_id, name, mobile, address, gst_number, is_active)
                VALUES (:tid,:name,:mob,:addr,:gstn,TRUE) RETURNING id
            """), {"tid": tenant_id, "name": name, "mob": mob, "addr": addr, "gstn": gstn}).fetchone()
            supplier_ids[name] = row[0]
            count += 1

        # ---- 3 Customers ----
        customers = [
            ("Suresh Kumar", "9990000020", "Gandhi Nagar, City"),
            ("Priya Sharma", "9990000021", "Station Road, City"),
            ("Amit Patel", "9990000022", "Ring Road, City"),
        ]
        for name, mob, addr in customers:
            conn.execute(text("""
                INSERT INTO customers (tenant_id, name, mobile, address, is_active)
                VALUES (:tid,:name,:mob,:addr,TRUE) ON CONFLICT (tenant_id, mobile) DO NOTHING
            """), {"tid": tenant_id, "name": name, "mob": mob, "addr": addr})
            count += 1

        # ---- 3 Purchases (with Cash/UPI split payments) ----
        purchases = [
            ("Rice 5kg", "ABC Traders", 40, 220, today - timedelta(days=6), 9152.0, 0.0),
            ("Kurti", "Patel Wholesale", 15, 300, today - timedelta(days=3), 3000.0, 1725.0),
            ("Jeans", "Patel Wholesale", 10, 400, today, 2000.0, 2200.0),
        ]
        for pname, sname, qty, pp, pdate, cash_paid, upi_paid in purchases:
            pid, sid = product_ids[pname], supplier_ids[sname]
            base = qty * pp
            total_amount = round(base * 1.05, 2)
            paid = min(cash_paid + upi_paid, total_amount)
            row = conn.execute(text("""
                INSERT INTO purchases (tenant_id, purchase_date, supplier_id, product_id, quantity,
                                        purchase_price, discount, gst, transport, total_amount, paid_amount)
                VALUES (:tid,:pd,:sid,:pid,:qty,:pp,0,5,0,:total,:paid) RETURNING id
            """), {"tid": tenant_id, "pd": pdate, "sid": sid, "pid": pid, "qty": qty, "pp": pp,
                   "total": total_amount, "paid": paid}).fetchone()
            purchase_id = row[0]
            if paid > 0:
                conn.execute(text("""
                    INSERT INTO supplier_payments (tenant_id, purchase_id, supplier_id, payment_date, cash_amount, upi_amount, total_amount)
                    VALUES (:tid,:pid,:sid,:pd,:cash,:upi,:total)
                """), {"tid": tenant_id, "pid": purchase_id, "sid": sid, "pd": pdate,
                       "cash": min(cash_paid, paid), "upi": min(upi_paid, paid), "total": paid})
            count += 1

        # ---- 6 Sales, across dates and every payment mode ----
        sales = [
            (today, "Suresh Kumar", "9990000020", "Cash", [("Rice 5kg", 2), ("Notebook", 3)]),
            (today, "Walk-in Customer", "0000000000", "UPI", [("Sunflower Oil 1L", 1), ("Shirt", 1)]),
            (today - timedelta(days=1), "Priya Sharma", "9990000021", "Mixed", [("Kurti", 1), ("Jeans", 1)]),
            (today - timedelta(days=2), "Walk-in Customer", "0000000000", "Card", [("Shirt", 2)]),
            (today - timedelta(days=3), "Amit Patel", "9990000022", "Cash", [("Toothpaste", 2), ("Biscuits Pack", 3)]),
            (today - timedelta(days=4), "Suresh Kumar", "9990000020", "UPI", [("Rice 5kg", 1)]),
        ]
        for bdate, cname, cmob, pmode, items in sales:
            subtotal, gst_total, item_rows = 0.0, 0.0, []
            for pname, qty in items:
                prow = conn.execute(text("SELECT selling_price, gst FROM products WHERE tenant_id=:t AND id=:p"),
                                     {"t": tenant_id, "p": product_ids[pname]}).fetchone()
                line_total = round(qty * float(prow[0]), 2)
                subtotal += line_total
                gst_total += line_total * float(prow[1]) / 100
                item_rows.append((product_ids[pname], qty, prow[0], line_total))
            grand_total = round(subtotal + gst_total, 2)

            if pmode == "Cash":
                cash_amt, upi_amt = grand_total, 0.0
            elif pmode == "UPI":
                cash_amt, upi_amt = 0.0, grand_total
            elif pmode == "Mixed":
                cash_amt, upi_amt = round(grand_total * 0.6, 2), round(grand_total * 0.4, 2)
            else:
                cash_amt, upi_amt = 0.0, 0.0

            # Random suffix (not just date+count) so this never collides with
            # UNIQUE(tenant_id, bill_number) if seed_demo_data() ever runs
            # twice for the same tenant on the same day without a clear in
            # between (e.g. clicking "Add Demo Data" again from Settings).
            bill_no = f"DEMO-{bdate.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
            row = conn.execute(text("""
                INSERT INTO sales (tenant_id, bill_number, bill_date, customer_name, customer_mobile,
                                    payment_mode, subtotal, discount, gst, grand_total, cash_amount, upi_amount)
                VALUES (:tid,:bn,:bd,:cn,:cm,:pm,:sub,0,:gst,:gt,:cash,:upi) RETURNING id
            """), {"tid": tenant_id, "bn": bill_no, "bd": bdate, "cn": cname, "cm": cmob, "pm": pmode,
                   "sub": round(subtotal, 2), "gst": round(gst_total, 2), "gt": grand_total,
                   "cash": cash_amt, "upi": upi_amt}).fetchone()
            sale_id = row[0]
            for pid, qty, price, total in item_rows:
                conn.execute(text("""
                    INSERT INTO sale_items (tenant_id, sale_id, product_id, quantity, selling_price, total)
                    VALUES (:tid,:sid,:pid,:qty,:price,:total)
                """), {"tid": tenant_id, "sid": sale_id, "pid": pid, "qty": qty, "price": price, "total": total})
                conn.execute(text("UPDATE products SET opening_stock = GREATEST(opening_stock - :qty, 0) WHERE tenant_id=:tid AND id=:pid"),
                             {"qty": qty, "tid": tenant_id, "pid": pid})
            count += 1

        # ---- 3 Expenses ----
        expenses = [
            (today, "Rent", 8000, "Monthly shop rent"),
            (today - timedelta(days=2), "Electricity", 2200, "Electricity bill"),
            (today - timedelta(days=5), "Salary", 12000, "Staff salary"),
        ]
        for edate, etype, amt, remarks in expenses:
            conn.execute(text("""
                INSERT INTO expenses (tenant_id, expense_date, expense_type, amount, remarks)
                VALUES (:tid,:d,:type,:amt,:remarks)
            """), {"tid": tenant_id, "d": edate, "type": etype, "amt": amt, "remarks": remarks})
            count += 1

    return count


def clear_tenant_business_data(tenant_id: str):
    """Wipes every business table for ONE tenant (tenants/subscriptions/
    users/shop_settings untouched) — use before re-seeding, or before
    handing a demo-loaded install to a real customer."""
    engine = get_engine()
    tables = ["sale_items", "sales", "supplier_payments", "purchases",
              "expenses", "customers", "suppliers", "products"]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id = :tid"), {"tid": tenant_id})
