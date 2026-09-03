"""
database_saas.py — Tenant-aware PostgreSQL data-access layer.

This REPLACES database.py's role for the SaaS version. Every function that
touches a business table takes tenant_id explicitly and includes it in the
WHERE clause — this is the single most important rule in this file, and the
whole point of the migration (see SaaS_Migration_Analysis.md, Section E/I).

Password hashing (PBKDF2-HMAC-SHA256 + per-user salt) is carried over
UNCHANGED from the existing local app — the security audit found this part
of the original design already sound.

Connection: SQLAlchemy engine with pooling (Section O performance audit:
"replace per-call sqlite3.connect() with a pool" — this is that fix).
"""

import os
import hashlib
import binascii
import uuid
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

# ---------------------------------------------------------------------------
# Connection (pooled) — resolves DATABASE_URL in this priority order:
#   1. Streamlit Cloud's st.secrets["DATABASE_URL"]  (recommended for deployment)
#   2. OS environment variable DATABASE_URL           (Docker/other hosts)
#   3. A localhost fallback                            (local development only)
# Example value: postgresql://user:password@host:5432/dbname
# ---------------------------------------------------------------------------
def _resolve_database_url():
    try:
        import streamlit as st
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass  # st.secrets not available outside a running Streamlit app (e.g. tests/scripts) -- fall through
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/retail_saas_test")


_DATABASE_URL = _resolve_database_url()

_engine = create_engine(
    _DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # avoids using a dead connection after idle periods
)


def get_engine():
    """Exposed for callers (e.g. pandas.read_sql) that want the raw engine."""
    return _engine


def check_connection():
    """Cheap connectivity probe. Returns (ok: bool, detail: str).

    Callers use this to surface ONE clear, actionable message at app
    startup instead of Streamlit's generic redacted OperationalError
    turning up deep inside a form submit (e.g. signup) with no hint
    that the real cause is a missing/unreachable DATABASE_URL."""
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Password hashing — IDENTICAL scheme to the existing local app on purpose.
# ---------------------------------------------------------------------------
def hash_password(password, salt=None):
    if salt is None:
        salt = binascii.hexlify(os.urandom(16)).decode("utf-8")
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    )
    return binascii.hexlify(pwd_hash).decode("utf-8"), salt


def verify_password(password, salt, stored_hash):
    if not salt:
        return False
    new_hash, _ = hash_password(password, salt)
    return new_hash == stored_hash


# ---------------------------------------------------------------------------
# TENANT ONBOARDING (replaces the old single-shop shop_setup_screen concept)
# ---------------------------------------------------------------------------
def create_tenant(shop_name: str) -> str:
    """Creates a brand-new tenant + blank shop_settings row. Returns the
    new tenant_id (str). This is the ONE place a tenant_id is minted —
    every other function below requires one to already exist."""
    installation_id = uuid.uuid4().hex[:12]
    with _engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO tenants (shop_name, installation_id)
                VALUES (:shop_name, :installation_id)
                RETURNING id
            """),
            {"shop_name": shop_name, "installation_id": installation_id},
        ).fetchone()
        tenant_id = str(row[0])
        conn.execute(
            text("INSERT INTO shop_settings (tenant_id, configured) VALUES (:tid, FALSE)"),
            {"tid": tenant_id},
        )
    return tenant_id


def get_settings(tenant_id: str):
    """Returns the tenant's shop_settings row (dict-like) or None."""
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM shop_settings WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).mappings().fetchone()
        return dict(row) if row else None


def is_tenant_configured(tenant_id: str) -> bool:
    row = get_settings(tenant_id)
    return bool(row and row["configured"])


def save_shop_setup(tenant_id: str, address, mobile, gst_number, footer_message, terms):
    with _engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE shop_settings
                SET address = :address, mobile = :mobile, gst_number = :gst_number,
                    footer_message = :footer_message, terms = :terms, configured = TRUE
                WHERE tenant_id = :tid
            """),
            {"address": address, "mobile": mobile, "gst_number": gst_number,
             "footer_message": footer_message, "terms": terms, "tid": tenant_id},
        )


# ---------------------------------------------------------------------------
# AUTHENTICATION — every query is scoped to (tenant_id, username), per
# Section E of the analysis. This is the CRITICAL fix from the audit.
# ---------------------------------------------------------------------------
def check_login(tenant_id: str, username: str, password: str):
    """Returns the user row (dict) on success, else None. Requires
    tenant_id to already be known BEFORE calling this — never derive
    tenant_id from anything inside this function."""
    with _engine.connect() as conn:
        user = conn.execute(
            text("SELECT * FROM users WHERE tenant_id = :tid AND username = :u"),
            {"tid": tenant_id, "u": username},
        ).mappings().fetchone()
        if not user:
            return None
        if verify_password(password, user["salt"], user["password"]):
            return dict(user)
        return None


def register_user(tenant_id: str, username: str, password: str, role: str):
    if not username or not username.strip():
        return False, "Username cannot be empty."
    if len(password) < 4:
        return False, "Password must be at least 4 characters."
    if role not in ("Admin", "Manager", "Cashier"):
        return False, "Invalid role."

    with _engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM users WHERE tenant_id = :tid AND username = :u"),
            {"tid": tenant_id, "u": username},
        ).fetchone()
        if existing:
            return False, "Username already exists in this shop."

        hashed, salt = hash_password(password)
        conn.execute(
            text("""
                INSERT INTO users (tenant_id, username, password, salt, role)
                VALUES (:tid, :u, :p, :s, :r)
            """),
            {"tid": tenant_id, "u": username, "p": hashed, "s": salt, "r": role},
        )
    return True, "User registered successfully!"


def change_password(tenant_id: str, username: str, old_pass: str, new_pass: str):
    if len(new_pass) < 4:
        return False, "New password must be at least 4 characters."

    with _engine.begin() as conn:
        user = conn.execute(
            text("SELECT * FROM users WHERE tenant_id = :tid AND username = :u"),
            {"tid": tenant_id, "u": username},
        ).mappings().fetchone()
        if not user:
            return False, "User not found!"
        if not verify_password(old_pass, user["salt"], user["password"]):
            return False, "Current password is incorrect!"

        new_hash, new_salt = hash_password(new_pass)
        conn.execute(
            text("UPDATE users SET password = :p, salt = :s WHERE tenant_id = :tid AND username = :u"),
            {"p": new_hash, "s": new_salt, "tid": tenant_id, "u": username},
        )
    return True, "Password changed successfully!"


# ---------------------------------------------------------------------------
# SUBSCRIPTION VALIDATION — replaces license.py's role entirely.
# No free trial: a tenant with no subscriptions row at all is simply
# not active.
# ---------------------------------------------------------------------------
def get_subscription(tenant_id: str):
    with _engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT * FROM subscriptions WHERE tenant_id = :tid
                ORDER BY created_at DESC LIMIT 1
            """),
            {"tid": tenant_id},
        ).mappings().fetchone()
        return dict(row) if row else None


def is_subscription_active(tenant_id: str):
    """Returns (is_active: bool, status: str, message: str)."""
    sub = get_subscription(tenant_id)
    if not sub:
        return False, "none", "Is shop ke liye koi subscription active nahi hai."
    if sub["status"] == "demo":
        return True, "demo", "Demo mode"
    if sub["status"] in ("suspended", "cancelled"):
        return False, sub["status"], f"Subscription {sub['status']} hai — support se sampark karein."
    if sub["current_period_end"] < date.today():
        return False, "expired", f"Subscription {sub['current_period_end']} ko expire ho chuki hai."
    if sub["status"] != "active":
        return False, sub["status"], f"Subscription status: {sub['status']}"
    return True, "active", "OK"


def create_or_renew_subscription(tenant_id: str, plan: str, days: int):
    """Admin-side action (manual today, payment-webhook-driven later).
    plan: 'monthly' or 'yearly'. days: length of this period (30 / 365)."""
    today = date.today()
    with _engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO subscriptions (tenant_id, plan, status, current_period_start, current_period_end)
                VALUES (:tid, :plan, 'active', :start, :end)
            """),
            {"tid": tenant_id, "plan": plan, "start": today,
             "end": today + timedelta(days=days)},
        )


def set_subscription_status(tenant_id: str, status: str):
    """Manual admin action: suspend/cancel/reactivate."""
    with _engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE subscriptions SET status = :status, updated_at = now()
                WHERE tenant_id = :tid
                AND id = (SELECT id FROM subscriptions WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT 1)
            """),
            {"status": status, "tid": tenant_id},
        )
