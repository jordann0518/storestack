# storestack

A full-stack small-retail management web app: inventory, vendors, point-of-sale
checkout, and a sales analytics dashboard. Built with **Python, Flask, and
SQLite** — no frontend framework, no ORM, just clean server-rendered pages and
hand-written CSS.

This project grew out of real work running an antique store: tracking
inventory across vendors, modernizing checkout, and using sales data to decide
what to stock. storestack is that experience distilled into code.

## Features

- **Inventory management** — product CRUD with SKU, price, cost, quantity,
  category, and vendor. Profit margin computed per product.
- **Vendor management** — vendor profiles with per-vendor totals (units sold,
  revenue) and their active product list.
- **Point-of-sale checkout** — build a cart, complete a sale, stock decrements
  automatically, printable receipt view.
- **Analytics dashboard** — revenue-over-time line chart and top-products bar
  chart (Chart.js), lifetime stats, and low-stock alerts with a configurable
  threshold.
- **CSV import/export** — export the full catalog; import upserts by SKU with
  per-row error reporting.
- **Sales history** — every sale with line items, payment method, and a
  receipt that stays accurate forever (prices are snapshotted at checkout).

## Screenshots

*(Add screenshots here — dashboard, checkout, receipt.)*

- `docs/dashboard.png` — analytics dashboard with revenue chart
- `docs/checkout.png` — point-of-sale cart and checkout flow
- `docs/receipt.png` — printable receipt

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python seed.py        # load realistic antique-store sample data
python run.py         # start the server
```

Open http://127.0.0.1:5000. The database is created at `storestack.db`
automatically; set `STORESTACK_DB` to use a different path and
`STORESTACK_SECRET` to override the dev session secret.

## Project structure

```
storestack/
├── run.py              # entry point: create_app().run()
├── seed.py             # realistic antique-store sample data
├── app/
│   ├── __init__.py     # Flask application factory
│   ├── db.py           # SQLite schema + all data access (the "model" layer)
│   └── routes.py       # HTTP routes, form parsing, CSV import/export
├── templates/          # Jinja2 pages (dashboard, inventory, checkout, ...)
└── static/style.css    # hand-written stylesheet, no framework
```

## Architecture

### Data model

Four core tables plus a settings table:

- `vendors` — who supplies the merchandise.
- `products` — the catalog. Money is stored as **integer cents**, never
  floats, so totals can't drift by a penny.
- `sales` — one row per completed transaction (timestamp, total, payment
  method).
- `sale_items` — line items. Each row **snapshots the product name, price,
  and cost at sale time**, so a receipt from March still shows March's
  prices even if the product is renamed, repriced, or deactivated later.
- `settings` — key/value store (e.g. the low-stock alert threshold).

### How sales stay consistent

A checkout is a classic read-modify-write race: two registers could both
read "1 left" and both sell it. storestack prevents that with a single
database transaction:

1. `complete_sale()` opens the transaction with **`BEGIN IMMEDIATE`**.
   SQLite is single-writer, and `BEGIN IMMEDIATE` takes the RESERVED lock
   up front — a second concurrent checkout *blocks* on its own `BEGIN`
   instead of reading stale stock.
2. Inside the transaction, each line is re-validated: product exists, is
   active, and has enough quantity. If any line fails, the whole sale is
   rolled back and **nothing is written** (all-or-nothing).
3. Stock decrements, the `sales` row, and all `sale_items` rows commit
   together. After commit, the waiting checkout re-reads fresh quantities
   and either succeeds or reports "insufficient stock."

This is the same pattern real POS systems use on a single database:
serialize the critical section, validate inside it, commit atomically.

### Deleting products with sales history

Hard-deleting a sold product would orphan its `sale_items` and break old
receipts. Instead, products with any sales history are **soft-deleted**
(`is_active = 0`): they disappear from inventory and checkout but their
history stays queryable. Products that were never sold are deleted for
real. Vendors can't be deleted while they still have products.

### CSV import

Import matches rows by SKU: existing SKUs are updated, new ones inserted.
Each row is validated independently (price/cost/quantity parsing, required
fields), so one bad row can't corrupt the catalog — errors are collected
and reported per row after the import finishes.

## Design decisions

- **No ORM** — raw SQL with `sqlite3` keeps every query visible and is
  plenty for this scale; row factories make results template-friendly.
- **Integer cents** — avoids the classic `0.1 + 0.2` float problem in money.
- **Server-rendered Jinja templates** — no build step, no JS framework;
  Chart.js is the only client-side library (loaded via CDN for charts).
- **Session cart** — the in-progress sale lives in Flask's signed session
  cookie, so no half-finished state touches the database.
- **Config via environment** — `STORESTACK_DB` and `STORESTACK_SECRET`
  keep deployment concerns out of the code.
