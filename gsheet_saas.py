"""
gsheet_saas.py — Tenant-aware Google Sheets sync.

Ports app.py's sync_today_to_gsheet(). Two key SaaS-specific changes from
the local version (per SaaS_Migration_Analysis.md, Section M):

1. Credentials are no longer a local file path (gsheet_creds_path) --
   Streamlit Cloud's filesystem is ephemeral. This module accepts the
   service-account JSON *content* directly (stored in the tenant's
   shop_settings row, or better, in st.secrets for a shared service
   account used across all tenants -- see the docstring on
   get_gsheet_credentials_json below).

2. Every read query is tenant-scoped, same as every other module.
"""

from datetime import date, datetime
import pandas as pd
from sqlalchemy import text

from database_saas import get_engine

try:
    import gspread
    from google.oauth2.service_account import Credentials as GCredentials
    GSHEET_LIB_AVAILABLE = True
except ImportError:
    GSHEET_LIB_AVAILABLE = False


def get_gsheet_config(tenant_id: str):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT gsheet_enabled, gsheet_id, gsheet_creds FROM shop_settings WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).mappings().fetchone()
        return dict(row) if row else {"gsheet_enabled": False, "gsheet_id": None, "gsheet_creds": None}


def save_gsheet_config(tenant_id: str, enabled: bool, sheet_id: str, creds_json: str):
    """creds_json: the service-account JSON *content* as a string (not a
    file path) — safe to store per-tenant since Streamlit Cloud has no
    durable local filesystem to keep an uploaded file in."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE shop_settings SET gsheet_enabled = :enabled, gsheet_id = :sid, gsheet_creds = :creds
                WHERE tenant_id = :tid
            """),
            {"enabled": enabled, "sid": sheet_id, "creds": creds_json, "tid": tenant_id},
        )


def _native(v):
    """Numpy/pandas scalars aren't JSON-serializable for the Sheets API —
    same fix as the local app's sync function. PostgreSQL DATE columns
    also come back as real datetime.date objects (SQLite's local version
    never hit this, since its DATE columns are just TEXT under the
    hood) — those need converting to plain strings too."""
    if pd.isna(v):
        return ""
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def sync_today_to_gsheet(tenant_id: str):
    """Returns (success: bool, message: str). Every failure mode is
    caught and reported rather than raised — a Sheets outage or bad
    credentials must NEVER block billing/purchase saving, which already
    completed in Postgres before this function is ever called."""
    if not GSHEET_LIB_AVAILABLE:
        return False, "Google Sheets library install nahi hai."

    cfg = get_gsheet_config(tenant_id)
    if not cfg["gsheet_enabled"] or not cfg["gsheet_id"] or not cfg["gsheet_creds"]:
        return False, "Google Sheets sync configure nahi hui hai."

    try:
        import json
        creds_dict = json.loads(cfg["gsheet_creds"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = GCredentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(cfg["gsheet_id"])

        today = date.today()
        engine = get_engine()

        sales_df = pd.read_sql(
            text("SELECT bill_number, bill_date, customer_name, payment_mode, grand_total FROM sales WHERE tenant_id = :tid AND bill_date = :d ORDER BY bill_number DESC"),
            engine, params={"tid": tenant_id, "d": today}
        )
        purchases_df = pd.read_sql(
            text("""
                SELECT pu.purchase_date, s.name AS supplier, p.name AS product, pu.quantity, pu.total_amount
                FROM purchases pu
                LEFT JOIN suppliers s ON s.id = pu.supplier_id AND s.tenant_id = pu.tenant_id
                LEFT JOIN products p ON p.id = pu.product_id AND p.tenant_id = pu.tenant_id
                WHERE pu.tenant_id = :tid AND pu.purchase_date = :d ORDER BY pu.id DESC
            """),
            engine, params={"tid": tenant_id, "d": today}
        )
        expenses_df = pd.read_sql(
            text("SELECT expense_date, expense_type, amount, remarks FROM expenses WHERE tenant_id = :tid AND expense_date = :d"),
            engine, params={"tid": tenant_id, "d": today}
        )

        total_sales = sales_df["grand_total"].sum() if not sales_df.empty else 0
        total_purchases = purchases_df["total_amount"].sum() if not purchases_df.empty else 0
        total_expenses = expenses_df["amount"].sum() if not expenses_df.empty else 0

        try:
            ws = spreadsheet.worksheet("Today's Activity")
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title="Today's Activity", rows=200, cols=10)

        rows = [
            [f"Today's Activity — {today.isoformat()}", "", "", "", ""],
            [f"Last synced: {datetime.now().strftime('%d-%b-%Y %I:%M %p')}", "", "", "", ""],
            [],
            ["SUMMARY"],
            ["Total Sales", _native(total_sales), "Total Purchases", _native(total_purchases), "Total Expenses", _native(total_expenses)],
            [],
            ["SALES"],
            ["Bill No", "Date", "Customer", "Payment Mode", "Amount"],
        ]
        for _, r in sales_df.iterrows():
            rows.append([_native(r["bill_number"]), _native(r["bill_date"]), _native(r["customer_name"]),
                         _native(r["payment_mode"]), _native(r["grand_total"])])

        rows.append([])
        rows.append(["PURCHASES"])
        rows.append(["Date", "Supplier", "Product", "Qty", "Amount"])
        for _, r in purchases_df.iterrows():
            rows.append([_native(r["purchase_date"]), _native(r["supplier"]), _native(r["product"]),
                         _native(r["quantity"]), _native(r["total_amount"])])

        rows.append([])
        rows.append(["EXPENSES"])
        rows.append(["Date", "Type", "Amount", "Remarks"])
        for _, r in expenses_df.iterrows():
            rows.append([_native(r["expense_date"]), _native(r["expense_type"]),
                         _native(r["amount"]), _native(r["remarks"])])

        ws.update(rows, "A1")
        return True, "Sync ho gaya!"
    except Exception as e:
        return False, f"Sync fail hua: {e}"
