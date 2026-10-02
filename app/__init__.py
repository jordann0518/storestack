"""Flask application factory for storestack."""

import os

from flask import Flask

from . import db as data
from .routes import register_routes


def create_app(db_path=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "..", "static"),
    )
    app.secret_key = os.environ.get("STORESTACK_SECRET", "storestack-dev-secret")

    if db_path is None:
        db_path = os.environ.get(
            "STORESTACK_DB",
            os.path.join(os.path.dirname(__file__), "..", "storestack.db"),
        )
    app.config["DB_PATH"] = os.path.abspath(db_path)

    data.init_db(app.config["DB_PATH"])

    # Fresh deployments (e.g. Render's ephemeral filesystem) start with an
    # empty database. Seed sample data automatically so the demo is usable
    # immediately; never touches a database that already has products.
    conn = data.get_connection(app.config["DB_PATH"])
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
        if n == 0:
            from . import seed_data
            summary = seed_data.seed_database(conn)
            app.logger.info("Seeded sample data: %s", summary)
    finally:
        conn.close()

    register_routes(app)
    return app
