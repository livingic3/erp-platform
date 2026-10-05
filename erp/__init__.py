import os
import secrets

from flask import Flask, abort, request, session
from markupsafe import Markup, escape

from .models import db


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{os.path.join(app.instance_path, 'erp.db')}"
        ),
        APP_NAME=os.environ.get("ERP_APP_NAME", "SWK-ERP"),
        CURRENCY=os.environ.get("ERP_CURRENCY", "RM"),
        WTF_CSRF_ENABLED=True,
    )
    if config:
        app.config.update(config)

    db.init_app(app)

    @app.before_request
    def csrf_protect():
        if request.method == "POST" and app.config["WTF_CSRF_ENABLED"]:
            token = session.get("_csrf")
            if not token or token != request.form.get("_csrf"):
                abort(400, "Invalid CSRF token")

    @app.context_processor
    def inject_globals():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return {
            "csrf_token": session["_csrf"],
            "currency": app.config["CURRENCY"],
            "app_name": app.config["APP_NAME"],
        }

    @app.template_filter("money")
    def money(value):
        return f"{app.config['CURRENCY']} {float(value or 0):,.2f}"

    status_colors = {
        "draft": "secondary", "confirmed": "primary", "ordered": "primary",
        "shipped": "info", "received": "success", "invoiced": "success",
        "cancelled": "dark", "paid": "success", "unpaid": "warning",
        "completed": "success", "voided": "dark",
    }

    @app.template_global()
    def status_badge(status):
        color = status_colors.get(status, "secondary")
        return Markup(f'<span class="badge badge-status bg-{color}">{escape(status)}</span>')

    from . import views, cli

    app.register_blueprint(views.bp)
    cli.register(app)

    with app.app_context():
        db.create_all()

    return app
