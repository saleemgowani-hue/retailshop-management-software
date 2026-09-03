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
import secrets
import uuid
from datetime import date, datetime, timedelta
import pandas as pd
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


def read_sql_df(query, engine, params=None):
    """Drop-in replacement for pandas.read_sql — every *_saas module should
    use this (not pd.read_sql directly) for any DataFrame that reaches
    st.dataframe/st.data_editor.

    SQLAlchemy's psycopg2 dialect unconditionally decodes PostgreSQL UUID
    columns to uuid.UUID objects (hardcoded in its on_connect, not
    configurable). Newer pyarrow then serializes those using its
    'arrow.uuid' extension type, which Streamlit's dataframe grid doesn't
    know how to render — it falls back to showing the raw 16-byte value as
    a {"0":.., "1":.., ...} object instead of the UUID string. Stringify
    any UUID column here, once, rather than at every call site."""
    df = pd.read_sql(query, engine, params=params)
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna()
            if len(sample) and isinstance(sample.iloc[0], uuid.UUID):
                df[col] = df[col].astype(str)
    return df


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


# ---------------------------------------------------------------------------
# LICENSE KEYS — admin-issued, one-time activation codes (see generate_
# license_keys.py). A valid key is what lets New Shop Signup activate a
# subscription immediately instead of leaving the shop locked out pending
# manual admin action.
# ---------------------------------------------------------------------------
_PLAN_DAYS = {"monthly": 30, "yearly": 365}


def generate_license_key(plan: str) -> str:
    """Mints and stores one unused key for `plan` ('monthly'/'yearly').
    Returns the code. Admin-only — run via generate_license_keys.py."""
    if plan not in _PLAN_DAYS:
        raise ValueError("plan must be 'monthly' or 'yearly'")
    code = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    with _engine.begin() as conn:
        conn.execute(
            text("INSERT INTO license_keys (code, plan, days) VALUES (:code, :plan, :days)"),
            {"code": code, "plan": plan, "days": _PLAN_DAYS[plan]},
        )
    return code


def validate_license_key(code: str):
    """Checks a key WITHOUT consuming it. Returns (ok, plan, days, message)."""
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT plan, days, used_by_tenant_id FROM license_keys WHERE code = :code"),
            {"code": (code or "").strip().upper()},
        ).fetchone()
    if not row:
        return False, None, None, "License key invalid hai."
    if row[2] is not None:
        return False, None, None, "Yeh license key pehle hi use ho chuki hai."
    return True, row[0], row[1], "OK"


def redeem_license_key(code: str, tenant_id: str) -> bool:
    """Atomically marks a key used by this tenant. Returns False if it was
    already redeemed by someone else between validate and redeem (race)."""
    with _engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE license_keys SET used_by_tenant_id = :tid, used_at = now()
                WHERE code = :code AND used_by_tenant_id IS NULL
            """),
            {"tid": tenant_id, "code": (code or "").strip().upper()},
        )
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# DEMO ACCESS — one shared, persistent "Try Demo" tenant so a visitor can
# explore the app with one click, with no signup/license key needed.
# ---------------------------------------------------------------------------
DEMO_USERNAME = "admin"
DEMO_PASSWORD = "demo1234"


def activate_demo_subscription(tenant_id: str):
    """status='demo' never expires (see is_subscription_active) — plan/dates
    are required NOT NULL columns but not otherwise meaningful here."""
    today = date.today()
    with _engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO subscriptions (tenant_id, plan, status, current_period_start, current_period_end)
                VALUES (:tid, 'monthly', 'demo', :start, :start)
            """),
            {"tid": tenant_id, "start": today},
        )


def get_or_create_demo_tenant():
    """Returns (tenant_id, shop_name, shop_code) for the single shared demo
    shop, creating and seeding it on first call. is_demo=TRUE (with a
    partial unique index) makes this idempotent under concurrent first
    clicks: on a race, the loser's INSERT/UPDATE fails and it just re-reads
    the winner's row instead."""
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, shop_name, installation_id FROM tenants WHERE is_demo LIMIT 1")
        ).fetchone()
        if row:
            return str(row[0]), row[1], row[2]

    shop_name = "Demo Shop (Try Me)"
    tenant_id = create_tenant(shop_name)
    try:
        with _engine.begin() as conn:
            conn.execute(text("UPDATE tenants SET is_demo = TRUE WHERE id = :tid"), {"tid": tenant_id})
    except Exception:
        with _engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, shop_name, installation_id FROM tenants WHERE is_demo LIMIT 1")
            ).fetchone()
        return str(row[0]), row[1], row[2]

    save_shop_setup(
        tenant_id, address="123 Demo Street, Sample City", mobile="9999999999",
        gst_number="27DEMOG1234A1Z5", footer_message="Thank You, Visit Again!",
        terms="Goods once sold will not be taken back.",
    )
    register_user(tenant_id, DEMO_USERNAME, DEMO_PASSWORD, "Admin")
    activate_demo_subscription(tenant_id)

    from demo_data_saas import seed_demo_data
    seed_demo_data(tenant_id)

    with _engine.connect() as conn:
        shop_code = conn.execute(
            text("SELECT installation_id FROM tenants WHERE id = :tid"), {"tid": tenant_id}
        ).fetchone()[0]
    return tenant_id, shop_name, shop_code
