# Nimbus ERP

A lightweight ERP built with Flask + SQLAlchemy (SQLite by default).

## Modules
- **Dashboard** – revenue, receivables, inventory value, open orders, low-stock & overdue alerts, 6-month revenue chart
- **Inventory** – products (SKU, price, cost, reorder level), manual stock adjustments, full stock-movement ledger
- **Sales** – customers, sales orders (draft → confirmed → shipped → invoiced), stock check on confirm, stock deducted on ship
- **Invoicing** – invoices generated from shipped orders, due dates, overdue tracking, mark paid, printable view
- **Purchasing** – suppliers, purchase orders (draft → ordered → received), stock and product cost updated on receipt
- **Users** – login, admin/staff roles, admin-only user management; CSRF protection on all forms

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/flask --app erp init-db --seed      # --reset to wipe; omit --seed for an empty DB
.venv/bin/flask --app erp run --port 5000
```
Log in as `admin` / `admin` (set `ERP_ADMIN_PASSWORD` before `init-db` to change it).

Config via env vars: `SECRET_KEY` (set in production), `DATABASE_URL` (e.g. a Postgres URL), `ERP_CURRENCY` (default `$`).

## Test
```bash
.venv/bin/python -m pytest -q
```
