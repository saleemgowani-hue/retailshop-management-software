"""
seed_demo_tenant.py — Run this ONCE after deployment to create a ready-to-
show demo shop: a tenant, an activated subscription, an Admin login, and
25 realistic sample records (products/suppliers/customers/purchases/
sales/expenses).

Usage (from your local machine, with DATABASE_URL set to the SAME
database your deployed app uses):

    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
    python3 seed_demo_tenant.py

Or edit DATABASE_URL directly below if you'd rather not use an env var.
Prints the Shop Code + login you can use to sign in immediately.
"""

import os

# Uncomment and fill this in if you don't want to use an environment variable:
# os.environ["DATABASE_URL"] = "postgresql://user:pass@host:5432/dbname"

import database_saas as db
import demo_data_saas as demo
from sqlalchemy import text

SHOP_NAME = "Demo Store"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "demo1234"

print(f"Creating tenant: {SHOP_NAME} ...")
tenant_id = db.create_tenant(SHOP_NAME)
db.save_shop_setup(
    tenant_id,
    address="123 Demo Street, Sample City",
    mobile="9999999999",
    gst_number="27DEMOG1234A1Z5",
    footer_message="Thank You, Visit Again!",
    terms="Goods once sold will not be taken back.",
)

print("Creating Admin login ...")
ok, msg = db.register_user(tenant_id, ADMIN_USERNAME, ADMIN_PASSWORD, "Admin")
if not ok:
    raise SystemExit(f"Could not create admin user: {msg}")

print("Activating a monthly subscription (30 days) ...")
db.create_or_renew_subscription(tenant_id, "monthly", 30)

print("Seeding 25 demo records ...")
count = demo.seed_demo_data(tenant_id)
print(f"  -> {count} records created")

with db.get_engine().connect() as conn:
    shop_code = conn.execute(
        text("SELECT installation_id FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    ).fetchone()[0]

print()
print("=" * 60)
print("DEMO SHOP READY")
print("=" * 60)
print(f"  Shop Code : {shop_code}")
print(f"  Username  : {ADMIN_USERNAME}")
print(f"  Password  : {ADMIN_PASSWORD}")
print("=" * 60)
print("Use these to log in to your deployed app.")
