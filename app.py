"""
app_saas.py — SaaS entry point (Streamlit).

Deliberately kept "thin" per SaaS_Migration_Analysis.md's recommended
project structure (Section R): this file is UI/routing/session-state
only. Every actual database operation lives in — and was already
tested against real PostgreSQL in — the *_saas.py modules:
database_saas, pos_saas, products_saas, suppliers_saas, customers_saas,
expenses_saas, stock_purchase_saas, reports_saas.

Flow:
    Login/Signup (tenant-scoped)
      -> Subscription Validation (no free trial)
      -> Role/Permission Validation (unchanged from the local app)
      -> Page routing (same PAGE_PERMISSIONS/page_allowed as before)
"""

import streamlit as st
import pandas as pd
from datetime import date

import database_saas as db
import pos_saas as pos
import products_saas as prod
import suppliers_saas as sup
import customers_saas as cust
import expenses_saas as exp
import stock_purchase_saas as spur
import reports_saas as rep
import barcode_saas as bc
import gsheet_saas as gs
import receipts_saas as rcpt
import ocr_saas as ocr
import bulk_import_export_saas as bulk
import demo_data_saas as demo

st.set_page_config(page_title="Retail Shop SaaS", page_icon="🧾", layout="wide")

# ---------------------------------------------------------------------------
# Colorful, professional theme — same visual language as the local app
# (gradient nav tiles, metric cards, polished buttons). Pure CSS, no
# functional change, ported over verbatim from app.py's styling blocks.
# ---------------------------------------------------------------------------
st.markdown("""
    <style>
    .stApp { background-color: #f7f9fc; }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #2b2d42 0%, #1a1c2e 100%);
    }
    section[data-testid="stSidebar"] * { color: #f0f0f5 !important; }
    section[data-testid="stSidebar"] div.stButton > button {
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.15);
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.15s ease;
    }
    section[data-testid="stSidebar"] div.stButton > button:hover {
        background: linear-gradient(135deg, #00c6ff, #0072ff);
        border-color: transparent;
        transform: translateX(3px);
    }
    div[data-testid="stMetric"] {
        background: white;
        border-radius: 14px;
        padding: 14px 18px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
        border-left: 4px solid #0072ff;
    }
    div.stButton > button[kind="primary"], div.stButton > button {
        border-radius: 10px;
        font-weight: 600;
    }
    div.stButton > button:not([kind="secondary"]):hover {
        border-color: #0072ff;
        color: #0072ff;
    }
    h1, h2, h3 { color: #2b2d42; }
    div[data-testid="stForm"] {
        background: white;
        border-radius: 14px;
        padding: 20px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    }
    /* Mobile/tablet touch-friendly tuning (same as the local app) */
    @media (max-width: 900px) {
        div.stButton > button { min-height: 44px; font-size: 15px; }
        input[type="number"], input[type="text"], input[type="password"] {
            min-height: 40px; font-size: 16px;
        }
    }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Roles & Permissions — UNCHANGED from the local app, per the analysis's own
# recommendation (Section H: no strong reason found to change this model).
# ---------------------------------------------------------------------------
ROLES = ["Admin", "Manager", "Cashier"]
PAGE_PERMISSIONS = {
    "Dashboard": ["Admin", "Manager", "Cashier"],
    "Billing System (POS)": ["Admin", "Manager", "Cashier"],
    "Product Master": ["Admin", "Manager"],
    "Supplier Management": ["Admin", "Manager"],
    "Customer Management": ["Admin", "Manager", "Cashier"],
    "Stock Purchase": ["Admin", "Manager"],
    "Expense Management": ["Admin", "Manager"],
    "Reports Hub": ["Admin", "Manager"],
    "Settings": ["Admin"],
}


def page_allowed(page: str, role: str) -> bool:
    return role in PAGE_PERMISSIONS.get(page, [])


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
for key, default in [
    ("logged_in", False), ("tenant_id", None), ("username", ""), ("role", ""),
    ("current_page", "Dashboard"), ("cart", []), ("confirm_delete", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# Database connectivity check — fail fast with ONE actionable message
# instead of a redacted OperationalError surfacing later inside a form
# (e.g. Signup), which gives the admin no clue that DATABASE_URL is
# missing/unreachable.
# ---------------------------------------------------------------------------
_db_ok, _db_error = db.check_connection()
if not _db_ok:
    st.error(
        "⚠️ Database se connect nahi ho paaya. Agar yeh app Streamlit Cloud "
        "par deployed hai, to **Manage app → Settings → Secrets** mein "
        "`DATABASE_URL = \"postgresql://user:password@host:5432/dbname\"` "
        "add karein aur app ko reboot karein."
    )
    st.caption(f"Details: {_db_error}")
    st.stop()


# ---------------------------------------------------------------------------
# Tenant resolution + Login screen
#
# A shop is identified by its "Shop Code" (the tenant's installation_id --
# short, human-typeable, distinct from the internal UUID) at login time.
# This is what makes "SELECT ... WHERE username = ?" safe again: the
# tenant is always known BEFORE the username lookup ever runs.
# ---------------------------------------------------------------------------
def resolve_tenant_by_code(shop_code: str):
    with db.get_engine().connect() as conn:
        from sqlalchemy import text
        row = conn.execute(
            text("SELECT id, shop_name FROM tenants WHERE installation_id = :code"),
            {"code": shop_code.strip()},
        ).fetchone()
        return (str(row[0]), row[1]) if row else (None, None)


def login_screen():
    st.markdown("<h1 style='text-align:center;'>🔑 Login</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("login_form"):
            shop_code = st.text_input("Shop Code", help="Aapki shop ka unique code — signup ke waqt mila tha")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)

            if submitted:
                tenant_id, shop_name = resolve_tenant_by_code(shop_code)
                if not tenant_id:
                    st.error("Shop Code galat hai.")
                    return

                active, status, msg = db.is_subscription_active(tenant_id)
                if not active:
                    st.error(f"⚠️ {msg}")
                    return

                user = db.check_login(tenant_id, username, password)
                if user:
                    st.session_state.logged_in = True
                    st.session_state.tenant_id = tenant_id
                    st.session_state.shop_name = shop_name
                    st.session_state.username = user["username"]
                    st.session_state.role = user["role"]
                    st.rerun()
                else:
                    st.error("Username ya Password galat hai.")

        st.caption("Naya shop hai? Neeche Signup karein.")
        if st.button("📝 New Shop Signup", use_container_width=True):
            st.session_state["show_signup"] = True
            st.rerun()


def signup_screen():
    st.markdown("<h1 style='text-align:center;'>📝 New Shop Signup</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("signup_form"):
            shop_name = st.text_input("Shop Name")
            admin_username = st.text_input("Admin Username")
            admin_password = st.text_input("Admin Password", type="password")
            submitted = st.form_submit_button("Create Shop", use_container_width=True)

            if submitted:
                if not shop_name.strip() or not admin_username.strip() or len(admin_password) < 4:
                    st.error("Shop Name, Username bharein, Password kam se kam 4 characters ka ho.")
                    return
                tenant_id = db.create_tenant(shop_name.strip())
                ok, msg = db.register_user(tenant_id, admin_username.strip(), admin_password, "Admin")
                if not ok:
                    st.error(msg)
                    return

                with db.get_engine().connect() as conn:
                    from sqlalchemy import text
                    shop_code = conn.execute(
                        text("SELECT installation_id FROM tenants WHERE id = :tid"), {"tid": tenant_id}
                    ).fetchone()[0]

                st.success(
                    f"Shop ban gayi! Aapka **Shop Code** hai: `{shop_code}` — ise safe rakhein, "
                    f"login karte waqt chahiye hoga."
                )
                st.info("No subscription is active yet — contact billing/admin to activate a plan before logging in.")
                st.session_state["show_signup"] = False

        if st.button("⬅ Back to Login", use_container_width=True):
            st.session_state["show_signup"] = False
            st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar():
    st.sidebar.markdown(f"### 🏪 {st.session_state.get('shop_name', '')}")
    st.sidebar.markdown(f"**User:** {st.session_state.username}")
    st.sidebar.markdown(
        f"**Role:** <span style='background:#0072ff; padding:2px 10px; border-radius:6px; font-weight:600;'>{st.session_state.role}</span>",
        unsafe_allow_html=True
    )
    st.sidebar.markdown("---")

    for page in PAGE_PERMISSIONS:
        if page_allowed(page, st.session_state.role):
            if st.sidebar.button(page, use_container_width=True, key=f"nav_{page}"):
                st.session_state.current_page = page
                st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Logout", use_container_width=True):
        for key in ("logged_in", "tenant_id", "username", "role", "cart"):
            st.session_state[key] = False if key == "logged_in" else ("" if key in ("username", "role") else ([] if key == "cart" else None))
        st.rerun()


# ---------------------------------------------------------------------------
# Page renderers — thin wrappers calling the already-tested *_saas modules.
# Every one of them reads tenant_id from session_state ONCE at the top and
# threads it through every call below — never re-derived, never trusted
# from any other source.
# ---------------------------------------------------------------------------
def render_dashboard():
    tenant_id = st.session_state.tenant_id
    st.header("📊 Dashboard")
    today = date.today()

    products_df = prod.get_all_products(tenant_id)
    suppliers_df = sup.get_all_suppliers(tenant_id)
    customers_df = cust.get_all_customers(tenant_id)

    tile_colors = ["#0072ff, #00c6ff", "#f7971e, #ffd200", "#8e2de2, #4a00e0", "#11998e, #38ef7d"]
    tiles = [
        ("📦 Products", len(products_df)),
        ("🏭 Suppliers", len(suppliers_df)),
        ("👥 Customers", len(customers_df)),
    ]
    cols = st.columns(len(tiles))
    for col, (label, value), grad in zip(cols, tiles, tile_colors):
        col.markdown(f"""
            <div style="background: linear-gradient(135deg, {grad}); border-radius: 14px;
                        padding: 16px 20px; color: white; box-shadow: 0 4px 10px rgba(0,0,0,0.15);">
                <div style="font-size: 13px; opacity: 0.9;">{label}</div>
                <div style="font-size: 26px; font-weight: 700;">{value}</div>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    pl = rep.get_profit_loss(tenant_id, today, today)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Today's Sales", f"₹ {pl['sales']:,.2f}")
    c2.metric("📥 Today's Purchases", f"₹ {pl['purchases']:,.2f}")
    c3.metric("💸 Today's Expenses", f"₹ {pl['expenses']:,.2f}")
    c4.metric("📈 Net (Today)", f"₹ {pl['net_profit']:,.2f}")

    low_stock = products_df[(products_df["is_active"]) & (products_df["opening_stock"] <= products_df["minimum_stock"])]
    if not low_stock.empty:
        st.warning(f"⚠️ {len(low_stock)} product(s) low on stock")
        st.dataframe(low_stock[["name", "opening_stock", "minimum_stock"]], use_container_width=True, hide_index=True)


def render_pos_page():
    tenant_id = st.session_state.tenant_id
    st.header("🧾 Billing System (POS)")

    if st.session_state.get("last_saved_bill"):
        saved = st.session_state["last_saved_bill"]
        st.success(f"✅ Bill Saved: {saved['bill_no']}")
        st.caption("Jiske paas jo bhi printer ho, wahi option use karein:")
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            st.components.v1.html(bc.render_print_button_html(saved["html_80mm"], "p80", "🖨️ 80mm Thermal"), height=55)
        with pc2:
            st.components.v1.html(bc.render_print_button_html(saved["html_a4"], "pa4", "🖨️ A4 Invoice"), height=55)
        with pc3:
            st.components.v1.html(bc.render_print_button_html(saved["html_a5"], "pa5", "🖨️ A5 Invoice"), height=55)
        if st.button("➕ Naya Bill Shuru Karein"):
            st.session_state.pop("last_saved_bill", None)
            st.rerun()
        st.markdown("---")

    products_df = pos.fetch_active_products(tenant_id)
    if products_df.empty:
        st.warning("Koi product nahi hai — pehle Product Master me add karein.")
        return

    barcode = st.text_input("📷 Barcode Scan Karein", key="barcode_scan_box")
    if barcode:
        found = pos.scan_barcode(tenant_id, barcode)
        if found:
            existing = next((i for i in st.session_state.cart if i["product_id"] == str(found["id"])), None)
            if existing:
                existing["qty"] += 1
                existing["total"] = round(existing["qty"] * existing["selling_price"], 2)
            else:
                st.session_state.cart.append({
                    "product_id": str(found["id"]), "name": found["name"],
                    "selling_price": float(found["selling_price"]), "qty": 1,
                    "gst": float(found["gst"]), "total": float(found["selling_price"]),
                })
            st.success(f"Added: {found['name']}")
        else:
            st.error("Barcode is shop me nahi mila.")

    sel_name = st.selectbox("Ya Product Search Karein", products_df["name"].tolist())
    sel_row = products_df[products_df["name"] == sel_name].iloc[0]
    qty = st.number_input("Quantity", min_value=0.01, value=1.0, step=1.0)
    if st.button("➕ Add to Cart"):
        existing = next((i for i in st.session_state.cart if i["product_id"] == str(sel_row["id"])), None)
        if existing:
            existing["qty"] += qty
            existing["total"] = round(existing["qty"] * existing["selling_price"], 2)
        else:
            st.session_state.cart.append({
                "product_id": str(sel_row["id"]), "name": sel_row["name"],
                "selling_price": float(sel_row["selling_price"]), "qty": qty,
                "gst": float(sel_row["gst"]), "total": round(qty * float(sel_row["selling_price"]), 2),
            })
        st.rerun()

    if st.session_state.cart:
        st.markdown("---")
        cart_df = pd.DataFrame(st.session_state.cart)
        st.dataframe(cart_df[["name", "qty", "selling_price", "total"]], use_container_width=True, hide_index=True)

        cust_name = st.text_input("Customer Name", value="Walk-in Customer")
        cust_mobile = st.text_input("Customer Mobile")
        pay_mode = st.selectbox("Payment Mode", ["Cash", "UPI", "Card", "Mixed"])
        cash_amount, upi_amount = 0.0, 0.0
        if pay_mode == "Mixed":
            cash_amount = st.number_input("Cash Amount", min_value=0.0)
            upi_amount = st.number_input("UPI Amount", min_value=0.0)

        if st.button("💾 Save Bill", use_container_width=True):
            ok, result = pos.save_sale(tenant_id, cust_name, cust_mobile, pay_mode,
                                        st.session_state.cart, 0, cash_amount, upi_amount)
            if ok:
                gcfg = gs.get_gsheet_config(tenant_id)
                if gcfg["gsheet_enabled"]:
                    gs.sync_today_to_gsheet(tenant_id)

                settings_row = db.get_settings(tenant_id) or {}
                subtotal = sum(i["total"] for i in st.session_state.cart)
                gst_total = sum(i["total"] * i["gst"] / 100 for i in st.session_state.cart)
                grand_total = round(subtotal + gst_total, 2)
                common_args = dict(
                    shop_name=st.session_state.get("shop_name", ""),
                    address=settings_row.get("address", "") or "",
                    mobile=settings_row.get("mobile", "") or "",
                    gst_number=settings_row.get("gst_number", "") or "",
                    bill_no=result, bill_date=date.today().isoformat(),
                    cust_name=cust_name, cust_mobile=cust_mobile, pay_mode=pay_mode,
                    cart_items=st.session_state.cart, subtotal=subtotal, discount=0,
                    gst_total=gst_total, grand_total=grand_total,
                    footer_message=settings_row.get("footer_message", "") or "",
                    cash_amount=cash_amount, upi_amount=upi_amount,
                )
                st.session_state["last_saved_bill"] = {
                    "bill_no": result,
                    "html_80mm": rcpt.build_80mm_receipt_html(**common_args),
                    "html_a4": rcpt.build_a4_invoice_html(**common_args, terms=settings_row.get("terms", "") or ""),
                    "html_a5": rcpt.build_a5_invoice_html(**common_args, terms=settings_row.get("terms", "") or ""),
                }
                st.session_state.cart = []
                st.rerun()
            else:
                st.error(result)


def render_product_master_page():
    tenant_id = st.session_state.tenant_id
    st.header("📦 Product Master")
    tab1, tab2, tab3, tab4 = st.tabs(["➕ Add Product", "✏️ View/Edit/Delete", "🏷️ Barcode Labels", "📊 Bulk Import/Export"])

    with tab1:
        with st.form("add_product_form"):
            name = st.text_input("Product Name")
            barcode = st.text_input("Barcode (optional)")
            c1, c2, c3 = st.columns(3)
            purchase_price = c1.number_input("Purchase Price", min_value=0.0)
            selling_price = c2.number_input("Selling Price", min_value=0.0)
            gst = c3.number_input("GST %", min_value=0.0, value=5.0)
            opening_stock = st.number_input("Opening Stock", min_value=0.0)
            if st.form_submit_button("Save Product"):
                ok, result = prod.add_product(tenant_id, name, barcode or None,
                                                purchase_price=purchase_price,
                                                selling_price=selling_price, gst=gst,
                                                opening_stock=opening_stock)
                if ok:
                    st.success("Product added!")
                    st.rerun()
                else:
                    st.error(result)

    with tab2:
        all_products = prod.get_all_products(tenant_id)
        if all_products.empty:
            st.info("Koi product nahi hai.")
        else:
            st.dataframe(all_products, use_container_width=True, hide_index=True)
            sel_id = st.selectbox("Select Product to Delete", all_products["id"].astype(str).tolist())
            if st.button("🗑️ Delete Selected Product"):
                action, msg = prod.delete_or_deactivate_product(tenant_id, sel_id)
                st.success(msg) if action != "not_found" else st.error(msg)
                st.rerun()

    with tab3:
        all_products = prod.get_all_products(tenant_id)
        if all_products.empty:
            st.info("Koi product nahi hai.")
        elif not bc.BARCODE_LIB_AVAILABLE:
            st.warning("Barcode library install nahi hai.")
        else:
            sel_name = st.selectbox("Product Select Karein", all_products["name"].tolist(), key="lbl_prod")
            copies = st.number_input("Kitne Labels", min_value=1, value=1, step=1)
            label_size = st.selectbox("Label Size", list(bc.LABEL_SIZE_PRESETS.keys()), index=1)

            if st.button("🖨️ Generate & Print Labels"):
                row = all_products[all_products["name"] == sel_name].iloc[0]
                product_id = str(row["id"])
                code_value = prod.assign_auto_barcode_if_missing(tenant_id, product_id)

                settings_row = db.get_settings(tenant_id) or {}
                img_b64 = bc.generate_barcode_png_base64(code_value)
                sheet_html = bc.build_barcode_label_sheet_html(
                    [{"name": row["name"], "price": float(row["selling_price"]), "barcode": code_value,
                      "copies": int(copies), "img_b64": img_b64}],
                    shop_name=st.session_state.get("shop_name", ""),
                    shop_address=settings_row.get("address", "") or "",
                    shop_mobile=settings_row.get("mobile", "") or "",
                    label_size_key=label_size,
                )
                st.components.v1.html(
                    bc.render_print_button_html(sheet_html, key="print_labels", label="🖨️ Print Ab Shuru Karein"),
                    height=60,
                )
                st.success(f"Barcode ready: {code_value} — upar wale button se print karein.")

    with tab4:
        st.markdown("##### ⬇️ Export")
        ec1, ec2 = st.columns(2)
        with ec1:
            st.download_button("⬇️ Current Catalog (Excel)", bulk.export_products_to_excel(tenant_id),
                                file_name="products_export.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with ec2:
            st.download_button("⬇️ Blank Template (Excel)", bulk.get_blank_template_excel(),
                                file_name="products_template.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.markdown("##### ⬆️ Import")
        uploaded = st.file_uploader("Excel Upload Karein", type=["xlsx", "xls"])
        if uploaded is not None and st.button("Import Karein"):
            summary = bulk.import_products_from_excel(tenant_id, uploaded.read())
            st.success(f"Total: {summary['total']} | Imported: {summary['imported']} | Updated: {summary['updated']}")
            if summary["errors"]:
                st.warning("Kuch rows me dikkat aayi:")
                for err in summary["errors"]:
                    st.caption(f"⚠️ {err}")
            st.rerun()


def render_supplier_page():
    tenant_id = st.session_state.tenant_id
    st.header("🏭 Supplier Management")
    tab1, tab2 = st.tabs(["➕ Add / List", "💳 Ledger"])

    with tab1:
        with st.form("add_supplier_form"):
            name = st.text_input("Supplier Name")
            mobile = st.text_input("Mobile")
            if st.form_submit_button("Save Supplier"):
                ok, result = sup.add_supplier(tenant_id, name, mobile)
                st.success("Supplier added!") if ok else st.error(result)
                if ok:
                    st.rerun()
        st.dataframe(sup.get_all_suppliers(tenant_id), use_container_width=True, hide_index=True)

    with tab2:
        summary = sup.get_ledger_summary(tenant_id)
        if summary.empty:
            st.info("Koi supplier nahi hai.")
        else:
            st.dataframe(summary, use_container_width=True, hide_index=True)


def render_customer_page():
    tenant_id = st.session_state.tenant_id
    st.header("👥 Customer Management")
    with st.form("add_customer_form"):
        name = st.text_input("Customer Name")
        mobile = st.text_input("Mobile")
        if st.form_submit_button("Save Customer"):
            ok, result = cust.add_customer(tenant_id, name, mobile)
            st.success("Customer added!") if ok else st.error(result)
            if ok:
                st.rerun()
    st.dataframe(cust.get_all_customers(tenant_id), use_container_width=True, hide_index=True)


def render_stock_purchase_page():
    tenant_id = st.session_state.tenant_id
    st.header("📥 Stock Purchase")

    suppliers_df = sup.get_active_suppliers(tenant_id)
    products_df = prod.get_all_products(tenant_id)
    if suppliers_df.empty or products_df.empty:
        st.warning("Pehle ek Supplier aur ek Product add karein.")
        return

    st.markdown("##### 📷 Supplier Bill Photo (Optional)")
    st.caption(
        "Photo yahan attach kar sakte ho — OCR se text nikal ke dikha denge taaki numbers jaldi "
        "padh sako. Values yahan khud hi form me bharni hongi — automatic bharne se galti ka risk hota hai."
    )
    bill_photo = st.file_uploader("Bill Photo Upload Karein", type=["jpg", "jpeg", "png"], key="bill_photo_upload")
    if bill_photo is not None:
        st.image(bill_photo, caption="Uploaded Bill", width=300)
        if ocr.OCR_AVAILABLE:
            if st.button("🔍 Photo Se Text Nikalein (OCR)"):
                text_out, err = ocr.extract_text_from_bill_image(bill_photo)
                if err:
                    st.warning(err)
                else:
                    st.text_area("📝 OCR se nikla text (khud verify karke form me bharein)", value=text_out or "", height=150)
        else:
            st.info("OCR library install nahi hai — photo phir bhi save hogi.")

    with st.form("purchase_form"):
        sel_supplier = st.selectbox("Supplier", suppliers_df["name"].tolist())
        sel_product = st.selectbox("Product", products_df["name"].tolist())
        qty = st.number_input("Quantity", min_value=0.01, value=1.0)
        price = st.number_input("Purchase Price", min_value=0.0)
        cash_now = st.number_input("Cash Paid Now", min_value=0.0)
        upi_now = st.number_input("UPI Paid Now", min_value=0.0)
        if st.form_submit_button("Save Purchase"):
            sid = str(suppliers_df[suppliers_df["name"] == sel_supplier].iloc[0]["id"])
            pid = str(products_df[products_df["name"] == sel_product].iloc[0]["id"])
            # Note: actual photo bytes aren't persisted to disk here (Streamlit
            # Cloud's filesystem is ephemeral) -- wire bill_photo_ref up to your
            # object-storage upload (S3/Cloud Storage) and pass its URL/key here.
            ok, result = spur.save_purchase(tenant_id, sid, pid, qty, price, 0, 0, 0, cash_now, upi_now)
            st.success("Purchase saved!") if ok else st.error(result)
            if ok:
                st.rerun()

    st.dataframe(spur.get_purchase_history(tenant_id, date(2000, 1, 1), date.today()),
                 use_container_width=True, hide_index=True)


def render_expense_page():
    tenant_id = st.session_state.tenant_id
    st.header("💸 Expense Management")
    with st.form("add_expense_form"):
        etype = st.text_input("Expense Type")
        amount = st.number_input("Amount", min_value=0.0)
        if st.form_submit_button("Save Expense"):
            ok, result = exp.add_expense(tenant_id, date.today(), etype, amount)
            st.success("Expense saved!") if ok else st.error(result)
            if ok:
                st.rerun()
    st.dataframe(exp.get_expenses(tenant_id, date(2000, 1, 1), date.today()),
                 use_container_width=True, hide_index=True)


def render_reports_page():
    tenant_id = st.session_state.tenant_id
    st.header("📈 Reports Hub")
    tab1, tab2, tab3 = st.tabs(["💰 Sales", "📊 Profit & Loss", "🏆 Top Products"])

    with tab1:
        c1, c2 = st.columns(2)
        sd = c1.date_input("From", value=date.today().replace(day=1))
        ed = c2.date_input("To", value=date.today())
        sales_df = rep.get_sales_report(tenant_id, sd, ed)
        if not sales_df.empty:
            summary = rep.get_sales_payment_summary(sales_df)
            m1, m2, m3 = st.columns(3)
            m1.metric("Cash", f"₹{summary['cash']:,.2f}")
            m2.metric("UPI", f"₹{summary['upi']:,.2f}")
            m3.metric("Total", f"₹{summary['total']:,.2f}")
            st.dataframe(sales_df, use_container_width=True, hide_index=True)
        else:
            st.info("No sales in this range.")

    with tab2:
        c1, c2 = st.columns(2)
        sd2 = c1.date_input("From", value=date.today().replace(day=1), key="pl_start")
        ed2 = c2.date_input("To", value=date.today(), key="pl_end")
        pl = rep.get_profit_loss(tenant_id, sd2, ed2)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Sales", f"₹{pl['sales']:,.2f}")
        m2.metric("Purchases", f"₹{pl['purchases']:,.2f}")
        m3.metric("Expenses", f"₹{pl['expenses']:,.2f}")
        m4.metric("Net Profit", f"₹{pl['net_profit']:,.2f}")

    with tab3:
        c1, c2 = st.columns(2)
        sd3 = c1.date_input("From", value=date.today().replace(day=1), key="top_start")
        ed3 = c2.date_input("To", value=date.today(), key="top_end")
        top_df = rep.get_top_products(tenant_id, sd3, ed3)
        st.dataframe(top_df, use_container_width=True, hide_index=True)


def render_settings_page():
    tenant_id = st.session_state.tenant_id
    st.header("⚙️ Settings")
    settings_row = db.get_settings(tenant_id)
    sub = db.get_subscription(tenant_id)

    if sub:
        st.info(f"Plan: **{sub['plan']}** | Status: **{sub['status']}** | Valid tak: **{sub['current_period_end']}**")
    else:
        st.error("Koi subscription active nahi hai.")

    with st.form("settings_form"):
        address = st.text_area("Address", value=settings_row["address"] or "")
        mobile = st.text_input("Mobile", value=settings_row["mobile"] or "")
        gst_number = st.text_input("GST Number", value=settings_row["gst_number"] or "")
        if st.form_submit_button("Save Settings"):
            db.save_shop_setup(tenant_id, address, mobile, gst_number,
                                settings_row["footer_message"] or "", settings_row["terms"] or "")
            st.success("Settings updated!")
            st.rerun()

    st.markdown("---")
    st.subheader("🔗 Google Sheets Sync")
    if not gs.GSHEET_LIB_AVAILABLE:
        st.warning("Google Sheets library install nahi hai.")
    else:
        gcfg = gs.get_gsheet_config(tenant_id)
        with st.expander("⚙️ Setup", expanded=not gcfg["gsheet_enabled"]):
            creds_upload = st.file_uploader("Service Account JSON File", type=["json"])
            sheet_id_input = st.text_input("Google Sheet ID", value=gcfg["gsheet_id"] or "")
            enabled_input = st.checkbox("Sync Enable Karein", value=bool(gcfg["gsheet_enabled"]))
            if st.button("💾 Save Google Sheets Settings"):
                creds_json = gcfg["gsheet_creds"]
                if creds_upload is not None:
                    creds_json = creds_upload.read().decode("utf-8")
                gs.save_gsheet_config(tenant_id, enabled_input, sheet_id_input.strip(), creds_json)
                st.success("Saved!")
                st.rerun()

        if gcfg["gsheet_enabled"]:
            if st.button("🔄 Abhi Sync Karein"):
                ok, msg = gs.sync_today_to_gsheet(tenant_id)
                (st.success if ok else st.error)(msg)

    if st.session_state.role == "Admin":
        st.markdown("---")
        st.subheader("🎬 Demo Data")
        st.caption("Demo dikhane ke liye 25 sample records add karein. Real customer ko dene se pehle 'Clear' zaroor karein.")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("➕ Demo Data Add Karein (25 Records)"):
                added = demo.seed_demo_data(tenant_id)
                st.success(f"{added} demo records add ho gaye!")
                st.rerun()
        with dc2:
            if st.button("🗑️ Clear All Business Data"):
                demo.clear_tenant_business_data(tenant_id)
                st.success("Saara business data clear ho gaya.")
                st.rerun()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
def main_app():
    render_sidebar()
    page = st.session_state.current_page

    if not page_allowed(page, st.session_state.role):
        st.error("⛔ Aapko is page ka access nahi hai.")
        return

    if page == "Dashboard":
        render_dashboard()
    elif page == "Billing System (POS)":
        render_pos_page()
    elif page == "Product Master":
        render_product_master_page()
    elif page == "Supplier Management":
        render_supplier_page()
    elif page == "Customer Management":
        render_customer_page()
    elif page == "Stock Purchase":
        render_stock_purchase_page()
    elif page == "Expense Management":
        render_expense_page()
    elif page == "Reports Hub":
        render_reports_page()
    elif page == "Settings":
        render_settings_page()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if not st.session_state.logged_in:
    if st.session_state.get("show_signup"):
        signup_screen()
    else:
        login_screen()
else:
    # Re-check subscription EVERY page load, not just at login — a
    # subscription that expires/gets suspended mid-session must lock the
    # user out on their very next interaction, not just their next login.
    active, status, msg = db.is_subscription_active(st.session_state.tenant_id)
    if not active:
        st.error(f"⚠️ {msg}")
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.rerun()
    else:
        main_app()
