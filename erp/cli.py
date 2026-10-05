import os
import math
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import click

from . import services
from .models import (
    Customer, PosSale, Product, PurchaseOrder, PurchaseOrderLine, SalesOrder,
    SalesOrderLine, Supplier, User, adjust_stock, db,
)


def ensure_admin():
    if not User.query.filter_by(username="admin").first():
        user = User(username="admin", role="admin")
        user.set_password(os.environ.get("ERP_ADMIN_PASSWORD", "admin"))
        db.session.add(user)
        db.session.commit()


def seed_demo():
    rng = random.Random(42)
    products = [
        Product(sku=sku, name=name, category=cat, unit_price=Decimal(price),
                cost=Decimal(price) * Decimal("0.6"), qty_on_hand=0, reorder_level=reorder)
        for sku, name, cat, price, reorder in [
            ("WID-001", "Steel Widget", "Hardware", "12.50", 20),
            ("WID-002", "Aluminium Widget", "Hardware", "15.00", 20),
            ("BOLT-10", "M10 Bolt (box of 50)", "Fasteners", "8.75", 30),
            ("NUT-10", "M10 Nut (box of 50)", "Fasteners", "6.25", 30),
            ("GEAR-A", "Gear Assembly A", "Assemblies", "89.00", 5),
            ("GEAR-B", "Gear Assembly B", "Assemblies", "129.00", 5),
            ("MOTOR-S", "Small DC Motor", "Electrical", "45.00", 10),
            ("CABLE-5", "Power Cable 5m", "Electrical", "9.90", 25),
        ]
    ]
    customers = [Customer(name=n, email=e) for n, e in [
        ("Acme Manufacturing", "orders@acme.example"),
        ("Globex Corp", "purchasing@globex.example"),
        ("Initech", "ap@initech.example"),
        ("Umbrella Industries", "supply@umbrella.example"),
    ]]
    suppliers = [Supplier(name=n, email=e) for n, e in [
        ("Steelworks Ltd", "sales@steelworks.example"),
        ("FastenAll Co", "orders@fastenall.example"),
        ("VoltParts", "hello@voltparts.example"),
    ]]
    db.session.add_all(products + customers + suppliers)
    db.session.flush()

    for i, supplier in enumerate(suppliers):
        po = PurchaseOrder(supplier=supplier, order_date=date.today() - timedelta(days=120 - i))
        for p in products[i::3]:
            po.lines.append(PurchaseOrderLine(product=p, quantity=rng.randint(60, 150), unit_cost=p.cost))
        db.session.add(po)
        db.session.flush()
        services.place_purchase_order(po)
        services.receive_purchase_order(po)

    for i in range(18):
        so = SalesOrder(customer=rng.choice(customers),
                        order_date=date.today() - timedelta(days=rng.randint(0, 150)))
        for p in rng.sample(products, rng.randint(1, 3)):
            so.lines.append(SalesOrderLine(product=p, quantity=rng.randint(1, 8), unit_price=p.unit_price))
        db.session.add(so)
        db.session.flush()
        stage = i % 5
        if stage >= 1:
            services.confirm_sales_order(so)
        if stage >= 2:
            services.ship_sales_order(so)
        if stage >= 3:
            inv = services.invoice_sales_order(so)
            db.session.flush()
            inv.issue_date = so.order_date
            inv.due_date = so.order_date + timedelta(days=30)
            if stage == 4:
                services.mark_invoice_paid(inv)

    cashier = User.query.filter_by(username="admin").first()
    for i in range(24):
        method = rng.choice(["cash", "cash", "card", "ewallet"])
        sale = PosSale(cashier=cashier, payment_method=method,
                       customer=rng.choice(customers) if rng.random() < 0.2 else None)
        lines = [(p, rng.randint(1, 3), p.unit_price) for p in rng.sample(products, rng.randint(1, 3))]
        total = sum(Decimal(price) * qty for _, qty, price in lines)
        tendered = Decimal(math.ceil(total / 10) * 10) if method == "cash" else None
        services.pos_checkout(sale, lines, tendered)
        sale.created_at = datetime.now().replace(second=0, microsecond=0) - timedelta(
            days=0 if i < 5 else rng.randint(1, 60), minutes=rng.randint(0, 600))
    services.void_pos_sale(sale)

    low = products[5]
    adjust_stock(low, -(low.qty_on_hand - 2), "adjustment", "demo")
    db.session.add(PurchaseOrder(supplier=suppliers[0]))
    db.session.commit()


def register(app):
    @app.cli.command("init-db")
    @click.option("--seed", is_flag=True, help="Load demo data.")
    @click.option("--reset", is_flag=True, help="Drop all tables first.")
    def init_db(seed, reset):
        """Create tables, the admin user, and optionally demo data."""
        if reset:
            db.drop_all()
        db.create_all()
        ensure_admin()
        if seed:
            if Product.query.first():
                click.echo("Database already has data; skipping seed (use --reset).")
            else:
                seed_demo()
                click.echo("Demo data loaded.")
        click.echo("Database ready. Login: admin / $ERP_ADMIN_PASSWORD (default 'admin').")
