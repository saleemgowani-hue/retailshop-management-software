# Retail Shop Management Software — SaaS Edition

Multi-tenant retail shop management software (Billing/POS, Products,
Suppliers, Customers, Stock Purchase, Expenses, Reports) — deploy once,
serve unlimited shops, each shop's data fully isolated.

## What's Inside

| File | Purpose |
|---|---|
| `app.py` | Main Streamlit app (entry point) |
| `database_saas.py` | Database connection, auth, subscriptions |
| `pos_saas.py`, `products_saas.py`, `suppliers_saas.py`, `customers_saas.py`, `expenses_saas.py`, `stock_purchase_saas.py`, `reports_saas.py` | Business logic, all tenant-scoped |
| `barcode_saas.py` | Barcode generation & label printing |
| `receipts_saas.py` | 80mm Thermal / A4 / A5 invoice printing |
| `ocr_saas.py` | Bill-photo text extraction (OCR) |
| `gsheet_saas.py` | Google Sheets sync (optional) |
| `bulk_import_export_saas.py` | Excel bulk product import/export |
| `demo_data_saas.py` | Seeds 25 sample records for demos |
| `schema_postgres.sql` | Database schema — run this once |
| `seed_demo_tenant.py` | One-time script: creates a ready demo shop |
| `requirements.txt` | Python dependencies |
| `packages.txt` | System package (Tesseract OCR) for Streamlit Cloud |

---

## Deployment Steps

### Step 1 — Create a PostgreSQL Database

Any of these have a free tier that works fine for this app:

- **[Supabase](https://supabase.com/)** (recommended — easiest free tier)
- **[Neon](https://neon.tech/)**
- **[Railway](https://railway.app/)**

After creating a project, copy the **connection string** — it looks like:
```
postgresql://username:password@host:5432/dbname
```

### Step 2 — Run the Schema

Using your database provider's SQL editor (Supabase/Neon both have one
built into their dashboard), paste the entire contents of
`schema_postgres.sql` and run it once. This creates every table.

### Step 3 — Push This Folder to GitHub

```bash
git init
git add .
git commit -m "Initial commit — Retail Shop SaaS"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

> `.gitignore` already excludes `.streamlit/secrets.toml` — your real
> database password will never get committed by accident.

### Step 4 — Deploy on Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io/) and sign in
   with GitHub.
2. Click **"New app"**.
3. Select your repository, branch `main`, and main file path `app.py`.
4. Click **"Deploy"** — it will fail on first load until Step 5 is done
   (no database connection yet), that's expected.

### Step 5 — Add Your Database Secret

1. On your app's page in Streamlit Cloud, click **⋮ → Settings → Secrets**.
2. Paste this (with your real connection string):
   ```toml
   DATABASE_URL = "postgresql://username:password@host:5432/dbname"
   ```
3. Save. The app will restart automatically and connect to your database.

### Step 6 — Create Your First Real Shop

1. Open your deployed app's URL.
2. Click **"New Shop Signup"**, fill in Shop Name / Admin Username /
   Password, submit.
3. Note the **Shop Code** shown — this is needed to log in.
4. A brand-new shop has **no subscription yet (no free trial, by
   design)** — you (the seller/admin) activate one manually for now:
   run this once from your own computer, with `DATABASE_URL` pointed at
   the same database:
   ```python
   import database_saas as db
   # find the tenant_id for the shop you just created:
   from sqlalchemy import text
   with db.get_engine().connect() as conn:
       tenant_id = conn.execute(
           text("SELECT id FROM tenants WHERE installation_id = :code"),
           {"code": "PASTE_SHOP_CODE_HERE"}
       ).fetchone()[0]
   db.create_or_renew_subscription(str(tenant_id), "monthly", 30)  # or "yearly", 365
   ```
5. Log in with the Shop Code + Username + Password from step 6.2.

### Step 7 — (Optional) Create a Demo Shop for Sales Demos

From your own computer, with `DATABASE_URL` set to your deployed
database:
```bash
export DATABASE_URL="postgresql://username:password@host:5432/dbname"
python3 seed_demo_tenant.py
```
This prints a ready-to-use **Shop Code / Username / Password** with 25
sample records already loaded — perfect for showing prospective
customers a full, realistic Dashboard without touching real shop data.

---

## Important Notes

- **No free trial by design** — a new shop's subscription must be
  activated manually (Step 6.4) until you wire up real payment
  processing. This matches the business requirement given during
  development.
- **Google Sheets Sync** is optional per-shop — each shop uploads its
  own service-account JSON and Sheet ID under Settings; nothing is
  shared between shops.
- **OCR** requires `packages.txt` (already included) — Streamlit Cloud
  installs Tesseract automatically at build time. If OCR ever shows
  "library not installed," check that `packages.txt` is present in
  your repo root.
- **Bill photo uploads** are NOT saved to permanent storage in this
  version (Streamlit Cloud's filesystem is temporary) — the OCR
  text-extraction still works on an uploaded photo in the moment, but
  wire up object storage (e.g. AWS S3, Supabase Storage) if you need to
  keep bill photos long-term.
- **Printing** (Barcode labels, 80mm/A4/A5 invoices) works exactly the
  same on Streamlit Cloud as it did locally — printing happens in the
  shopkeeper's own browser, not on the server.

## Local Testing (Before Deploying)

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://username:password@host:5432/dbname"
streamlit run app.py
```
