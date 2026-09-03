-- ============================================================================
-- Retail Shop Management Software — PostgreSQL Multi-Tenant Schema
-- ============================================================================
-- Every business table carries tenant_id. This is the "Phase 6" schema from
-- SaaS_Migration_Analysis.md, Section J, implemented exactly as designed —
-- shared database, logical multi-tenancy, tenant_id on every row.
--
-- Run this ONCE against a fresh PostgreSQL database to create the SaaS schema.
-- Safe to re-run: every statement uses IF NOT EXISTS.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ----------------------------------------------------------------------------
-- TENANTS — one row per shop. This IS the tenant; every other table below
-- points back to it via tenant_id.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_name       TEXT NOT NULL,
    installation_id TEXT UNIQUE,          -- carried over from the old local "shop_id", for support lookups
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one shared "Try Demo" tenant at a time — makes
-- get_or_create_demo_tenant() safe under concurrent first clicks.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_single_demo ON tenants (is_demo) WHERE is_demo;

-- ----------------------------------------------------------------------------
-- LICENSE_KEYS — admin-issued, one-time activation codes. Signup requires a
-- valid, unused key; the key itself carries the plan (monthly/yearly) and
-- period length, so signup activates the subscription immediately instead
-- of leaving new shops locked out pending manual admin action.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS license_keys (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code               TEXT UNIQUE NOT NULL,
    plan               TEXT NOT NULL CHECK (plan IN ('monthly', 'yearly')),
    days               INTEGER NOT NULL CHECK (days > 0),
    used_by_tenant_id  UUID REFERENCES tenants(id) ON DELETE SET NULL,
    used_at            TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_license_keys_code ON license_keys(code);

-- ----------------------------------------------------------------------------
-- SUBSCRIPTIONS — replaces license.py entirely. No free trial (per spec):
-- a tenant simply has no row here (or a non-active row) until a plan is
-- assigned. status is a stored, queryable value (not recomputed inline
-- everywhere), refreshed by a scheduled job comparing current_period_end.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plan                  TEXT NOT NULL CHECK (plan IN ('monthly', 'yearly')),
    status                TEXT NOT NULL CHECK (status IN ('active', 'expired', 'suspended', 'cancelled', 'demo'))
                              DEFAULT 'active',
    current_period_start  DATE NOT NULL,
    current_period_end    DATE NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant ON subscriptions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status_end ON subscriptions(status, current_period_end);

-- ----------------------------------------------------------------------------
-- USERS — username unique PER TENANT now, not globally (Phase 4 fix).
-- Same PBKDF2-HMAC-SHA256 + per-user salt scheme as the existing app —
-- deliberately unchanged, since Section N's security audit found this
-- part of the original design was already sound.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    username    TEXT NOT NULL,
    password    TEXT NOT NULL,   -- PBKDF2-HMAC-SHA256 hash, hex
    salt        TEXT NOT NULL,   -- per-user random salt, hex
    role        TEXT NOT NULL CHECK (role IN ('Admin', 'Manager', 'Cashier')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, username)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- ----------------------------------------------------------------------------
-- SHOP_SETTINGS — one row PER TENANT (was: one row total, hardcoded id=1).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shop_settings (
    tenant_id           UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    address             TEXT DEFAULT '',
    mobile              TEXT DEFAULT '',
    gst_number          TEXT DEFAULT '',
    footer_message      TEXT DEFAULT 'Thank You, Visit Again!',
    terms               TEXT DEFAULT 'Goods once sold will not be taken back.',
    barcode_disclaimer  TEXT DEFAULT '',
    gsheet_enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    gsheet_id           TEXT,
    gsheet_creds        TEXT,     -- JSON content itself (or a secrets-manager reference), NOT a local file path
    configured          BOOLEAN NOT NULL DEFAULT FALSE
);

-- ----------------------------------------------------------------------------
-- PRODUCTS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    barcode           TEXT,
    category          TEXT,
    brand             TEXT,
    unit              TEXT,
    purchase_price    NUMERIC(12,2) DEFAULT 0,
    selling_price     NUMERIC(12,2) DEFAULT 0,
    gst               NUMERIC(5,2) DEFAULT 0,
    opening_stock     NUMERIC(12,2) DEFAULT 0,
    minimum_stock     NUMERIC(12,2) DEFAULT 0,
    default_discount  NUMERIC(12,2) DEFAULT 0,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_products_tenant_active ON products(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_products_tenant_barcode ON products(tenant_id, barcode);

-- ----------------------------------------------------------------------------
-- SUPPLIERS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    mobile      TEXT,
    address     TEXT,
    gst_number  TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_suppliers_tenant_active ON suppliers(tenant_id, is_active);

-- ----------------------------------------------------------------------------
-- CUSTOMERS — mobile unique PER TENANT now (two shops WILL share a customer
-- phone number in the real world).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    mobile      TEXT,
    address     TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (tenant_id, mobile)
);
CREATE INDEX IF NOT EXISTS idx_customers_tenant_active ON customers(tenant_id, is_active);

-- ----------------------------------------------------------------------------
-- PURCHASES
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchases (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    purchase_date     DATE NOT NULL,
    supplier_id       UUID REFERENCES suppliers(id),
    product_id        UUID REFERENCES products(id),
    quantity          NUMERIC(12,2) DEFAULT 0,
    purchase_price    NUMERIC(12,2) DEFAULT 0,
    discount          NUMERIC(12,2) DEFAULT 0,
    gst               NUMERIC(5,2) DEFAULT 0,
    transport         NUMERIC(12,2) DEFAULT 0,
    total_amount      NUMERIC(12,2) DEFAULT 0,
    paid_amount       NUMERIC(12,2) DEFAULT 0,
    bill_photo_path   TEXT   -- becomes an object-storage URL/key in SaaS, not a local path
);
CREATE INDEX IF NOT EXISTS idx_purchases_tenant_date ON purchases(tenant_id, purchase_date);

-- ----------------------------------------------------------------------------
-- SALES (was "bills") — bill_number unique PER TENANT now.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    bill_number       TEXT NOT NULL,
    bill_date         DATE NOT NULL,
    customer_name     TEXT,
    customer_mobile   TEXT,
    payment_mode      TEXT CHECK (payment_mode IN ('Cash','UPI','Card','Mixed')),
    subtotal          NUMERIC(12,2) DEFAULT 0,
    discount          NUMERIC(12,2) DEFAULT 0,
    gst               NUMERIC(12,2) DEFAULT 0,
    grand_total       NUMERIC(12,2) DEFAULT 0,
    cash_amount       NUMERIC(12,2) DEFAULT 0,
    upi_amount        NUMERIC(12,2) DEFAULT 0,
    UNIQUE (tenant_id, bill_number)
);
CREATE INDEX IF NOT EXISTS idx_sales_tenant_date ON sales(tenant_id, bill_date);

-- ----------------------------------------------------------------------------
-- SALE_ITEMS (was "bill_items") — tenant_id denormalized here too,
-- deliberately, as defense-in-depth (Section J note: don't rely purely on
-- the join back to sales).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sale_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    sale_id         UUID NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    product_id      UUID REFERENCES products(id),
    quantity        NUMERIC(12,2) DEFAULT 0,
    selling_price   NUMERIC(12,2) DEFAULT 0,
    total           NUMERIC(12,2) DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sale_items_tenant_sale ON sale_items(tenant_id, sale_id);

-- ----------------------------------------------------------------------------
-- EXPENSES
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expenses (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    expense_date   DATE NOT NULL,
    expense_type   TEXT,
    amount         NUMERIC(12,2) DEFAULT 0,
    remarks        TEXT
);
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_date ON expenses(tenant_id, expense_date);

-- ----------------------------------------------------------------------------
-- SUPPLIER_PAYMENTS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supplier_payments (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    purchase_id    UUID REFERENCES purchases(id),
    supplier_id    UUID REFERENCES suppliers(id),
    payment_date   DATE NOT NULL,
    cash_amount    NUMERIC(12,2) DEFAULT 0,
    upi_amount     NUMERIC(12,2) DEFAULT 0,
    total_amount   NUMERIC(12,2) DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_tenant_date ON supplier_payments(tenant_id, payment_date);

-- ----------------------------------------------------------------------------
-- AUDIT_LOGS — new table recommended in Section N (security audit, MEDIUM
-- finding: "no audit logging anywhere").
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID REFERENCES users(id),
    action      TEXT NOT NULL,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created ON audit_logs(tenant_id, created_at);

-- ----------------------------------------------------------------------------
-- Row Level Security — added as DEFENSE IN DEPTH, disabled by default until
-- the application layer's tenant filtering is proven correct (per the
-- analysis's own instruction not to enable this blindly). To activate later:
--   SET app.tenant_id = '<uuid>';   -- set once per session/request
--   ALTER TABLE products ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY tenant_isolation ON products
--       USING (tenant_id = current_setting('app.tenant_id')::uuid);
-- (repeat per table). Left commented out intentionally for this first pass.
-- ----------------------------------------------------------------------------
