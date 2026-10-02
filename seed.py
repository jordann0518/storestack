#!/usr/bin/env python3
"""Seed storestack with realistic antique-store sample data.

Thin wrapper: creating the app auto-seeds an empty database, so this just
ensures the app boots and reports what is in the database.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from app import create_app  # noqa: E402
from app import db as data  # noqa: E402


def main():
    app = create_app()  # auto-seeds when the database is empty
    conn = data.get_connection(app.config["DB_PATH"])
    try:
        n_products = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
        n_vendors = conn.execute("SELECT COUNT(*) AS c FROM vendors").fetchone()["c"]
        n_sales = conn.execute("SELECT COUNT(*) AS c FROM sales").fetchone()["c"]
    finally:
        conn.close()
    print(f"Database ready: {n_vendors} vendors, {n_products} products, "
          f"{n_sales} sales -> {app.config['DB_PATH']}")


if __name__ == "__main__":
    main()
