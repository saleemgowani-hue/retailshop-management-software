"""
generate_license_keys.py — Mint admin-issued License Keys for New Shop
Signup. Run this locally, with DATABASE_URL pointed at the SAME database
your deployed app uses; it prints the codes so you can hand them out
(email/WhatsApp/etc) to shops signing up.

Usage:
    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
    python3 generate_license_keys.py

Or edit DATABASE_URL directly below if you'd rather not use an env var.
Edit PLAN/COUNT below to mint a different batch.
"""

import os

# Uncomment and fill this in if you don't want to use an environment variable:
# os.environ["DATABASE_URL"] = "postgresql://user:pass@host:5432/dbname"

import database_saas as db

PLAN = "monthly"   # 'monthly' (30 days) or 'yearly' (365 days)
COUNT = 5           # how many keys to mint in this run

print(f"Generating {COUNT} '{PLAN}' license key(s) ...")
codes = [db.generate_license_key(PLAN) for _ in range(COUNT)]

print()
print("=" * 60)
print(f"{COUNT} {PLAN.upper()} LICENSE KEY(S) READY")
print("=" * 60)
for code in codes:
    print(f"  {code}")
print("=" * 60)
print("Each key is single-use — hand one to each shop for New Shop Signup.")
