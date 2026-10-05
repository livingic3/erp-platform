import pytest

from erp import create_app
from erp.cli import ensure_admin, seed_demo
from erp.models import Customer, Invoice, Product, SalesOrder, StockMovement, Supplier, db


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://", "WTF_CSRF_ENABLED": False})
    with app.app_context():
        ensure_admin()
        db.session.add_all([
            Product(sku="A", name="Alpha", unit_price=10, cost=6, qty_on_hand=5, reorder_level=2),
            Customer(name="Cust"),
            Supplier(name="Supp"),
        ])
        db.session.commit()
        yield app


@pytest.fixture
def client(app):
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "admin"})
    return c


def test_requires_login(app):
    assert app.test_client().get("/").status_code == 302


def test_sales_flow_moves_stock_and_invoices(client):
    r = client.post("/sales/new", data={"partner_id": 1, "product_id": ["1"], "quantity": ["3"], "price": [""]})
    assert r.status_code == 302
    so = db.session.get(SalesOrder, 1)
    assert so.total == 30
    for action in ("confirm", "ship", "invoice"):
        client.post(f"/sales/1/{action}")
    db.session.refresh(so)
    assert so.status == "invoiced"
    assert db.session.get(Product, 1).qty_on_hand == 2
    inv = db.session.get(Invoice, 1)
    assert inv.amount == 30 and inv.status == "unpaid"
    client.post("/invoices/1/pay")
    db.session.refresh(inv)
    assert inv.status == "paid"
    assert StockMovement.query.filter_by(reason="sale").count() == 1


def test_confirm_blocks_insufficient_stock(client):
    client.post("/sales/new", data={"partner_id": 1, "product_id": ["1"], "quantity": ["9"], "price": ["10"]})
    r = client.post("/sales/1/confirm", follow_redirects=True)
    assert b"Insufficient stock" in r.data
    assert db.session.get(SalesOrder, 1).status == "draft"


def test_purchase_receive_adds_stock_and_updates_cost(client):
    client.post("/purchases/new", data={"partner_id": 1, "product_id": ["1"], "quantity": ["10"], "price": ["5.50"]})
    client.post("/purchases/1/receive")  # not yet placed: rejected
    assert db.session.get(Product, 1).qty_on_hand == 5
    client.post("/purchases/1/place")
    client.post("/purchases/1/receive")
    p = db.session.get(Product, 1)
    assert p.qty_on_hand == 15 and float(p.cost) == 5.5


def test_stock_adjustment_cannot_go_negative(client):
    client.post("/products/1/adjust", data={"change": "-10"})
    assert db.session.get(Product, 1).qty_on_hand == 5
    client.post("/products/1/adjust", data={"change": "-2", "note": "damaged"})
    assert db.session.get(Product, 1).qty_on_hand == 3


def test_csrf_enforced():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        assert app.test_client().post("/login", data={"username": "x"}).status_code == 400


def test_all_pages_render_with_demo_data():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://", "WTF_CSRF_ENABLED": False})
    with app.app_context():
        ensure_admin()
        seed_demo()
        c = app.test_client()
        c.post("/login", data={"username": "admin", "password": "admin"})
        for url in ["/", "/products", "/products/new", "/products/1/edit", "/inventory/movements",
                    "/customers", "/customers/new", "/customers/1/edit", "/suppliers",
                    "/sales", "/sales?status=draft", "/sales/new", "/sales/1", "/purchases", "/purchases/new",
                    "/purchases/1", "/invoices", "/invoices?status=overdue", "/invoices/1", "/users"]:
            assert c.get(url).status_code == 200, url
