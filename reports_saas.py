"""
reports_saas.py — Tenant-aware Reports Hub module.

Ports app.py's render_reports_hub() — Sales Report, Purchase Report,
Expense Report, Profit & Loss, Top Products, and Day Summary — onto the
tenant-aware PostgreSQL layer.

This is flagged in SaaS_Migration_Analysis.md (Section I) as the single
largest source of query-count in the whole migration, AND a distinct risk
category from row-leakage: every SUM()/COUNT() aggregate here must be
tenant-scoped, or one tenant's Profit & Loss could silently blend in
every other tenant's numbers ("aggregate poisoning") even without ever
exposing a single row directly.
"""

import pandas as pd
from datetime import date
from sqlalchemy import text

from database_saas import get_engine


# ---------------------------------------------------------------------------
# Sales Report
# ---------------------------------------------------------------------------
def get_sales_report(tenant_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT bill_number, bill_date, customer_name, customer_mobile, payment_mode,
                   subtotal, discount, gst, grand_total, cash_amount, upi_amount
            FROM sales WHERE tenant_id = :tid AND bill_date BETWEEN :sd AND :ed
            ORDER BY bill_date DESC
        """),
        engine, params={"tid": tenant_id, "sd": start_date, "ed": end_date}
    )


def get_sales_payment_summary(sales_df: pd.DataFrame) -> dict:
    """Pure DataFrame aggregation, no query — operates on an
    already-tenant-filtered DataFrame, so no additional risk here."""
    if sales_df.empty:
        return {"cash": 0, "upi": 0, "card": 0, "mixed": 0, "total": 0, "count": 0}
    return {
        "cash": sales_df.loc[sales_df["payment_mode"] == "Cash", "grand_total"].sum(),
        "upi": sales_df.loc[sales_df["payment_mode"] == "UPI", "grand_total"].sum(),
        "card": sales_df.loc[sales_df["payment_mode"] == "Card", "grand_total"].sum(),
        "mixed": sales_df.loc[sales_df["payment_mode"] == "Mixed", "grand_total"].sum(),
        "total": sales_df["grand_total"].sum(),
        "count": len(sales_df),
    }


def get_sales_daymonth_breakdown(tenant_id: str, start_date: date, end_date: date, view_type: str) -> pd.DataFrame:
    """view_type: 'day' or 'month'. Cash/UPI received, tenant-scoped."""
    sales_df = get_sales_report(tenant_id, start_date, end_date)
    if sales_df.empty:
        return pd.DataFrame()
    if view_type == "day":
        grouped = sales_df.groupby("bill_date").agg(
            cash_received=("cash_amount", "sum"), upi_received=("upi_amount", "sum"),
            total_sales=("grand_total", "sum"), bills=("bill_number", "count"),
        ).reset_index().sort_values("bill_date", ascending=False)
    else:
        sales_df = sales_df.copy()
        sales_df["month"] = pd.to_datetime(sales_df["bill_date"]).dt.strftime("%Y-%m")
        grouped = sales_df.groupby("month").agg(
            cash_received=("cash_amount", "sum"), upi_received=("upi_amount", "sum"),
            total_sales=("grand_total", "sum"), bills=("bill_number", "count"),
        ).reset_index().sort_values("month", ascending=False)
    return grouped


# ---------------------------------------------------------------------------
# Purchase Report
# ---------------------------------------------------------------------------
def get_purchase_report(tenant_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT pu.purchase_date, s.name AS supplier, p.name AS product, pu.quantity,
                   pu.total_amount, pu.paid_amount
            FROM purchases pu
            LEFT JOIN suppliers s ON s.id = pu.supplier_id AND s.tenant_id = pu.tenant_id
            LEFT JOIN products p ON p.id = pu.product_id AND p.tenant_id = pu.tenant_id
            WHERE pu.tenant_id = :tid AND pu.purchase_date BETWEEN :sd AND :ed
            ORDER BY pu.purchase_date DESC
        """),
        engine, params={"tid": tenant_id, "sd": start_date, "ed": end_date}
    )


def get_purchase_daymonth_breakdown(tenant_id: str, start_date: date, end_date: date, view_type: str) -> pd.DataFrame:
    pur_df = get_purchase_report(tenant_id, start_date, end_date)
    if pur_df.empty:
        return pd.DataFrame()
    pur_df = pur_df.copy()
    pur_df["balance"] = pur_df["total_amount"] - pur_df["paid_amount"]
    if view_type == "day":
        grouped = pur_df.groupby("purchase_date").agg(
            total_purchase=("total_amount", "sum"), paid=("paid_amount", "sum"),
            balance=("balance", "sum"), entries=("product", "count"),
        ).reset_index().sort_values("purchase_date", ascending=False)
    else:
        pur_df["month"] = pd.to_datetime(pur_df["purchase_date"]).dt.strftime("%Y-%m")
        grouped = pur_df.groupby("month").agg(
            total_purchase=("total_amount", "sum"), paid=("paid_amount", "sum"),
            balance=("balance", "sum"), entries=("product", "count"),
        ).reset_index().sort_values("month", ascending=False)
    return grouped


def get_all_supplier_payments_report(tenant_id: str, start_date: date, end_date: date, view_type: str = "all") -> pd.DataFrame:
    """All-suppliers combined Cash/UPI payment report, tenant-scoped."""
    engine = get_engine()
    df = pd.read_sql(
        text("""
            SELECT sp.payment_date, s.name AS supplier, sp.cash_amount, sp.upi_amount, sp.total_amount
            FROM supplier_payments sp
            LEFT JOIN suppliers s ON s.id = sp.supplier_id AND s.tenant_id = sp.tenant_id
            WHERE sp.tenant_id = :tid AND sp.payment_date BETWEEN :sd AND :ed
            ORDER BY sp.payment_date DESC
        """),
        engine, params={"tid": tenant_id, "sd": start_date, "ed": end_date}
    )
    if df.empty or view_type == "all":
        return df
    if view_type == "day":
        return df.groupby("payment_date").agg(
            cash_paid=("cash_amount", "sum"), upi_paid=("upi_amount", "sum"),
            total=("total_amount", "sum"), payments=("total_amount", "count"),
        ).reset_index().sort_values("payment_date", ascending=False)
    else:
        df = df.copy()
        df["month"] = pd.to_datetime(df["payment_date"]).dt.strftime("%Y-%m")
        return df.groupby("month").agg(
            cash_paid=("cash_amount", "sum"), upi_paid=("upi_amount", "sum"),
            total=("total_amount", "sum"), payments=("total_amount", "count"),
        ).reset_index().sort_values("month", ascending=False)


# ---------------------------------------------------------------------------
# Expense Report
# ---------------------------------------------------------------------------
def get_expense_report(tenant_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT expense_date, expense_type, amount, remarks
            FROM expenses WHERE tenant_id = :tid AND expense_date BETWEEN :sd AND :ed
            ORDER BY expense_date DESC
        """),
        engine, params={"tid": tenant_id, "sd": start_date, "ed": end_date}
    )


# ---------------------------------------------------------------------------
# Profit & Loss — THE highest-risk aggregate: three separate SUMs that
# must EACH be tenant-scoped independently, or the "Net Profit" figure
# silently blends other tenants' revenue/costs into this tenant's report.
# ---------------------------------------------------------------------------
def get_profit_loss(tenant_id: str, start_date: date, end_date: date) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        sales_total = conn.execute(
            text("SELECT COALESCE(SUM(grand_total), 0) FROM sales WHERE tenant_id = :tid AND bill_date BETWEEN :sd AND :ed"),
            {"tid": tenant_id, "sd": start_date, "ed": end_date},
        ).scalar()
        purchase_total = conn.execute(
            text("SELECT COALESCE(SUM(total_amount), 0) FROM purchases WHERE tenant_id = :tid AND purchase_date BETWEEN :sd AND :ed"),
            {"tid": tenant_id, "sd": start_date, "ed": end_date},
        ).scalar()
        expense_total = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE tenant_id = :tid AND expense_date BETWEEN :sd AND :ed"),
            {"tid": tenant_id, "sd": start_date, "ed": end_date},
        ).scalar()
    sales_total, purchase_total, expense_total = float(sales_total), float(purchase_total), float(expense_total)
    return {
        "sales": sales_total,
        "purchases": purchase_total,
        "expenses": expense_total,
        "net_profit": sales_total - purchase_total - expense_total,
    }


# ---------------------------------------------------------------------------
# Top Products
# ---------------------------------------------------------------------------
def get_top_products(tenant_id: str, start_date: date, end_date: date, limit: int = 10) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT p.name AS product, SUM(si.quantity) AS qty_sold, SUM(si.total) AS revenue
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id AND s.tenant_id = si.tenant_id
            LEFT JOIN products p ON p.id = si.product_id AND p.tenant_id = si.tenant_id
            WHERE si.tenant_id = :tid AND s.bill_date BETWEEN :sd AND :ed
            GROUP BY si.product_id, p.name
            ORDER BY revenue DESC
            LIMIT :lim
        """),
        engine, params={"tid": tenant_id, "sd": start_date, "ed": end_date, "lim": limit}
    )


# ---------------------------------------------------------------------------
# Day Summary — single-date calendar view combining Sales + Purchases +
# Supplier Payments, exactly as app.py's render_reports_hub Day Summary tab.
# ---------------------------------------------------------------------------
def get_day_summary(tenant_id: str, target_date: date) -> dict:
    sales_df = get_sales_report(tenant_id, target_date, target_date)
    pur_df = get_purchase_report(tenant_id, target_date, target_date)
    pay_df = get_all_supplier_payments_report(tenant_id, target_date, target_date, view_type="all")

    return {
        "sales_df": sales_df,
        "sales_total": sales_df["grand_total"].sum() if not sales_df.empty else 0,
        "sales_cash": sales_df["cash_amount"].sum() if not sales_df.empty else 0,
        "sales_upi": sales_df["upi_amount"].sum() if not sales_df.empty else 0,
        "purchases_df": pur_df,
        "purchases_total": pur_df["total_amount"].sum() if not pur_df.empty else 0,
        "payments_df": pay_df,
        "payments_cash": pay_df["cash_amount"].sum() if not pay_df.empty else 0,
        "payments_upi": pay_df["upi_amount"].sum() if not pay_df.empty else 0,
    }
