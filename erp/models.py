from datetime import date, datetime, timedelta
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

MONEY = db.Numeric(12, 2)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(60), default="")
    unit_price = db.Column(MONEY, nullable=False, default=0)
    cost = db.Column(MONEY, nullable=False, default=0)
    qty_on_hand = db.Column(db.Integer, nullable=False, default=0)
    reorder_level = db.Column(db.Integer, nullable=False, default=0)

    @property
    def low_stock(self):
        return self.qty_on_hand <= self.reorder_level

    @property
    def stock_value(self):
        return Decimal(self.cost or 0) * self.qty_on_hand


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), default="")
    phone = db.Column(db.String(40), default="")
    address = db.Column(db.String(255), default="")


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), default="")
    phone = db.Column(db.String(40), default="")
    address = db.Column(db.String(255), default="")


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product = db.relationship("Product")
    change = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(40), nullable=False)
    reference = db.Column(db.String(40), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def adjust_stock(product, change, reason, reference=""):
    product.qty_on_hand += change
    db.session.add(
        StockMovement(product=product, change=change, reason=reason, reference=reference)
    )


class OrderMixin:
    @property
    def total(self):
        return sum((line.subtotal for line in self.lines), Decimal("0"))


class SalesOrder(OrderMixin, db.Model):
    STATUSES = ["draft", "confirmed", "shipped", "invoiced", "cancelled"]

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    customer = db.relationship("Customer", backref="sales_orders")
    order_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(20), nullable=False, default="draft")
    lines = db.relationship(
        "SalesOrderLine", backref="order", cascade="all, delete-orphan"
    )
    invoice = db.relationship("Invoice", backref="sales_order", uselist=False)

    @property
    def number(self):
        return f"SO-{self.id:05d}"


class SalesOrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("sales_order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product = db.relationship("Product")
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(MONEY, nullable=False)

    @property
    def subtotal(self):
        return Decimal(self.unit_price) * self.quantity


class PurchaseOrder(OrderMixin, db.Model):
    STATUSES = ["draft", "ordered", "received", "cancelled"]

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    supplier = db.relationship("Supplier", backref="purchase_orders")
    order_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(20), nullable=False, default="draft")
    lines = db.relationship(
        "PurchaseOrderLine", backref="order", cascade="all, delete-orphan"
    )

    @property
    def number(self):
        return f"PO-{self.id:05d}"


class PurchaseOrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("purchase_order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product = db.relationship("Product")
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(MONEY, nullable=False)

    @property
    def subtotal(self):
        return Decimal(self.unit_cost) * self.quantity


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sales_order_id = db.Column(
        db.Integer, db.ForeignKey("sales_order.id"), unique=True, nullable=False
    )
    issue_date = db.Column(db.Date, default=date.today)
    due_date = db.Column(db.Date, default=lambda: date.today() + timedelta(days=30))
    amount = db.Column(MONEY, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="unpaid")
    paid_date = db.Column(db.Date)

    @property
    def number(self):
        return f"INV-{self.id:05d}"

    @property
    def overdue(self):
        return self.status == "unpaid" and self.due_date < date.today()
