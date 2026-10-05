from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, session, url_for,
)
from . import services
from .models import (
    Customer, Invoice, PosSale, Product, PurchaseOrder, PurchaseOrderLine, SalesOrder,
    SalesOrderLine, StockMovement, Supplier, User, adjust_stock, db,
)
from .services import ERPError

bp = Blueprint("erp", __name__)


@bp.before_app_request
def load_user():
    uid = session.get("user_id")
    g.user = db.session.get(User, uid) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("erp.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


# ---------- auth ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "")).first()
        if user and user.check_password(request.form.get("password", "")):
            session.clear()
            session["user_id"] = user.id
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("erp.dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("erp.login"))


# ---------- dashboard ----------

@bp.route("/")
@login_required
def dashboard():
    invoices = Invoice.query.all()
    invoiced = sum((i.amount for i in invoices), Decimal(0))
    paid = sum((i.amount for i in invoices if i.status == "paid"), Decimal(0))
    receivable = invoiced - paid
    pos_sales = PosSale.query.filter_by(status="completed").all()
    pos_revenue = sum((s.total for s in pos_sales), Decimal(0))
    revenue = invoiced + pos_revenue
    overdue = [i for i in invoices if i.overdue]
    products = Product.query.all()
    low_stock = [p for p in products if p.low_stock]
    stock_value = sum((p.stock_value for p in products), Decimal(0))

    months = OrderedDict()
    first = date.today().replace(day=1)
    for k in range(5, -1, -1):
        y, m = first.year, first.month - k
        while m <= 0:
            m += 12
            y -= 1
        months[(y, m)] = [Decimal(0), Decimal(0)]
    for inv in invoices:
        key = (inv.issue_date.year, inv.issue_date.month)
        if key in months:
            months[key][0] += inv.amount
    for sale in pos_sales:
        key = (sale.created_at.year, sale.created_at.month)
        if key in months:
            months[key][1] += sale.total

    return render_template(
        "dashboard.html",
        revenue=revenue, pos_revenue=pos_revenue, receivable=receivable, overdue=overdue,
        stock_value=stock_value, low_stock=low_stock,
        open_sales=SalesOrder.query.filter(SalesOrder.status.in_(["draft", "confirmed", "shipped"])).count(),
        open_purchases=PurchaseOrder.query.filter(PurchaseOrder.status.in_(["draft", "ordered"])).count(),
        recent_sales=SalesOrder.query.order_by(SalesOrder.id.desc()).limit(6).all(),
        chart_labels=[date(y, m, 1).strftime("%b %Y") for y, m in months],
        chart_invoiced=[float(v[0]) for v in months.values()],
        chart_pos=[float(v[1]) for v in months.values()],
    )


# ---------- products / inventory ----------

def _dec(value, field):
    try:
        d = Decimal(value or "0")
    except InvalidOperation:
        raise ERPError(f"{field} must be a number.")
    if d < 0:
        raise ERPError(f"{field} cannot be negative.")
    return d


def _int(value, field, allow_negative=False):
    try:
        n = int(value or 0)
    except ValueError:
        raise ERPError(f"{field} must be a whole number.")
    if n < 0 and not allow_negative:
        raise ERPError(f"{field} cannot be negative.")
    return n


@bp.route("/products")
@login_required
def products():
    q = request.args.get("q", "").strip()
    query = Product.query
    if q:
        like = f"%{q}%"
        query = query.filter(Product.name.ilike(like) | Product.sku.ilike(like) | Product.category.ilike(like))
    return render_template("products.html", products=query.order_by(Product.sku).all(), q=q)


@bp.route("/products/new", methods=["GET", "POST"])
@bp.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def product_form(pid=None):
    product = get_or_404(Product, pid) if pid else Product(qty_on_hand=0)
    if request.method == "POST":
        f = request.form
        try:
            sku = f.get("sku", "").strip()
            name = f.get("name", "").strip()
            if not sku or not name:
                raise ERPError("SKU and name are required.")
            dup = Product.query.filter(Product.sku == sku, Product.id != (product.id or 0)).first()
            if dup:
                raise ERPError(f"SKU {sku} already exists.")
            product.sku, product.name = sku, name
            product.category = f.get("category", "").strip()
            product.unit_price = _dec(f.get("unit_price"), "Price")
            product.cost = _dec(f.get("cost"), "Cost")
            product.reorder_level = _int(f.get("reorder_level"), "Reorder level")
            if pid is None:
                db.session.add(product)
                opening = _int(f.get("qty_on_hand"), "Opening stock")
                if opening:
                    db.session.flush()
                    adjust_stock(product, opening, "opening", "")
            db.session.commit()
            flash(f"Saved {product.sku}.", "success")
            return redirect(url_for("erp.products"))
        except ERPError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("product_form.html", product=product)


@bp.route("/products/<int:pid>/adjust", methods=["POST"])
@login_required
def product_adjust(pid):
    product = get_or_404(Product, pid)
    try:
        change = _int(request.form.get("change"), "Adjustment", allow_negative=True)
        if change == 0:
            raise ERPError("Adjustment cannot be zero.")
        if product.qty_on_hand + change < 0:
            raise ERPError("Stock cannot go below zero.")
        adjust_stock(product, change, "adjustment", request.form.get("note", "")[:40])
        db.session.commit()
        flash(f"Adjusted {product.sku} by {change:+d}.", "success")
    except ERPError as e:
        flash(str(e), "danger")
    return redirect(url_for("erp.products"))


@bp.route("/inventory/movements")
@login_required
def movements():
    rows = StockMovement.query.order_by(StockMovement.id.desc()).limit(200).all()
    return render_template("movements.html", movements=rows)


# ---------- customers / suppliers ----------

PARTNERS = {"customers": (Customer, "Customer"), "suppliers": (Supplier, "Supplier")}


@bp.route("/<any(customers, suppliers):kind>")
@login_required
def partners(kind):
    model, label = PARTNERS[kind]
    return render_template("partners.html", kind=kind, label=label,
                           partners=model.query.order_by(model.name).all())


@bp.route("/<any(customers, suppliers):kind>/new", methods=["GET", "POST"])
@bp.route("/<any(customers, suppliers):kind>/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def partner_form(kind, pid=None):
    model, label = PARTNERS[kind]
    partner = get_or_404(model, pid) if pid else model()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name is required.", "danger")
        else:
            partner.name = name
            for field in ("email", "phone", "address"):
                setattr(partner, field, request.form.get(field, "").strip())
            if pid is None:
                db.session.add(partner)
            db.session.commit()
            flash(f"Saved {label.lower()} {partner.name}.", "success")
            return redirect(url_for("erp.partners", kind=kind))
    return render_template("partner_form.html", kind=kind, label=label, partner=partner)


# ---------- sales & purchase orders ----------

ORDER_KINDS = {
    "sales": dict(model=SalesOrder, line=SalesOrderLine, partner=Customer, partner_field="customer",
                  price_field="unit_price", line_price="unit_price", actions=services.SALES_ACTIONS,
                  title="Sales Orders", partner_label="Customer"),
    "purchases": dict(model=PurchaseOrder, line=PurchaseOrderLine, partner=Supplier, partner_field="supplier",
                      price_field="cost", line_price="unit_cost", actions=services.PURCHASE_ACTIONS,
                      title="Purchase Orders", partner_label="Supplier"),
}


@bp.route("/<any(sales, purchases):kind>")
@login_required
def orders(kind):
    cfg = ORDER_KINDS[kind]
    status = request.args.get("status", "")
    query = cfg["model"].query
    if status:
        query = query.filter_by(status=status)
    return render_template("orders.html", kind=kind, cfg=cfg, status=status,
                           statuses=cfg["model"].STATUSES,
                           orders=query.order_by(cfg["model"].id.desc()).all())


@bp.route("/<any(sales, purchases):kind>/new", methods=["GET", "POST"])
@login_required
def order_new(kind):
    cfg = ORDER_KINDS[kind]
    all_products = Product.query.order_by(Product.sku).all()
    partners = cfg["partner"].query.order_by(cfg["partner"].name).all()
    if request.method == "POST":
        try:
            partner = db.session.get(cfg["partner"], int(request.form.get("partner_id") or 0))
            if partner is None:
                raise ERPError(f"Select a {cfg['partner_label'].lower()}.")
            lines = services.parse_lines(request.form, {p.id: p for p in all_products}, cfg["price_field"])
            order = cfg["model"](**{cfg["partner_field"]: partner})
            for product, qty, price in lines:
                order.lines.append(cfg["line"](product=product, quantity=qty, **{cfg["line_price"]: price}))
            db.session.add(order)
            db.session.commit()
            flash(f"Created {order.number}.", "success")
            return redirect(url_for("erp.order_detail", kind=kind, oid=order.id))
        except ERPError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("order_form.html", kind=kind, cfg=cfg, products=all_products, partners=partners)


@bp.route("/<any(sales, purchases):kind>/<int:oid>")
@login_required
def order_detail(kind, oid):
    cfg = ORDER_KINDS[kind]
    return render_template("order_detail.html", kind=kind, cfg=cfg, order=get_or_404(cfg["model"], oid))


@bp.route("/<any(sales, purchases):kind>/<int:oid>/<action>", methods=["POST"])
@login_required
def order_action(kind, oid, action):
    cfg = ORDER_KINDS[kind]
    order = get_or_404(cfg["model"], oid)
    fn = cfg["actions"].get(action)
    if fn is None:
        abort(404)
    try:
        fn(order)
        db.session.commit()
        flash(f"{order.number}: {action} done.", "success")
    except ERPError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("erp.order_detail", kind=kind, oid=oid))


# ---------- point of sale ----------

@bp.route("/pos", methods=["GET", "POST"])
@login_required
def pos():
    all_products = Product.query.order_by(Product.name).all()
    customers = Customer.query.order_by(Customer.name).all()
    if request.method == "POST":
        f = request.form
        try:
            parsed = services.parse_lines(f, {p.id: p for p in all_products}, "unit_price")
            lines = [(p, qty, Decimal(p.unit_price)) for p, qty, _ in parsed]
            customer = None
            if f.get("customer_id"):
                customer = db.session.get(Customer, _int(f.get("customer_id"), "Customer"))
                if customer is None:
                    raise ERPError("Unknown customer.")
            raw = f.get("tendered", "").strip()
            tendered = _dec(raw, "Cash tendered") if raw else None
            sale = PosSale(customer=customer, cashier=g.user, payment_method=f.get("payment_method", ""))
            services.pos_checkout(sale, lines, tendered)
            db.session.commit()
            flash(f"{sale.number} completed.", "success")
            return redirect(url_for("erp.pos_receipt", sid=sale.id))
        except ERPError as e:
            db.session.rollback()
            flash(str(e), "danger")
    catalog = [dict(id=p.id, sku=p.sku, name=p.name, category=p.category,
                    price=float(p.unit_price), stock=p.qty_on_hand) for p in all_products]
    return render_template("pos.html", products=all_products, catalog=catalog, customers=customers,
                           methods=PosSale.PAYMENT_METHODS)


@bp.route("/pos/sales")
@login_required
def pos_sales():
    try:
        day = date.fromisoformat(request.args.get("day", ""))
    except ValueError:
        day = date.today()
    start = datetime.combine(day, time.min)
    sales = (PosSale.query.filter(PosSale.created_at >= start, PosSale.created_at < start + timedelta(days=1))
             .order_by(PosSale.id.desc()).all())
    completed = [s for s in sales if s.status == "completed"]
    by_method = {label: sum((s.total for s in completed if s.payment_method == key), Decimal(0))
                 for key, label in PosSale.PAYMENT_METHODS.items()}
    return render_template("pos_sales.html", sales=sales, day=day, count=len(completed),
                           total=sum((s.total for s in completed), Decimal(0)), by_method=by_method,
                           prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1))


@bp.route("/pos/sales/<int:sid>")
@login_required
def pos_receipt(sid):
    return render_template("pos_receipt.html", sale=get_or_404(PosSale, sid))


@bp.route("/pos/sales/<int:sid>/void", methods=["POST"])
@admin_required
def pos_void(sid):
    sale = get_or_404(PosSale, sid)
    try:
        services.void_pos_sale(sale)
        db.session.commit()
        flash(f"{sale.number} voided; stock returned.", "success")
    except ERPError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("erp.pos_receipt", sid=sid))


# ---------- invoices ----------

@bp.route("/invoices")
@login_required
def invoices():
    status = request.args.get("status", "")
    query = Invoice.query
    if status in ("paid", "unpaid"):
        query = query.filter_by(status=status)
    rows = query.order_by(Invoice.id.desc()).all()
    if status == "overdue":
        rows = [i for i in rows if i.overdue]
    return render_template("invoices.html", invoices=rows, status=status)


@bp.route("/invoices/<int:iid>")
@login_required
def invoice_detail(iid):
    return render_template("invoice_detail.html", invoice=get_or_404(Invoice, iid))


@bp.route("/invoices/<int:iid>/pay", methods=["POST"])
@login_required
def invoice_pay(iid):
    invoice = get_or_404(Invoice, iid)
    try:
        services.mark_invoice_paid(invoice)
        db.session.commit()
        flash(f"{invoice.number} marked paid.", "success")
    except ERPError as e:
        flash(str(e), "danger")
    return redirect(url_for("erp.invoice_detail", iid=iid))


# ---------- users ----------

@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "staff")
        if not username or len(password) < 6:
            flash("Username required and password must be at least 6 characters.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
        else:
            user = User(username=username, role="admin" if role == "admin" else "staff")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f"Created user {username}.", "success")
        return redirect(url_for("erp.users"))
    return render_template("users.html", users=User.query.order_by(User.username).all())
