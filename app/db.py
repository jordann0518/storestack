"""SQLite data layer for storestack.

Money is stored as integer cents everywhere to avoid floating-point rounding
errors. Product names/prices are snapshotted into sale_items at checkout time
so receipts stay accurate even if a product is later renamed, repriced, or
deactivated.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class CheckoutError(Exception):
    """Raised when a sale cannot be completed (e.g. insufficient stock)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sku TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
    cost_cents INTEGER NOT NULL CHECK(cost_cents >= 0),
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    category TEXT DEFAULT '',
    vendor_id INTEGER REFERENCES vendors(id),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    payment_method TEXT NOT NULL DEFAULT 'cash',
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sale_items (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER REFERENCES products(id),
    product_name TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product ON sale_items(product_id);
CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);
"""


def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        cur = conn.execute(
            "SELECT value FROM settings WHERE key = 'low_stock_threshold'"
        )
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('low_stock_threshold', '5')"
            )
        conn.commit()
    finally:
        conn.close()


def cents_to_dollars(cents):
    return f"${cents / 100:,.2f}"


# ---------------------------------------------------------------- vendors ---

def list_vendors(conn):
    return conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()


def get_vendor(conn, vendor_id):
    return conn.execute(
        "SELECT * FROM vendors WHERE id = ?", (vendor_id,)
    ).fetchone()


def create_vendor(conn, name, contact="", email="", phone="", notes=""):
    cur = conn.execute(
        "INSERT INTO vendors (name, contact, email, phone, notes)"
        " VALUES (?, ?, ?, ?, ?)",
        (name.strip(), contact.strip(), email.strip(), phone.strip(), notes.strip()),
    )
    conn.commit()
    return cur.lastrowid


def update_vendor(conn, vendor_id, name, contact="", email="", phone="", notes=""):
    conn.execute(
        "UPDATE vendors SET name=?, contact=?, email=?, phone=?, notes=?"
        " WHERE id = ?",
        (name.strip(), contact.strip(), email.strip(), phone.strip(), notes.strip(), vendor_id),
    )
    conn.commit()


def delete_vendor(conn, vendor_id):
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE vendor_id = ?", (vendor_id,)
    ).fetchone()["c"]
    if count:
        raise ValueError("Cannot delete a vendor that still has products.")
    conn.execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
    conn.commit()


def vendor_totals(conn, vendor_id):
    """Units sold and revenue for one vendor (joins through live products)."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(si.quantity), 0) AS units_sold,
               COALESCE(SUM(si.quantity * si.unit_price_cents), 0) AS revenue_cents
        FROM sale_items si
        JOIN products p ON p.id = si.product_id
        WHERE p.vendor_id = ?
        """,
        (vendor_id,),
    ).fetchone()
    return row


def vendor_products(conn, vendor_id):
    return conn.execute(
        "SELECT * FROM products WHERE vendor_id = ? AND is_active = 1 ORDER BY name",
        (vendor_id,),
    ).fetchall()


# --------------------------------------------------------------- products ---

def list_products(conn, include_inactive=False):
    q = """
        SELECT p.*, v.name AS vendor_name,
               CASE WHEN p.price_cents > 0
                    THEN (p.price_cents - p.cost_cents) * 100.0 / p.price_cents
                    ELSE 0 END AS margin_pct
        FROM products p
        LEFT JOIN vendors v ON v.id = p.vendor_id
    """
    if not include_inactive:
        q += " WHERE p.is_active = 1"
    q += " ORDER BY p.name"
    return conn.execute(q).fetchall()


def get_product(conn, product_id):
    return conn.execute(
        "SELECT * FROM products WHERE id = ?", (product_id,)
    ).fetchone()


def create_product(conn, name, sku, price_cents, cost_cents, quantity,
                   category="", vendor_id=None, description=""):
    try:
        cur = conn.execute(
            """INSERT INTO products
               (name, sku, description, price_cents, cost_cents, quantity,
                category, vendor_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), sku.strip().upper(), description.strip(),
             price_cents, cost_cents, quantity, category.strip(), vendor_id),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Could not save product: {e}")
    conn.commit()
    return cur.lastrowid


def update_product(conn, product_id, name, sku, price_cents, cost_cents,
                   quantity, category="", vendor_id=None, description=""):
    try:
        conn.execute(
            """UPDATE products SET name=?, sku=?, description=?,
                   price_cents=?, cost_cents=?, quantity=?, category=?,
                   vendor_id=? WHERE id=?""",
            (name.strip(), sku.strip().upper(), description.strip(),
             price_cents, cost_cents, quantity, category.strip(), vendor_id,
             product_id),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Could not save product: {e}")
    conn.commit()


def delete_product(conn, product_id):
    """Soft-delete products that have sales history (keeps receipts intact);
    hard-delete only products that were never sold."""
    sold = conn.execute(
        "SELECT COUNT(*) AS c FROM sale_items WHERE product_id = ?",
        (product_id,),
    ).fetchone()["c"]
    if sold:
        conn.execute(
            "UPDATE products SET is_active = 0 WHERE id = ?", (product_id,)
        )
    else:
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    return sold > 0  # True if it was deactivated rather than deleted


def low_stock_products(conn, threshold):
    return conn.execute(
        """SELECT p.*, v.name AS vendor_name FROM products p
           LEFT JOIN vendors v ON v.id = p.vendor_id
           WHERE p.is_active = 1 AND p.quantity <= ?
           ORDER BY p.quantity, p.name""",
        (threshold,),
    ).fetchall()


# ------------------------------------------------------------------ sales ---

def complete_sale(conn, items, payment_method="cash", note="", created_at=None):
    """Record a sale atomically.

    `items` is a list of (product_id, quantity). The whole checkout runs
    inside a single transaction opened with BEGIN IMMEDIATE: the RESERVED
    lock is taken up front, so a second concurrent checkout blocks until
    this one commits or rolls back, then re-reads fresh stock inside its
    own transaction. That is what prevents two registers from overselling
    the last unit of the same item.

    Raises CheckoutError if any line cannot be fulfilled; nothing is
    written in that case.
    """
    if not items:
        raise CheckoutError("Cannot complete a sale with an empty cart.")

    created_at = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        lines = []
        total = 0
        for product_id, qty in items:
            if qty <= 0:
                raise CheckoutError("Quantities must be positive.")
            row = conn.execute(
                "SELECT * FROM products WHERE id = ? AND is_active = 1",
                (product_id,),
            ).fetchone()
            if row is None:
                raise CheckoutError(f"Product #{product_id} is unavailable.")
            if row["quantity"] < qty:
                raise CheckoutError(
                    f"Only {row['quantity']} × “{row['name']}” in stock "
                    f"(tried to sell {qty})."
                )
            conn.execute(
                "UPDATE products SET quantity = quantity - ? WHERE id = ?",
                (qty, product_id),
            )
            lines.append(row)
            total += row["price_cents"] * qty

        cur = conn.execute(
            "INSERT INTO sales (created_at, total_cents, payment_method, note)"
            " VALUES (?, ?, ?, ?)",
            (created_at, total, payment_method, note.strip()),
        )
        sale_id = cur.lastrowid
        for row, (product_id, qty) in zip(lines, items):
            conn.execute(
                """INSERT INTO sale_items
                   (sale_id, product_id, product_name, unit_price_cents,
                    unit_cost_cents, quantity)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sale_id, product_id, row["name"], row["price_cents"],
                 row["cost_cents"], qty),
            )
        conn.commit()
        return sale_id
    except Exception:
        conn.rollback()
        raise


def list_sales(conn, limit=100):
    return conn.execute(
        """SELECT s.*, COUNT(si.id) AS line_count
           FROM sales s LEFT JOIN sale_items si ON si.sale_id = s.id
           GROUP BY s.id ORDER BY s.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def get_sale(conn, sale_id):
    sale = conn.execute(
        "SELECT * FROM sales WHERE id = ?", (sale_id,)
    ).fetchone()
    if sale is None:
        return None, []
    items = conn.execute(
        "SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id",
        (sale_id,),
    ).fetchall()
    return sale, items


# --------------------------------------------------------------- reports ---

def revenue_by_day(conn, days=30):
    rows = conn.execute(
        """SELECT date(created_at) AS day,
                  COALESCE(SUM(total_cents), 0) AS revenue_cents
           FROM sales
           WHERE created_at >= datetime('now', ?)
           GROUP BY day ORDER BY day""",
        (f"-{days} days",),
    ).fetchall()
    return [(r["day"], r["revenue_cents"]) for r in rows]


def top_products(conn, limit=8):
    rows = conn.execute(
        """SELECT product_name,
                  SUM(quantity) AS units,
                  SUM(quantity * unit_price_cents) AS revenue_cents,
                  SUM(quantity * (unit_price_cents - unit_cost_cents)) AS profit_cents
           FROM sale_items
           GROUP BY product_name
           ORDER BY revenue_cents DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return rows


def dashboard_totals(conn):
    row = conn.execute(
        """SELECT COALESCE(SUM(total_cents), 0) AS lifetime_revenue,
                  COUNT(*) AS sale_count
           FROM sales"""
    ).fetchone()
    inv = conn.execute(
        """SELECT COALESCE(SUM(quantity * cost_cents), 0) AS inventory_cost,
                  COALESCE(SUM(quantity), 0) AS units_on_hand
           FROM products WHERE is_active = 1"""
    ).fetchone()
    return {
        "lifetime_revenue": row["lifetime_revenue"],
        "sale_count": row["sale_count"],
        "inventory_cost": inv["inventory_cost"],
        "units_on_hand": inv["units_on_hand"],
    }


# --------------------------------------------------------------- settings ---

def get_setting(conn, key, default=""):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
