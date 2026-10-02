"""Sample data for storestack demos and fresh deployments.

Pure data + seeding logic: takes an open DB connection, writes vendors,
products, and ~6 weeks of sales history through the real checkout path so
stock levels stay consistent. Does not create the app (see seed.py for the
CLI wrapper, and app/__init__.py auto-seeds empty databases on startup).
"""

import random
from datetime import datetime, timedelta, timezone

from . import db as data

VENDORS = [
    ("Bluebird Pickers", "Dale Whitaker", "dale@bluebirdpickers.example", "256-555-0142",
     "Estate cleanouts across Lauderdale County. Strong on furniture."),
    ("Magnolia Estate Sales", "Priya Raman", "priya@magnoliaestate.example", "256-555-0198",
     "Monthly estate sales; glassware and china specialist."),
    ("Riverbend Vintage Co.", "Tom Okafor", "tom@riverbendvintage.example", "256-555-0117",
     "Wholesale vintage lots. Good prices, mixed condition."),
    ("Hazel's Attic Finds", "Hazel Brooks", "", "256-555-0163",
     "Local picker, textiles and smalls. Cash only."),
]

# (name, sku, description, price, cost, qty, category, vendor_index)
PRODUCTS = [
    ("Oak Rocking Chair, c. 1920", "FURN-001", "Solid oak rocker, tight joints, original finish.", 189.00, 85.00, 2, "Furniture", 0),
    ("Farmhouse Dining Table", "FURN-002", "Six-foot pine farm table, seats 8.", 425.00, 190.00, 1, "Furniture", 0),
    ("Victorian Brass Floor Lamp", "LITE-001", "Rewired, new shade, working dimmer.", 145.00, 60.00, 3, "Lighting", 1),
    ("Depression Glass Dinner Set (24 pc)", "GLAS-001", "Pink depression glass, no chips.", 120.00, 45.00, 4, "Glassware", 1),
    ("Cut Crystal Decanter Set", "GLAS-002", "Decanter plus 6 glasses, etched floral.", 95.00, 38.00, 5, "Glassware", 1),
    ("Hand-stitched Wedding Ring Quilt", "TEXT-001", "Queen size, feed-sack fabrics, excellent condition.", 275.00, 110.00, 2, "Textiles", 3),
    ("Crocheted Afghan Throw", "TEXT-002", "Granny-square afghan, vibrant 1970s colors.", 48.00, 12.00, 8, "Textiles", 3),
    ("Cast Iron Skillet No. 8", "KITC-001", "Griswold-style, smooth cooking surface, seasoned.", 65.00, 22.00, 6, "Kitchen", 2),
    ("Copper Kettle", "KITC-002", "Polished copper, dovetailed seams.", 78.00, 30.00, 4, "Kitchen", 2),
    ("Tin Advertising Sign — Cola", "DECO-001", "Embossed tin sign, 28x20 in, bright colors.", 85.00, 28.00, 5, "Decor", 2),
    ("Porcelain Doll Collection (6)", "COLL-001", "Bisque dolls with original clothing.", 150.00, 62.00, 2, "Collectibles", 0),
    ("Vinyl Record Lot (40 LPs)", "MEDI-001", "Classic rock and soul, mostly VG+.", 110.00, 40.00, 3, "Media", 2),
    ("Brass Mantel Clock", "DECO-002", "Westminster chime, serviced 2024.", 165.00, 70.00, 2, "Decor", 1),
    ("Stoneware Crock, 3 gal", "KITC-003", "Salt-glazed crock with cobalt decoration.", 58.00, 18.00, 7, "Kitchen", 0),
    ("Oak Picture Frames (set of 4)", "DECO-003", "Gilded oak frames, assorted sizes.", 42.00, 10.00, 10, "Decor", 3),
    ("Sterling Silver Flatware (32 pc)", "COLL-002", "Monogrammed, full service for 8.", 320.00, 140.00, 1, "Collectibles", 1),
]

PAYMENT_METHODS = ["cash", "cash", "cash", "card", "card", "check"]


def seed_database(conn):
    """Write sample vendors, products, and sales history into an empty DB.

    Raises RuntimeError if the database already has products (refuses to
    seed twice). Does not close the connection.
    """
    existing = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    if existing:
        raise RuntimeError("Database already has products; refusing to seed twice.")

    vendor_ids = [data.create_vendor(conn, *v) for v in VENDORS]

    product_ids = []
    for name, sku, desc, price, cost, qty, category, vi in PRODUCTS:
        pid = data.create_product(
            conn, name=name, sku=sku, description=desc,
            price_cents=int(price * 100), cost_cents=int(cost * 100),
            quantity=qty, category=category, vendor_id=vendor_ids[vi],
        )
        product_ids.append(pid)

    # Six weeks of plausible sales through the real checkout path.
    random.seed(20261001)
    now = datetime.now(timezone.utc)
    sale_count = 0
    for day_offset in range(42, 0, -1):
        day = now - timedelta(days=day_offset)
        if day.weekday() == 0:  # closed Mondays
            continue
        for _ in range(random.randint(0, 3)):
            n_lines = random.randint(1, 3)
            items = []
            for pid in random.sample(product_ids, k=min(n_lines, len(product_ids))):
                p = data.get_product(conn, pid)
                if p and p["quantity"] > 0:
                    items.append((pid, random.randint(1, min(2, p["quantity"]))))
            if not items:
                continue
            ts = (day.replace(hour=random.randint(9, 17),
                              minute=random.randint(0, 59))).isoformat()
            try:
                data.complete_sale(
                    conn, items,
                    payment_method=random.choice(PAYMENT_METHODS),
                    created_at=ts,
                )
                sale_count += 1
            except data.CheckoutError:
                continue

    return {"vendors": len(VENDORS), "products": len(PRODUCTS), "sales": sale_count}
