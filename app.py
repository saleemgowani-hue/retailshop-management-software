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
import io
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
    /* Force a light colour scheme explicitly. Without this, Android
       Chrome's "Force dark mode for web contents" auto-inverts colours on
       pages that don't declare their own scheme -- on this app that shows
       up as solid black text input / selectbox boxes with invisible text,
       since Streamlit's own light-theme styling isn't something Chrome's
       heuristic understands. Declaring color-scheme opts the page out of
       that auto-inversion. */
    :root, html, body { color-scheme: light only; }
    .stApp { background-color: #f7f9fc; }
    /* Explicit colours on every input-like control -- belt-and-suspenders
       alongside color-scheme, since some Android Chrome versions still
       partially invert individual form controls even on a page that
       declares color-scheme: light. */
    input[type="text"], input[type="password"], input[type="number"],
    textarea, div[data-baseweb="select"] > div, div[data-baseweb="input"] {
        background-color: #ffffff !important;
        color: #262730 !important;
    }
    input[type="text"]::placeholder, input[type="password"]::placeholder,
    input[type="number"]::placeholder, textarea::placeholder {
        color: #808495 !important;
    }
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
    /* Colourful Dashboard nav tiles — each is a real st.button (not an <a>
       link, which would force a full page reload and drop the session).
       Per-tile colour is applied via nth-child on the shared row container
       in render_dashboard(), the one CSS technique that actually works
       against Streamlit's DOM (nth-of-type doesn't: every button is the
       sole child of its own wrapper div, so it can never disambiguate
       buttons from each other — verified empirically, not assumed). */
    .st-key-dash_nav_tiles div.stButton > button {
        min-height: 92px; border-radius: 16px; border: none; color: white;
        font-weight: 700; font-size: 14px; box-shadow: 0 4px 10px rgba(0,0,0,0.18);
        transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
        white-space: normal;
    }
    .st-key-dash_nav_tiles div.stButton > button:hover {
        transform: translateY(-4px) scale(1.02);
        box-shadow: 0 9px 18px rgba(0,0,0,0.26);
        opacity: 0.95;
    }
    /* Mobile/tablet touch-friendly tuning (same as the local app) */
    @media (max-width: 900px) {
        div.stButton > button { min-height: 44px; font-size: 15px; }
        input[type="number"], input[type="text"], input[type="password"] {
            min-height: 40px; font-size: 16px;
        }
        .st-key-dash_nav_tiles div.stButton > button { min-height: 76px; font-size: 13px; }
        div[data-testid="stDataFrame"] { overflow-x: auto; }
    }
    @media (max-width: 480px) {
        .st-key-dash_nav_tiles div[data-testid="stHorizontalBlock"] { flex-direction: column; }
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
    "Low Stock Alerts": ["Admin", "Manager", "Cashier"],
    "Settings": ["Admin"],
}

# (icon, gradient-start, gradient-end) — used for the colourful clickable
# Dashboard navigation tiles, same visual language as the original app.
PAGE_STYLES = {
    "Billing System (POS)": ("🧾", "#7f7fd5", "#38ef7d"),
    "Product Master": ("📦", "#f12711", "#f5af19"),
    "Supplier Management": ("🏭", "#11998e", "#38ef7d"),
    "Customer Management": ("👥", "#ff416c", "#ff4b2b"),
    "Stock Purchase": ("📥", "#4e54c8", "#8f94fb"),
    "Expense Management": ("💸", "#203a43", "#2c5364"),
    "Reports Hub": ("📈", "#f7b733", "#fc4a1a"),
    "Low Stock Alerts": ("⚠️", "#cb356b", "#bd3f32"),
    "Settings": ("⚙️", "#3a6073", "#16222a"),
}


def page_allowed(page: str, role: str) -> bool:
    return role in PAGE_PERMISSIONS.get(page, [])


def _df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
for key, default in [
    ("logged_in", False), ("tenant_id", None), ("username", ""), ("role", ""),
    ("current_page", "Dashboard"), ("cart", []), ("confirm_delete", {}), ("is_demo_account", False),
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
            text("SELECT id, shop_name, is_demo FROM tenants WHERE installation_id = :code"),
            {"code": shop_code.strip()},
        ).fetchone()
        return (str(row[0]), row[1], bool(row[2])) if row else (None, None, False)


def _log_into_tenant(tenant_id, shop_name, username, role, is_demo=False):
    st.session_state.logged_in = True
    st.session_state.tenant_id = tenant_id
    st.session_state.shop_name = shop_name
    st.session_state.username = username
    st.session_state.role = role
    st.session_state.is_demo_account = is_demo
    st.rerun()


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
                tenant_id, shop_name, is_demo = resolve_tenant_by_code(shop_code)
                if not tenant_id:
                    st.error("Shop Code galat hai.")
                    return

                active, status, msg = db.is_subscription_active(tenant_id)
                if not active:
                    st.error(f"⚠️ {msg}")
                    return

                user = db.check_login(tenant_id, username, password)
                if user:
                    _log_into_tenant(tenant_id, shop_name, user["username"], user["role"], is_demo=is_demo)
                else:
                    st.error("Username ya Password galat hai.")

        st.caption("Naya shop hai? Neeche Signup karein.")
        if st.button("📝 New Shop Signup", use_container_width=True):
            st.session_state["show_signup"] = True
            st.rerun()

        st.divider()
        demo_tenant_id, demo_shop_name, demo_shop_code = db.get_or_create_demo_tenant()
        st.caption(
            f"👀 Bina signup ke try karna hai? Shop Code `{demo_shop_code}`, "
            f"Username `{db.DEMO_USERNAME}`, Password `{db.DEMO_PASSWORD}` se login karein — "
            f"ya seedha neeche button dabayein:"
        )
        if st.button("🎬 Try Demo (One-Click Login)", use_container_width=True):
            _log_into_tenant(demo_tenant_id, demo_shop_name, db.DEMO_USERNAME, "Admin", is_demo=True)


def signup_screen():
    st.markdown("<h1 style='text-align:center;'>📝 New Shop Signup</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("signup_form"):
            shop_name = st.text_input("Shop Name")
            admin_username = st.text_input("Admin Username")
            admin_password = st.text_input("Admin Password", type="password")
            license_key = st.text_input(
                "License Key",
                help="Monthly ya Yearly plan ka activation code — billing/admin se milta hai",
            )
            submitted = st.form_submit_button("Create Shop", use_container_width=True)

            if submitted:
                if not shop_name.strip() or not admin_username.strip() or len(admin_password) < 4:
                    st.error("Shop Name, Username bharein, Password kam se kam 4 characters ka ho.")
                    return
                if not license_key.strip():
                    st.error("License Key bharna zaroori hai. Yeh billing/admin se milta hai.")
                    return

                key_ok, plan, days, key_msg = db.validate_license_key(license_key)
                if not key_ok:
                    st.error(f"⚠️ {key_msg}")
                    return

                tenant_id = db.create_tenant(shop_name.strip())
                ok, msg = db.register_user(tenant_id, admin_username.strip(), admin_password, "Admin")
                if not ok:
                    st.error(msg)
                    return

                if not db.redeem_license_key(license_key, tenant_id):
                    st.error("⚠️ Yeh License Key abhi-abhi kisi aur ne use kar li. Dusri key try karein.")
                    return
                db.create_or_renew_subscription(tenant_id, plan, days)

                with db.get_engine().connect() as conn:
                    from sqlalchemy import text
                    shop_code = conn.execute(
                        text("SELECT installation_id FROM tenants WHERE id = :tid"), {"tid": tenant_id}
                    ).fetchone()[0]

                st.success(
                    f"Shop ban gayi! Aapka **Shop Code** hai: `{shop_code}` — ise safe rakhein, "
                    f"login karte waqt chahiye hoga."
                )
                st.info(f"✅ {plan.capitalize()} plan ({days} din) activate ho gaya hai — turant login kar sakte hain.")
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

    if st.sidebar.button("🚪 Logout", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.tenant_id = None
        st.session_state.username = ""
        st.session_state.role = ""
        st.session_state.cart = []
        st.session_state.is_demo_account = False
        st.rerun()

    with st.sidebar.expander("🔑 Change Password"):
        if st.session_state.is_demo_account:
            st.caption("Demo account ka password change nahi kiya ja sakta — yeh sabke liye shared hai.")
        else:
            with st.form("pwd_change_form"):
                old_p = st.text_input("Current Password", type="password")
                new_p = st.text_input("New Password", type="password")
                p_sub = st.form_submit_button("Update Password", use_container_width=True)
                if p_sub:
                    if old_p and new_p:
                        tenant_id = st.session_state.tenant_id
                        succ, message = db.change_password(tenant_id, st.session_state.username, old_p, new_p)
                        (st.success if succ else st.error)(message)
                    else:
                        st.warning("Please fill all fields.")

    st.sidebar.markdown("---")

    for page in PAGE_PERMISSIONS:
        if page_allowed(page, st.session_state.role):
            icon = PAGE_STYLES.get(page, ("📊",))[0] if page != "Dashboard" else "📊"
            if st.sidebar.button(f"{icon} {page}", use_container_width=True, key=f"nav_{page}"):
                st.session_state.current_page = page
                st.rerun()


# ---------------------------------------------------------------------------
# Page renderers — thin wrappers calling the already-tested *_saas modules.
# Every one of them reads tenant_id from session_state ONCE at the top and
# threads it through every call below — never re-derived, never trusted
# from any other source.
# ---------------------------------------------------------------------------
def render_dashboard():
    tenant_id = st.session_state.tenant_id
    role = st.session_state.role
    st.header("📊 Dashboard")
    today = date.today()
    EARLIEST = date(2000, 1, 1)

    products_df = prod.get_all_products(tenant_id)
    suppliers_df = sup.get_all_suppliers(tenant_id)
    customers_df = cust.get_all_customers(tenant_id)
    active_products_df = products_df[products_df["is_active"]] if not products_df.empty else products_df

    # ---- Colourful clickable navigation tiles for every page this role can access ----
    st.markdown("#### 🚀 Quick Navigation")
    tile_pages = [p for p in PAGE_STYLES if page_allowed(p, role)]
    cols_per_row = 3
    css_rules = []
    with st.container(key="dash_nav_tiles"):
        for r in range(0, len(tile_pages), cols_per_row):
            row_pages = tile_pages[r:r + cols_per_row]
            row_key = f"dash_nav_row_{r // cols_per_row}"
            with st.container(key=row_key):
                cols = st.columns(len(row_pages))
                for idx, (col, page_name) in enumerate(zip(cols, row_pages), start=1):
                    icon, color1, color2 = PAGE_STYLES[page_name]
                    css_rules.append(
                        f'.st-key-{row_key} div[data-testid="stHorizontalBlock"] > div:nth-child({idx}) '
                        f'div.stButton button {{background: linear-gradient(135deg, {color1}, {color2});}}'
                    )
                    with col:
                        if st.button(f"{icon}  {page_name}", key=f"navtile_{page_name}", use_container_width=True):
                            st.session_state.current_page = page_name
                            st.rerun()
        st.markdown(f"<style>{''.join(css_rules)}</style>", unsafe_allow_html=True)

    # ---- Basic counts ----
    tile_colors = ["#0072ff, #00c6ff", "#f7971e, #ffd200", "#8e2de2, #4a00e0"]
    tiles = [
        ("📦 Products", len(active_products_df)),
        ("🏭 Suppliers", len(suppliers_df[suppliers_df["is_active"]]) if not suppliers_df.empty else 0),
        ("👥 Customers", len(customers_df[customers_df["is_active"]]) if not customers_df.empty else 0),
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

    # ---- All-time Sales / Expense totals ----
    st.markdown("<br>", unsafe_allow_html=True)
    all_time_pl = rep.get_profit_loss(tenant_id, EARLIEST, today)
    ac1, ac2, ac3, ac4, ac5 = st.columns(5)
    ac1.metric("📦 Products", len(active_products_df))
    ac2.metric("👥 Customers", len(customers_df[customers_df["is_active"]]) if not customers_df.empty else 0)
    ac3.metric("🏭 Suppliers", len(suppliers_df[suppliers_df["is_active"]]) if not suppliers_df.empty else 0)
    ac4.metric("💰 Total Sales", f"₹ {all_time_pl['sales']:,.2f}")
    ac5.metric("💸 Total Expense", f"₹ {all_time_pl['expenses']:,.2f}")

    # ---- Current stock valuation: at cost (purchase rate) vs at sale rate ----
    if not active_products_df.empty:
        stock_cost_value = float((active_products_df["opening_stock"] * active_products_df["purchase_price"]).sum())
        stock_sale_value = float((active_products_df["opening_stock"] * active_products_df["selling_price"]).sum())
    else:
        stock_cost_value = stock_sale_value = 0.0
    potential_profit = stock_sale_value - stock_cost_value

    st.markdown("<br>", unsafe_allow_html=True)
    vcol1, vcol2, vcol3 = st.columns(3)
    vcol1.metric("📥 Total Stock Value (Purchase Rate)", f"₹ {stock_cost_value:,.2f}")
    vcol2.metric("📤 Total Stock Value (Sale Rate)", f"₹ {stock_sale_value:,.2f}")
    vcol3.metric("📊 Potential Profit (if all stock sold)", f"₹ {potential_profit:,.2f}")

    # ---- Collection Summary (all-time, by payment mode) ----
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("###### 💳 Collection Summary (All-Time)")
    all_sales_df = rep.get_sales_report(tenant_id, EARLIEST, today)
    collection = rep.get_sales_payment_summary(all_sales_df)
    ccol1, ccol2, ccol3, ccol4 = st.columns(4)
    ccol1.metric("💵 Cash Collection", f"₹ {all_sales_df['cash_amount'].sum() if not all_sales_df.empty else 0:,.2f}")
    ccol2.metric("📲 UPI Collection", f"₹ {all_sales_df['upi_amount'].sum() if not all_sales_df.empty else 0:,.2f}")
    ccol3.metric("💳 Card Collection", f"₹ {collection['card']:,.2f}")
    ccol4.metric("🧾 Total Collection", f"₹ {collection['total']:,.2f}")

    st.markdown("<hr>", unsafe_allow_html=True)

    # ---- Today's Summary — one-click Excel export for WhatsApp/Email ----
    st.subheader("📤 Aaj Ki Summary (WhatsApp Karne Ke Liye)")
    st.caption("Ek click me aaj ki Sales, Purchases, Expenses ki Excel file banegi — download karke WhatsApp/Email se kisi ko bhi bhej sakte ho.")

    def _renamed(df, rename_map):
        if df.empty:
            return pd.DataFrame()
        return df.rename(columns=rename_map)[list(rename_map.values())]

    today_sales_df = _renamed(rep.get_sales_report(tenant_id, today, today), {
        "bill_number": "Bill No", "bill_date": "Date", "customer_name": "Customer",
        "payment_mode": "Payment Mode", "grand_total": "Amount",
    })
    today_purchases_df = _renamed(rep.get_purchase_report(tenant_id, today, today), {
        "purchase_date": "Date", "supplier": "Supplier", "product": "Product",
        "quantity": "Qty", "total_amount": "Amount",
    })
    today_expenses_df = _renamed(exp.get_expenses(tenant_id, today, today), {
        "expense_date": "Date", "expense_type": "Type", "amount": "Amount", "remarks": "Remarks",
    })

    today_total_sales = today_sales_df["Amount"].sum() if not today_sales_df.empty else 0
    today_total_purchases = today_purchases_df["Amount"].sum() if not today_purchases_df.empty else 0
    today_total_expenses = today_expenses_df["Amount"].sum() if not today_expenses_df.empty else 0

    scol1, scol2, scol3 = st.columns(3)
    scol1.metric("💰 Aaj Ki Sales", f"₹ {today_total_sales:,.2f}")
    scol2.metric("📥 Aaj Ki Purchases", f"₹ {today_total_purchases:,.2f}")
    scol3.metric("💸 Aaj Ke Expenses", f"₹ {today_total_expenses:,.2f}")

    summary_row = pd.DataFrame([{
        "Date": today.isoformat(), "Total Sales": today_total_sales,
        "Total Purchases": today_total_purchases, "Total Expenses": today_total_expenses,
        "Net (Sales - Purchases - Expenses)": today_total_sales - today_total_purchases - today_total_expenses
    }])

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_row.to_excel(writer, index=False, sheet_name="Summary")
        (today_sales_df if not today_sales_df.empty else pd.DataFrame([{"Info": "Aaj koi sale nahi hui"}])).to_excel(writer, index=False, sheet_name="Sales")
        (today_purchases_df if not today_purchases_df.empty else pd.DataFrame([{"Info": "Aaj koi purchase nahi hui"}])).to_excel(writer, index=False, sheet_name="Purchases")
        (today_expenses_df if not today_expenses_df.empty else pd.DataFrame([{"Info": "Aaj koi expense nahi hua"}])).to_excel(writer, index=False, sheet_name="Expenses")

    st.download_button(
        "⬇️ Aaj Ki Summary Excel Download Karein",
        buffer.getvalue(),
        file_name=f"Daily_Summary_{today.isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
    st.caption("Download hone ke baad WhatsApp/Gmail me attach karke seedha bhej sakte ho.")

    st.markdown("<hr>", unsafe_allow_html=True)

    st.subheader("⚠️ Low Stock Alert Items")
    low_stock = active_products_df[active_products_df["opening_stock"] <= active_products_df["minimum_stock"]] if not active_products_df.empty else active_products_df
    if not low_stock.empty:
        st.warning(f"⚠️ {len(low_stock)} product(s) ko turant re-stock karna hoga!")
        st.dataframe(low_stock[["name", "opening_stock", "minimum_stock", "unit"]], use_container_width=True, hide_index=True)
    else:
        st.success("✨ Excellent! Saare products me sufficient stock hai.")


def _clear_pos_cart_state():
    """Resets the cart AND every per-item/payment widget key tied to it —
    otherwise leftover keys (cart_disc_0, mixed_cash_amt, etc.) from the
    previous bill can bleed into the next one."""
    for idx in range(len(st.session_state.get("cart", []))):
        st.session_state.pop(f"cart_disc_{idx}", None)
    st.session_state.pop("mixed_cash_amt", None)
    st.session_state.pop("mixed_upi_amt", None)
    st.session_state.cart = []


def _build_receipt_text(shop_name, address, mobile, gst_number, bill_no, bill_date,
                         cust_name, cust_mobile, pay_mode, cart_items,
                         subtotal, discount, gst_total, grand_total, footer_message,
                         cash_amount=0.0, upi_amount=0.0):
    lines = [shop_name or "Retail Shop", address or "", f"Mobile: {mobile or ''}  GSTIN: {gst_number or ''}"]
    lines.append("-" * 40)
    lines.append(f"Bill No: {bill_no}   Date: {bill_date}")
    lines.append(f"Customer: {cust_name or 'Walk-in Customer'} ({cust_mobile or '-'})")
    lines.append(f"Payment Mode: {pay_mode}")
    lines.append("-" * 40)
    for item in cart_items:
        lines.append(f"{item['name']} x{item['qty']} @ ₹{item['selling_price']:.2f} = ₹{item['total']:.2f}")
    lines.append("-" * 40)
    lines.append(f"Subtotal: ₹{subtotal:.2f}")
    lines.append(f"Discount: ₹{discount:.2f}")
    lines.append(f"GST: ₹{gst_total:.2f}")
    lines.append(f"Grand Total: ₹{grand_total:.2f}")
    if pay_mode == "Mixed":
        lines.append(f"Cash Paid: ₹{cash_amount:.2f}")
        lines.append(f"UPI Paid: ₹{upi_amount:.2f}")
    if footer_message:
        lines.append("-" * 40)
        lines.append(footer_message)
    return "\n".join(lines)


def render_pos_page():
    tenant_id = st.session_state.tenant_id
    st.header("🧾 Billing System (POS)")

    if st.session_state.get("last_saved_bill"):
        saved = st.session_state["last_saved_bill"]
        st.success(f"✅ Bill Saved: {saved['bill_no']}")
        st.download_button(
            "🧾 Download Receipt (Text)", saved["receipt_text"],
            file_name=f"{saved['bill_no']}.txt", mime="text/plain", key="download_receipt_persist"
        )
        st.caption("Jiske paas jo bhi printer ho, wahi option use karein:")
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            st.components.v1.html(bc.render_print_button_html(saved["html_80mm"], "p80", "🖨️ 80mm Thermal"), height=55)
        with pc2:
            st.components.v1.html(bc.render_print_button_html(saved["html_a4"], "pa4", "🖨️ A4 Invoice"), height=55)
        with pc3:
            st.components.v1.html(bc.render_print_button_html(saved["html_a5"], "pa5", "🖨️ A5 Invoice"), height=55)
        st.caption("PDF chahiye ho to Print dialog me printer ki jagah 'Save as PDF' choose karein.")
        if st.button("➕ Naya Bill Shuru Karein"):
            st.session_state.pop("last_saved_bill", None)
            st.rerun()
        st.markdown("---")

    products_df = pos.fetch_active_products(tenant_id)
    if products_df.empty:
        st.warning("Koi product nahi hai — pehle Product Master me add karein.")
        return

    def _add_or_increment(product_id, name, selling_price, gst, qty, discount_amt):
        existing = next((i for i in st.session_state.cart if i["product_id"] == product_id), None)
        if existing:
            existing["qty"] += qty
            existing["total"] = round(existing["qty"] * max(existing["selling_price"] - existing["discount_amt"], 0), 2)
        else:
            st.session_state.cart.append({
                "product_id": product_id, "name": name, "selling_price": float(selling_price),
                "qty": qty, "gst": float(gst), "discount_amt": float(discount_amt),
                "total": round(qty * max(float(selling_price) - float(discount_amt), 0), 2),
            })

    def _handle_barcode_scan():
        code = st.session_state.get("barcode_scan_box", "").strip()
        st.session_state["barcode_scan_box"] = ""
        if not code:
            return
        found = pos.scan_barcode(tenant_id, code)
        if not found:
            st.session_state["_barcode_scan_msg"] = ("error", f"❌ Barcode '{code}' se koi product nahi mila.")
            return
        pid = str(found["id"])
        already_in_cart = sum(i["qty"] for i in st.session_state.cart if i["product_id"] == pid)
        live_stock = pos.get_live_stock(tenant_id, pid)
        if already_in_cart + 1 > live_stock:
            st.session_state["_barcode_scan_msg"] = ("error", f"⚠️ '{found['name']}' ka stock khatam ho gaya hai.")
            return
        _add_or_increment(pid, found["name"], found["selling_price"], found["gst"], 1, float(found.get("default_discount") or 0))
        st.session_state["_barcode_scan_msg"] = ("success", f"✅ Cart me add hua: {found['name']}")

    st.text_input(
        "📷 Barcode Scan Karein (cursor yahan rakh kar scanner se scan karein)",
        key="barcode_scan_box", on_change=_handle_barcode_scan,
        placeholder="Scanner se scan karein, ya barcode type karke Enter dabayein"
    )
    scan_msg = st.session_state.pop("_barcode_scan_msg", None)
    if scan_msg:
        kind, msg_text = scan_msg
        (st.success if kind == "success" else st.error)(msg_text)
    st.markdown("---")

    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("🔍 Search Product", placeholder="Type product name...")
        filtered = products_df[products_df["name"].str.contains(search, case=False, na=False)] if search else products_df
        filtered = filtered[filtered["opening_stock"] > 0]

        if filtered.empty:
            st.info("No matching in-stock products found.")
        else:
            prod_dict = dict(zip(
                filtered["name"] + " (Stock: " + filtered["opening_stock"].astype(str) + " " + filtered["unit"].fillna("") + ")",
                filtered["id"]
            ))
            sel_prod_label = st.selectbox("Select Product to Add", list(prod_dict.keys()))
            sel_prod_id = str(prod_dict[sel_prod_label])
            prod_info = filtered[filtered["id"].astype(str) == sel_prod_id].iloc[0]

            already_in_cart = sum(i["qty"] for i in st.session_state.cart if i["product_id"] == sel_prod_id)
            live_stock = pos.get_live_stock(tenant_id, sel_prod_id)
            remaining = max(live_stock - already_in_cart, 0.0)
            st.caption(f"In cart already: {already_in_cart} | Available to add: {remaining}")

            if remaining <= 0:
                st.warning("All available stock for this product is already in the cart.")
            else:
                qty = st.number_input("Quantity", min_value=0.01, max_value=float(remaining), value=min(1.0, float(remaining)), step=1.0)
                default_disc = float(prod_info.get("default_discount") or 0)
                if default_disc > 0:
                    st.caption(f"💡 Is product ka default discount: ₹{default_disc:.2f} per unit — cart me add hone ke baad edit kar sakte ho.")
                if st.button("➕ Add to Cart", use_container_width=True, key="add_cart_btn"):
                    _add_or_increment(sel_prod_id, prod_info["name"], prod_info["selling_price"], prod_info["gst"], qty, default_disc)
                    st.success("Added to cart!")
                    st.rerun()

    with col2:
        st.subheader("Customer Details")
        customer_mode = st.radio("Customer", ["Walk-in Customer", "Existing / New Customer"], horizontal=True)
        if customer_mode == "Walk-in Customer":
            cust_name, cust_mobile, save_customer = "Walk-in Customer", "0000000000", False
        else:
            active_customers_df = cust.get_active_customers(tenant_id)
            cust_name = st.text_input("Customer Name")
            cust_mobile = st.text_input("Customer Mobile")
            existing_match = active_customers_df[active_customers_df["mobile"] == cust_mobile] if cust_mobile and not active_customers_df.empty else pd.DataFrame()
            if not existing_match.empty:
                st.caption(f"✅ Existing customer: {existing_match.iloc[0]['name']}")
                save_customer = False
            else:
                save_customer = st.checkbox("💾 Save this as a new customer", value=bool(cust_mobile))

        pay_mode = st.selectbox("Payment Mode", ["Cash", "UPI", "Card", "Mixed"])
        bill_discount = st.number_input("Bill Discount (₹)", min_value=0.0, value=0.0, step=1.0)

    if st.session_state.cart:
        st.markdown("---")
        st.subheader("🛒 Current Cart Items")
        hc1, hc2, hc3, hc4, hc5, hc6 = st.columns([2.5, 1.3, 1.1, 1.3, 1.3, 1])
        hc1.markdown("**Item**"); hc2.markdown("**Price**"); hc3.markdown("**Qty**")
        hc4.markdown("**Disc ₹**"); hc5.markdown("**Total**")

        for idx, item in enumerate(st.session_state.cart):
            c1, c2, c3, c4, c5, c6 = st.columns([2.5, 1.3, 1.1, 1.3, 1.3, 1])
            c1.write(item["name"])
            c2.write(f"₹ {item['selling_price']:,.2f}")
            c3.write(f"Qty: {item['qty']}")
            disc_key = f"cart_disc_{idx}"
            if disc_key not in st.session_state:
                st.session_state[disc_key] = float(item["discount_amt"])
            new_disc = c4.number_input(
                "Disc ₹", min_value=0.0, max_value=float(item["selling_price"]),
                step=1.0, key=disc_key, label_visibility="collapsed"
            )
            if new_disc != item["discount_amt"]:
                item["discount_amt"] = new_disc
                item["total"] = round(item["qty"] * max(item["selling_price"] - new_disc, 0), 2)
                st.rerun()
            c5.write(f"₹ {item['total']:,.2f}")
            if c6.button("🗑️", key=f"remove_item_{idx}"):
                st.session_state.cart.pop(idx)
                st.session_state.pop(disc_key, None)
                st.rerun()

        subtotal = sum(i["total"] for i in st.session_state.cart)
        total_gst = sum(i["total"] * i["gst"] / 100 for i in st.session_state.cart)
        bill_discount = min(bill_discount, subtotal)
        grand_total = round(subtotal - bill_discount + total_gst, 2)

        st.write(f"**Subtotal:** ₹ {subtotal:,.2f}")
        st.write(f"**Discount:** ₹ {bill_discount:,.2f}")
        st.write(f"**GST Amount:** ₹ {total_gst:,.2f}")
        st.markdown(f"### **Grand Total: ₹ {grand_total:,.2f}**")

        cash_amount, upi_amount, split_valid = 0.0, 0.0, True
        if pay_mode == "Cash":
            cash_amount = grand_total
        elif pay_mode == "UPI":
            upi_amount = grand_total
        elif pay_mode == "Mixed":
            st.markdown("##### 💵 Split Payment — Cash + UPI")
            # Seed the default via session_state once, then let the widget own
            # it — passing a recomputed `value=` alongside `key=` here would
            # force the field back to that value on every rerun, making it
            # feel like it "resets itself" as soon as you type.
            if "mixed_cash_amt" not in st.session_state:
                st.session_state["mixed_cash_amt"] = round(grand_total, 2)
            if "mixed_upi_amt" not in st.session_state:
                st.session_state["mixed_upi_amt"] = 0.0
            spcol1, spcol2 = st.columns(2)
            with spcol1:
                cash_amount = st.number_input("Cash Amount (₹)", min_value=0.0, step=1.0, key="mixed_cash_amt")
            with spcol2:
                upi_amount = st.number_input("UPI Amount (₹)", min_value=0.0, step=1.0, key="mixed_upi_amt")
            split_total = round(cash_amount + upi_amount, 2)
            diff = round(grand_total - split_total, 2)
            if abs(diff) > 0.01:
                split_valid = False
                if diff > 0:
                    st.error(f"⚠️ Cash + UPI (₹{split_total:,.2f}) Grand Total (₹{grand_total:,.2f}) se ₹{diff:,.2f} kam hai.")
                else:
                    st.error(f"⚠️ Cash + UPI (₹{split_total:,.2f}) Grand Total (₹{grand_total:,.2f}) se ₹{abs(diff):,.2f} zyada hai.")
            else:
                st.success(f"✅ Cash ₹{cash_amount:,.2f} + UPI ₹{upi_amount:,.2f} = Grand Total. Sahi match ho raha hai.")

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            save_disabled = pay_mode == "Mixed" and not split_valid
            if st.button("💾 Save & Complete Bill", use_container_width=True, key="save_bill_btn", disabled=save_disabled):
                ok, result = pos.save_sale(tenant_id, cust_name, cust_mobile, pay_mode,
                                            st.session_state.cart, bill_discount, cash_amount, upi_amount,
                                            save_customer=save_customer)
                if ok:
                    gcfg = gs.get_gsheet_config(tenant_id)
                    if gcfg["gsheet_enabled"]:
                        gs.sync_today_to_gsheet(tenant_id)

                    settings_row = db.get_settings(tenant_id) or {}
                    common_args = dict(
                        shop_name=st.session_state.get("shop_name", ""),
                        address=settings_row.get("address", "") or "",
                        mobile=settings_row.get("mobile", "") or "",
                        gst_number=settings_row.get("gst_number", "") or "",
                        bill_no=result, bill_date=date.today().isoformat(),
                        cust_name=cust_name, cust_mobile=cust_mobile, pay_mode=pay_mode,
                        cart_items=st.session_state.cart, subtotal=subtotal, discount=bill_discount,
                        gst_total=total_gst, grand_total=grand_total,
                        footer_message=settings_row.get("footer_message", "") or "",
                        cash_amount=cash_amount, upi_amount=upi_amount,
                    )
                    st.session_state["last_saved_bill"] = {
                        "bill_no": result,
                        "receipt_text": _build_receipt_text(**common_args),
                        "html_80mm": rcpt.build_80mm_receipt_html(**common_args),
                        "html_a4": rcpt.build_a4_invoice_html(**common_args, terms=settings_row.get("terms", "") or ""),
                        "html_a5": rcpt.build_a5_invoice_html(**common_args, terms=settings_row.get("terms", "") or ""),
                    }
                    _clear_pos_cart_state()
                    st.rerun()
                else:
                    st.error(result)
        with col_b2:
            if st.button("🗑️ Clear Cart", use_container_width=True, key="clear_cart_btn"):
                _clear_pos_cart_state()
                st.rerun()


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
            search_term = st.text_input("🔍 Search by name / category", key="prod_search")
            view_df = all_products
            if search_term:
                mask = (
                    all_products["name"].str.contains(search_term, case=False, na=False) |
                    all_products["category"].fillna("").str.contains(search_term, case=False, na=False)
                )
                view_df = all_products[mask]
            st.dataframe(
                view_df[["id", "name", "category", "brand", "unit", "purchase_price", "selling_price",
                         "gst", "opening_stock", "minimum_stock", "is_active"]],
                use_container_width=True, hide_index=True
            )

            st.markdown("---")
            st.subheader("✏️ Edit Product Details")
            sel_p_name = st.selectbox("Select Product to Edit", all_products["name"].tolist(), key="edit_prod_select")
            p_row = all_products[all_products["name"] == sel_p_name].iloc[0]

            with st.form("edit_prod_form"):
                e_c1, e_c2, e_c3 = st.columns(3)
                with e_c1:
                    e_name = st.text_input("Product Name", value=p_row["name"])
                    e_barcode = st.text_input("Barcode", value=p_row["barcode"] or "")
                    e_cat = st.text_input("Category", value=p_row["category"] or "")
                with e_c2:
                    e_brand = st.text_input("Brand", value=p_row["brand"] or "")
                    unit_options = ["Pcs", "Kg", "Gram", "Litre", "Packet", "Box"]
                    default_unit_idx = unit_options.index(p_row["unit"]) if p_row["unit"] in unit_options else 0
                    e_unit = st.selectbox("Unit", unit_options, index=default_unit_idx)
                    e_purchase_price = st.number_input("Purchase Price (₹)", min_value=0.0, value=float(p_row["purchase_price"] or 0))
                with e_c3:
                    e_selling_price = st.number_input("Selling Price (₹)", min_value=0.0, value=float(p_row["selling_price"] or 0))
                    e_gst = st.number_input("GST %", min_value=0.0, value=float(p_row["gst"] or 0))
                    e_opening_stock = st.number_input("Current Stock", min_value=0.0, value=float(p_row["opening_stock"] or 0))
                    e_minimum_stock = st.number_input("Minimum Stock Alert", min_value=0.0, value=float(p_row["minimum_stock"] or 0))

                e_active = st.checkbox("Active (visible in POS)", value=bool(p_row["is_active"]))
                e_default_discount = st.number_input(
                    "Default Discount ₹ (Optional)", min_value=0.0, step=1.0,
                    value=float(p_row["default_discount"] or 0),
                    help="Billing me ye product cart me add hote hi ye discount (per unit, ₹ me) pehle se bhara milega."
                )

                if st.form_submit_button("💾 Update Product", use_container_width=True):
                    ok, msg = prod.update_product(
                        tenant_id, str(p_row["id"]), e_name, e_barcode or None, e_cat, e_brand, e_unit,
                        e_purchase_price, e_selling_price, e_gst, e_opening_stock, e_minimum_stock,
                        e_default_discount, e_active
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

            st.markdown("---")
            st.subheader("🗑️ Delete Product")
            sel_id = st.selectbox("Select Product to Delete", all_products["id"].astype(str).tolist(), key="del_prod_select")
            if st.button("🗑️ Delete Selected Product"):
                action, msg = prod.delete_or_deactivate_product(tenant_id, sel_id)
                if action != "not_found":
                    st.success(msg)
                else:
                    st.error(msg)
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
    tab1, tab2, tab3 = st.tabs(["➕ Add / List", "✏️ Edit / Delete", "💳 Ledger"])

    with tab1:
        with st.form("add_supplier_form"):
            name = st.text_input("Supplier Name")
            mobile = st.text_input("Mobile")
            address = st.text_input("Address")
            gst_number = st.text_input("GST Number (optional)")
            if st.form_submit_button("Save Supplier"):
                ok, result = sup.add_supplier(tenant_id, name, mobile, address, gst_number)
                if ok:
                    st.success("Supplier added!")
                    st.rerun()
                else:
                    st.error(result)
        st.dataframe(sup.get_all_suppliers(tenant_id), use_container_width=True, hide_index=True)

    with tab2:
        all_suppliers = sup.get_all_suppliers(tenant_id)
        if all_suppliers.empty:
            st.info("Koi supplier nahi hai.")
        else:
            sel_s_name = st.selectbox("Select Supplier to Edit", all_suppliers["name"].tolist(), key="edit_sup_select")
            s_row = all_suppliers[all_suppliers["name"] == sel_s_name].iloc[0]
            with st.form("edit_supplier_form"):
                e_name = st.text_input("Supplier Name", value=s_row["name"])
                e_mobile = st.text_input("Mobile", value=s_row["mobile"] or "")
                e_address = st.text_input("Address", value=s_row["address"] or "")
                e_gst = st.text_input("GST Number", value=s_row["gst_number"] or "")
                e_active = st.checkbox("Active", value=bool(s_row["is_active"]))
                if st.form_submit_button("💾 Update Supplier", use_container_width=True):
                    ok, msg = sup.update_supplier(tenant_id, str(s_row["id"]), e_name, e_mobile, e_address, e_gst, e_active)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()

            st.markdown("---")
            st.subheader("🗑️ Delete Supplier")
            sel_del_name = st.selectbox("Select Supplier to Delete", all_suppliers["name"].tolist(), key="del_sup_select")
            if st.button("🗑️ Delete Selected Supplier"):
                del_row = all_suppliers[all_suppliers["name"] == sel_del_name].iloc[0]
                action, msg = sup.delete_or_deactivate_supplier(tenant_id, str(del_row["id"]))
                if action != "not_found":
                    st.success(msg)
                else:
                    st.error(msg)
                st.rerun()

    with tab3:
        summary = sup.get_ledger_summary(tenant_id)
        if summary.empty:
            st.info("Koi supplier nahi hai.")
        else:
            st.dataframe(summary, use_container_width=True, hide_index=True)

            st.markdown("---")
            st.subheader("📜 Supplier Transactions & Payment")
            sel_ledger_name = st.selectbox("Select Supplier", summary["name"].tolist(), key="ledger_sup_select")
            sel_supplier_id = str(summary[summary["name"] == sel_ledger_name].iloc[0]["id"])

            transactions = sup.get_supplier_transactions(tenant_id, sel_supplier_id)
            if transactions.empty:
                st.info("Is supplier ka koi purchase record nahi hai.")
            else:
                st.dataframe(transactions, use_container_width=True, hide_index=True)

                pending = transactions[transactions["balance"] > 0.005]
                if not pending.empty:
                    st.markdown("##### 💰 Record Payment")
                    pending_labels = {
                        f"{row['purchase_date']} — {row['product']} (Balance: ₹{row['balance']:,.2f})": row["id"]
                        for _, row in pending.iterrows()
                    }
                    sel_purchase_label = st.selectbox("Select Purchase", list(pending_labels.keys()), key="ledger_purchase_select")
                    sel_purchase_id = str(pending_labels[sel_purchase_label])
                    pc1, pc2 = st.columns(2)
                    pay_cash = pc1.number_input("Cash Amount (₹)", min_value=0.0, step=1.0, key="ledger_pay_cash")
                    pay_upi = pc2.number_input("UPI Amount (₹)", min_value=0.0, step=1.0, key="ledger_pay_upi")
                    if st.button("💾 Record Payment", use_container_width=True):
                        ok, msg = sup.record_supplier_payment(tenant_id, sel_purchase_id, sel_supplier_id, pay_cash, pay_upi)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.rerun()
                else:
                    st.success("✨ Is supplier ka sara payment ho chuka hai.")

            st.markdown("---")
            st.subheader("📆 Payment History")
            payment_history = sup.get_payment_history(tenant_id, sel_supplier_id)
            if payment_history.empty:
                st.info("Koi payment record nahi hai.")
            else:
                st.dataframe(payment_history, use_container_width=True, hide_index=True)


def render_customer_page():
    tenant_id = st.session_state.tenant_id
    st.header("👥 Customer Management")
    tab1, tab2, tab3 = st.tabs(["➕ Add / List", "✏️ Edit / Delete", "🧾 Purchase History"])

    with tab1:
        with st.form("add_customer_form"):
            name = st.text_input("Customer Name")
            mobile = st.text_input("Mobile")
            address = st.text_input("Address")
            if st.form_submit_button("Save Customer"):
                ok, result = cust.add_customer(tenant_id, name, mobile, address)
                if ok:
                    st.success("Customer added!")
                    st.rerun()
                else:
                    st.error(result)
        st.dataframe(cust.get_all_customers(tenant_id), use_container_width=True, hide_index=True)

    with tab2:
        all_customers = cust.get_all_customers(tenant_id)
        if all_customers.empty:
            st.info("Koi customer nahi hai.")
        else:
            sel_c_name = st.selectbox("Select Customer to Edit", all_customers["name"].tolist(), key="edit_cust_select")
            c_row = all_customers[all_customers["name"] == sel_c_name].iloc[0]
            with st.form("edit_customer_form"):
                e_name = st.text_input("Customer Name", value=c_row["name"])
                e_mobile = st.text_input("Mobile", value=c_row["mobile"] or "")
                e_address = st.text_input("Address", value=c_row["address"] or "")
                e_active = st.checkbox("Active", value=bool(c_row["is_active"]))
                if st.form_submit_button("💾 Update Customer", use_container_width=True):
                    ok, msg = cust.update_customer(tenant_id, str(c_row["id"]), e_name, e_mobile, e_address, e_active)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()

            st.markdown("---")
            st.subheader("🗑️ Delete Customer")
            sel_del_name = st.selectbox("Select Customer to Delete", all_customers["name"].tolist(), key="del_cust_select")
            if st.button("🗑️ Delete Selected Customer"):
                del_row = all_customers[all_customers["name"] == sel_del_name].iloc[0]
                action, msg = cust.delete_or_deactivate_customer(tenant_id, str(del_row["id"]))
                if action != "not_found":
                    st.success(msg)
                else:
                    st.error(msg)
                st.rerun()

    with tab3:
        lookup_mobile = st.text_input("📱 Customer Mobile Number Daalein")
        if lookup_mobile:
            history = cust.get_purchase_history(tenant_id, lookup_mobile)
            if history.empty:
                st.info("Is mobile number se koi purchase history nahi mili.")
            else:
                st.metric("🧾 Total Bills", len(history))
                st.metric("💰 Total Spent", f"₹ {history['grand_total'].sum():,.2f}")
                st.dataframe(history, use_container_width=True, hide_index=True)


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
            if ok:
                st.success("Purchase saved!")
                st.rerun()
            else:
                st.error(result)

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
            if ok:
                st.success("Expense saved!")
                st.rerun()
            else:
                st.error(result)
    st.dataframe(exp.get_expenses(tenant_id, date(2000, 1, 1), date.today()),
                 use_container_width=True, hide_index=True)


def render_reports_page():
    tenant_id = st.session_state.tenant_id
    st.header("📈 Reports Hub")
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "💰 Sales", "📥 Purchases", "💸 Expenses", "📊 Profit & Loss", "🏆 Top Products", "📆 Day Summary"
    ])

    with tab1:
        c1, c2, c3 = st.columns(3)
        sd = c1.date_input("From", value=date.today().replace(day=1))
        ed = c2.date_input("To", value=date.today())
        view_type = c3.selectbox("View", ["Detailed", "Day-wise", "Month-wise"], key="sales_view")
        sales_df = rep.get_sales_report(tenant_id, sd, ed)
        if not sales_df.empty:
            summary = rep.get_sales_payment_summary(sales_df)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Cash", f"₹{sales_df['cash_amount'].sum():,.2f}")
            m2.metric("UPI", f"₹{sales_df['upi_amount'].sum():,.2f}")
            m3.metric("Card", f"₹{summary['card']:,.2f}")
            m4.metric("Total", f"₹{summary['total']:,.2f}")
            if view_type == "Detailed":
                st.dataframe(sales_df, use_container_width=True, hide_index=True)
            else:
                breakdown = rep.get_sales_daymonth_breakdown(tenant_id, sd, ed, "day" if view_type == "Day-wise" else "month")
                st.dataframe(breakdown, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Sales Report Download Karein (Excel)", _df_to_excel_bytes(sales_df, "Sales"),
                file_name="sales_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("No sales in this range.")

    with tab2:
        c1, c2, c3 = st.columns(3)
        sd_p = c1.date_input("From", value=date.today().replace(day=1), key="pur_start")
        ed_p = c2.date_input("To", value=date.today(), key="pur_end")
        view_type_p = c3.selectbox("View", ["Detailed", "Day-wise", "Month-wise"], key="pur_view")
        purchase_df = rep.get_purchase_report(tenant_id, sd_p, ed_p)
        if not purchase_df.empty:
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Purchases", f"₹{purchase_df['total_amount'].sum():,.2f}")
            m2.metric("Total Paid", f"₹{purchase_df['paid_amount'].sum():,.2f}")
            m3.metric("Balance Due", f"₹{(purchase_df['total_amount'] - purchase_df['paid_amount']).sum():,.2f}")
            if view_type_p == "Detailed":
                st.dataframe(purchase_df, use_container_width=True, hide_index=True)
            else:
                breakdown_p = rep.get_purchase_daymonth_breakdown(tenant_id, sd_p, ed_p, "day" if view_type_p == "Day-wise" else "month")
                st.dataframe(breakdown_p, use_container_width=True, hide_index=True)
        else:
            st.info("No purchases in this range.")

        st.markdown("---")
        st.markdown("##### 💳 Supplier Payments (All Suppliers)")
        payments_df = rep.get_all_supplier_payments_report(tenant_id, sd_p, ed_p)
        if not payments_df.empty:
            st.dataframe(payments_df, use_container_width=True, hide_index=True)
        else:
            st.info("No supplier payments in this range.")

    with tab3:
        c1, c2 = st.columns(2)
        sd_e = c1.date_input("From", value=date.today().replace(day=1), key="exp_start")
        ed_e = c2.date_input("To", value=date.today(), key="exp_end")
        expense_df = rep.get_expense_report(tenant_id, sd_e, ed_e)
        if not expense_df.empty:
            st.metric("Total Expenses", f"₹{expense_df['amount'].sum():,.2f}")
            st.dataframe(expense_df, use_container_width=True, hide_index=True)
            by_type = expense_df.groupby("expense_type")["amount"].sum().reset_index().sort_values("amount", ascending=False)
            st.markdown("##### By Expense Type")
            st.dataframe(by_type, use_container_width=True, hide_index=True)
        else:
            st.info("No expenses in this range.")

    with tab4:
        c1, c2 = st.columns(2)
        sd2 = c1.date_input("From", value=date.today().replace(day=1), key="pl_start")
        ed2 = c2.date_input("To", value=date.today(), key="pl_end")
        pl = rep.get_profit_loss(tenant_id, sd2, ed2)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Sales", f"₹{pl['sales']:,.2f}")
        m2.metric("Purchases", f"₹{pl['purchases']:,.2f}")
        m3.metric("Expenses", f"₹{pl['expenses']:,.2f}")
        m4.metric("Net Profit", f"₹{pl['net_profit']:,.2f}")

    with tab5:
        c1, c2 = st.columns(2)
        sd3 = c1.date_input("From", value=date.today().replace(day=1), key="top_start")
        ed3 = c2.date_input("To", value=date.today(), key="top_end")
        top_df = rep.get_top_products(tenant_id, sd3, ed3)
        st.dataframe(top_df, use_container_width=True, hide_index=True)

    with tab6:
        target_date = st.date_input("Select Date", value=date.today(), key="day_summary_date")
        summary = rep.get_day_summary(tenant_id, target_date)
        m1, m2, m3 = st.columns(3)
        m1.metric("💰 Sales", f"₹{summary['sales_total']:,.2f}")
        m2.metric("📥 Purchases", f"₹{summary['purchases_total']:,.2f}")
        m3.metric("💳 Supplier Payments", f"₹{summary['payments_cash'] + summary['payments_upi']:,.2f}")

        st.markdown("##### 💰 Sales")
        if not summary["sales_df"].empty:
            st.dataframe(summary["sales_df"], use_container_width=True, hide_index=True)
        else:
            st.info("No sales.")
        st.markdown("##### 📥 Purchases")
        if not summary["purchases_df"].empty:
            st.dataframe(summary["purchases_df"], use_container_width=True, hide_index=True)
        else:
            st.info("No purchases.")
        st.markdown("##### 💳 Supplier Payments")
        if not summary["payments_df"].empty:
            st.dataframe(summary["payments_df"], use_container_width=True, hide_index=True)
        else:
            st.info("No supplier payments.")


def render_low_stock_alerts_page():
    tenant_id = st.session_state.tenant_id
    st.header("⚠️ Low Stock Alerts")
    products_df = prod.get_all_products(tenant_id)
    active_products_df = products_df[products_df["is_active"]] if not products_df.empty else products_df
    low_stock = active_products_df[active_products_df["opening_stock"] <= active_products_df["minimum_stock"]] if not active_products_df.empty else active_products_df

    if low_stock.empty:
        st.success("✨ Excellent! Saare products me sufficient stock hai.")
        return

    st.warning(f"⚠️ {len(low_stock)} product(s) ko turant re-stock karna hoga!")
    st.dataframe(
        low_stock[["name", "category", "brand", "unit", "opening_stock", "minimum_stock"]],
        use_container_width=True, hide_index=True
    )
    st.download_button(
        "⬇️ Low Stock List Download Karein (Excel)",
        _df_to_excel_bytes(low_stock[["name", "category", "brand", "unit", "opening_stock", "minimum_stock"]], "Low Stock"),
        file_name="low_stock_alerts.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )


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
    elif page == "Low Stock Alerts":
        render_low_stock_alerts_page()
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
    # For the shared demo account, check on every page load whether it's
    # due for its 60-minute auto-reset (wipes anything a visitor entered
    # and reseeds the original sample data) -- a no-op cheap query outside
    # that window, so this costs nothing for a normal tenant's session.
    if st.session_state.is_demo_account:
        db.maybe_reset_demo_data(st.session_state.tenant_id)

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
