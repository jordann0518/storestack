"""HTTP routes for storestack."""

import csv
import io
import json
from decimal import Decimal, InvalidOperation

from flask import (
    flash, g, redirect, render_template, request, Response, session, url_for,
)

from . import db as data


def get_db():
    if "db" not in g:
        from flask import current_app
        g.db = data.get_connection(current_app.config["DB_PATH"])
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def parse_money(text, field_name):
    """'$12.50' or '12.50' -> 1249 cents. Raises ValueError on bad input."""
    text = (text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"“{field_name}” must be a number.")
    if amount < 0:
        raise ValueError(f"“{field_name}” cannot be negative.")
    return int((amount * 100).to_integral_value())


def parse_int(text, field_name, minimum=0):
    try:
        value = int((text or "").strip())
    except (ValueError, AttributeError):
        raise ValueError(f"“{field_name}” must be a whole number.")
    if value < minimum:
        raise ValueError(f"“{field_name}” cannot be less than {minimum}.")
    return value


def get_cart():
    return {int(k): int(v) for k, v in session.get("cart", {}).items()}


def save_cart(cart):
    session["cart"] = {str(k): int(v) for k, v in cart.items() if int(v) > 0}


def register_routes(app):
    app.teardown_appcontext(close_db)

    # ---------------------------------------------------------- dashboard ---
    @app.route("/")
    def dashboard():
        conn = get_db()
        threshold = int(data.get_setting(conn, "low_stock_threshold", "5"))
        days = data.revenue_by_day(conn, days=30)
        return render_template(
            "dashboard.html",
            totals=data.dashboard_totals(conn),
            revenue_labels=json.dumps([d for d, _ in days]),
            revenue_values=json.dumps([c / 100 for _, c in days]),
            top_products=data.top_products(conn),
            low_stock=data.low_stock_products(conn, threshold),
            threshold=threshold,
            cents=data.cents_to_dollars,
        )

    @app.route("/settings/threshold", methods=["POST"])
    def set_threshold():
        try:
            value = parse_int(request.form.get("threshold"), "threshold", minimum=0)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("dashboard"))
        data.set_setting(get_db(), "low_stock_threshold", str(value))
        flash(f"Low-stock alert threshold set to {value}.", "ok")
        return redirect(url_for("dashboard"))

    # ----------------------------------------------------------- products ---
    @app.route("/products")
    def products():
        conn = get_db()
        return render_template(
            "products.html",
            products=data.list_products(conn),
            cents=data.cents_to_dollars,
        )

    @app.route("/products/new", methods=["GET", "POST"])
    def product_new():
        conn = get_db()
        if request.method == "POST":
            try:
                pid = data.create_product(
                    conn,
                    name=request.form["name"],
                    sku=request.form["sku"],
                    price_cents=parse_money(request.form.get("price"), "price"),
                    cost_cents=parse_money(request.form.get("cost"), "cost"),
                    quantity=parse_int(request.form.get("quantity"), "quantity"),
                    category=request.form.get("category", ""),
                    vendor_id=int(request.form["vendor_id"])
                    if request.form.get("vendor_id") else None,
                    description=request.form.get("description", ""),
                )
            except (ValueError, KeyError) as e:
                flash(str(e), "error")
                return render_template(
                    "product_form.html", product=None,
                    vendors=data.list_vendors(conn), form=request.form,
                )
            flash("Product added.", "ok")
            return redirect(url_for("products"))
        return render_template(
            "product_form.html", product=None,
            vendors=data.list_vendors(conn), form={},
        )

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    def product_edit(product_id):
        conn = get_db()
        product = data.get_product(conn, product_id)
        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("products"))
        if request.method == "POST":
            try:
                data.update_product(
                    conn, product_id,
                    name=request.form["name"],
                    sku=request.form["sku"],
                    price_cents=parse_money(request.form.get("price"), "price"),
                    cost_cents=parse_money(request.form.get("cost"), "cost"),
                    quantity=parse_int(request.form.get("quantity"), "quantity"),
                    category=request.form.get("category", ""),
                    vendor_id=int(request.form["vendor_id"])
                    if request.form.get("vendor_id") else None,
                    description=request.form.get("description", ""),
                )
            except (ValueError, KeyError) as e:
                flash(str(e), "error")
                return render_template(
                    "product_form.html", product=product,
                    vendors=data.list_vendors(conn), form=request.form,
                )
            flash("Product updated.", "ok")
            return redirect(url_for("products"))
        return render_template(
            "product_form.html", product=product,
            vendors=data.list_vendors(conn), form=dict(product),
        )

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    def product_delete(product_id):
        deactivated = data.delete_product(get_db(), product_id)
        if deactivated:
            flash("Product has sales history, so it was deactivated "
                  "instead of deleted (receipts stay intact).", "ok")
        else:
            flash("Product deleted.", "ok")
        return redirect(url_for("products"))

    @app.route("/products/export.csv")
    def products_export():
        conn = get_db()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "sku", "description", "price", "cost",
                         "quantity", "category", "vendor"])
        vendors = {v["id"]: v["name"] for v in data.list_vendors(conn)}
        for p in data.list_products(conn, include_inactive=True):
            writer.writerow([
                p["name"], p["sku"], p["description"],
                f"{p['price_cents'] / 100:.2f}",
                f"{p['cost_cents'] / 100:.2f}",
                p["quantity"], p["category"],
                vendors.get(p["vendor_id"], ""),
            ])
        return Response(
            buf.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition":
                     "attachment; filename=storestack-products.csv"},
        )

    @app.route("/products/import", methods=["GET", "POST"])
    def products_import():
        if request.method == "POST":
            file = request.files.get("file")
            if file is None or not file.filename:
                flash("Choose a CSV file to import.", "error")
                return redirect(url_for("products_import"))
            try:
                text = file.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                flash("Could not read the file as UTF-8 CSV.", "error")
                return redirect(url_for("products_import"))
            reader = csv.DictReader(io.StringIO(text))
            created, updated, errors = import_products(get_db(), reader)
            flash(f"Import finished: {created} created, {updated} updated, "
                  f"{len(errors)} errors.", "ok" if not errors else "error")
            for e in errors[:10]:
                flash(e, "error")
            return redirect(url_for("products"))
        return render_template("import.html")

    # ------------------------------------------------------------ vendors ---
    @app.route("/vendors")
    def vendors():
        conn = get_db()
        rows = []
        for v in data.list_vendors(conn):
            totals = data.vendor_totals(conn, v["id"])
            rows.append((v, totals))
        return render_template("vendors.html", vendors=rows,
                               cents=data.cents_to_dollars)

    @app.route("/vendors/new", methods=["GET", "POST"])
    def vendor_new():
        return vendor_form(None)

    @app.route("/vendors/<int:vendor_id>/edit", methods=["GET", "POST"])
    def vendor_edit(vendor_id):
        return vendor_form(vendor_id)

    def vendor_form(vendor_id):
        conn = get_db()
        vendor = data.get_vendor(conn, vendor_id) if vendor_id else None
        if vendor_id and vendor is None:
            flash("Vendor not found.", "error")
            return redirect(url_for("vendors"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Vendor name is required.", "error")
                return render_template("vendor_form.html", vendor=vendor,
                                       form=request.form)
            kwargs = dict(
                name=name,
                contact=request.form.get("contact", ""),
                email=request.form.get("email", ""),
                phone=request.form.get("phone", ""),
                notes=request.form.get("notes", ""),
            )
            if vendor is None:
                data.create_vendor(conn, **kwargs)
                flash("Vendor added.", "ok")
            else:
                data.update_vendor(conn, vendor_id, **kwargs)
                flash("Vendor updated.", "ok")
            return redirect(url_for("vendors"))
        return render_template("vendor_form.html", vendor=vendor,
                               form=dict(vendor) if vendor else {})

    @app.route("/vendors/<int:vendor_id>")
    def vendor_detail(vendor_id):
        conn = get_db()
        vendor = data.get_vendor(conn, vendor_id)
        if vendor is None:
            flash("Vendor not found.", "error")
            return redirect(url_for("vendors"))
        return render_template(
            "vendor_detail.html",
            vendor=vendor,
            products=data.vendor_products(conn, vendor_id),
            totals=data.vendor_totals(conn, vendor_id),
            cents=data.cents_to_dollars,
        )

    @app.route("/vendors/<int:vendor_id>/delete", methods=["POST"])
    def vendor_delete(vendor_id):
        try:
            data.delete_vendor(get_db(), vendor_id)
        except ValueError as e:
            flash(str(e), "error")
        else:
            flash("Vendor deleted.", "ok")
        return redirect(url_for("vendors"))

    # ----------------------------------------------------------- checkout ---
    @app.route("/checkout")
    def checkout():
        conn = get_db()
        cart = get_cart()
        lines, total = [], 0
        for pid, qty in cart.items():
            p = data.get_product(conn, pid)
            if p is None or not p["is_active"]:
                continue
            lines.append({"product": p, "qty": qty,
                          "line_total": p["price_cents"] * qty})
            total += p["price_cents"] * qty
        return render_template(
            "checkout.html",
            products=data.list_products(conn),
            lines=lines, total=total,
            cents=data.cents_to_dollars,
        )

    @app.route("/checkout/add/<int:product_id>", methods=["POST"])
    def cart_add(product_id):
        qty = parse_int(request.form.get("quantity", "1"), "quantity", minimum=1)
        cart = get_cart()
        cart[product_id] = cart.get(product_id, 0) + qty
        save_cart(cart)
        return redirect(url_for("checkout"))

    @app.route("/checkout/remove/<int:product_id>", methods=["POST"])
    def cart_remove(product_id):
        cart = get_cart()
        cart.pop(product_id, None)
        save_cart(cart)
        return redirect(url_for("checkout"))

    @app.route("/checkout/complete", methods=["POST"])
    def checkout_complete():
        cart = get_cart()
        try:
            sale_id = data.complete_sale(
                get_db(),
                list(cart.items()),
                payment_method=request.form.get("payment_method", "cash"),
                note=request.form.get("note", ""),
            )
        except data.CheckoutError as e:
            flash(str(e), "error")
            return redirect(url_for("checkout"))
        save_cart({})
        flash("Sale completed.", "ok")
        return redirect(url_for("receipt", sale_id=sale_id))

    # -------------------------------------------------------------- sales ---
    @app.route("/sales")
    def sales():
        conn = get_db()
        return render_template("sales.html", sales=data.list_sales(conn),
                               cents=data.cents_to_dollars)

    @app.route("/sales/<int:sale_id>")
    def receipt(sale_id):
        sale, items = data.get_sale(get_db(), sale_id)
        if sale is None:
            flash("Sale not found.", "error")
            return redirect(url_for("sales"))
        return render_template("receipt.html", sale=sale, items=items,
                               cents=data.cents_to_dollars)


def import_products(conn, reader):
    """Upsert products from a CSV DictReader keyed by SKU.

    Expected columns: name, sku, price, cost, quantity.
    Optional: description, category, vendor (matched by name).
    Returns (created, updated, [error strings]).
    """
    created = updated = 0
    errors = []
    vendors = {v["name"].lower(): v["id"] for v in data.list_vendors(conn)}

    for lineno, row in enumerate(reader, start=2):
        try:
            name = (row.get("name") or "").strip()
            sku = (row.get("sku") or "").strip().upper()
            if not name or not sku:
                raise ValueError("name and sku are required")
            price = parse_money(row.get("price"), "price")
            cost = parse_money(row.get("cost"), "cost")
            quantity = parse_int(row.get("quantity"), "quantity")
            vendor_id = None
            vendor_name = (row.get("vendor") or "").strip().lower()
            if vendor_name:
                vendor_id = vendors.get(vendor_name)

            existing = conn.execute(
                "SELECT id FROM products WHERE sku = ?", (sku,)
            ).fetchone()
            if existing:
                data.update_product(
                    conn, existing["id"], name=name, sku=sku,
                    price_cents=price, cost_cents=cost, quantity=quantity,
                    category=(row.get("category") or "").strip(),
                    vendor_id=vendor_id,
                    description=(row.get("description") or "").strip(),
                )
                updated += 1
            else:
                data.create_product(
                    conn, name=name, sku=sku, price_cents=price,
                    cost_cents=cost, quantity=quantity,
                    category=(row.get("category") or "").strip(),
                    vendor_id=vendor_id,
                    description=(row.get("description") or "").strip(),
                )
                created += 1
        except (ValueError, KeyError) as e:
            errors.append(f"Row {lineno}: {e}")
    return created, updated, errors
