from collections import defaultdict
from itertools import zip_longest
from datetime import date
from decimal import Decimal

from .models import Invoice, PosSale, PosSaleLine, adjust_stock, db


class ERPError(Exception):
    pass


def _require_status(order, allowed):
    if order.status not in allowed:
        raise ERPError(f"{order.number} is {order.status}; expected {' or '.join(allowed)}.")


def confirm_sales_order(order):
    _require_status(order, ["draft"])
    if not order.lines:
        raise ERPError("Cannot confirm an order with no lines.")
    needed = defaultdict(int)
    for line in order.lines:
        needed[line.product] += line.quantity
    short = [f"{p.sku} (need {q}, have {p.qty_on_hand})" for p, q in needed.items() if p.qty_on_hand < q]
    if short:
        raise ERPError("Insufficient stock: " + ", ".join(short))
    order.status = "confirmed"


def ship_sales_order(order):
    _require_status(order, ["confirmed"])
    for line in order.lines:
        if line.product.qty_on_hand < line.quantity:
            raise ERPError(f"Insufficient stock for {line.product.sku}.")
    for line in order.lines:
        adjust_stock(line.product, -line.quantity, "sale", order.number)
    order.status = "shipped"


def invoice_sales_order(order):
    _require_status(order, ["shipped"])
    invoice = Invoice(sales_order=order, amount=order.total)
    db.session.add(invoice)
    order.status = "invoiced"
    return invoice


def cancel_sales_order(order):
    _require_status(order, ["draft", "confirmed"])
    order.status = "cancelled"


def mark_invoice_paid(invoice):
    if invoice.status == "paid":
        raise ERPError(f"{invoice.number} is already paid.")
    invoice.status = "paid"
    invoice.paid_date = date.today()


def place_purchase_order(order):
    _require_status(order, ["draft"])
    if not order.lines:
        raise ERPError("Cannot place an order with no lines.")
    order.status = "ordered"


def receive_purchase_order(order):
    _require_status(order, ["ordered"])
    for line in order.lines:
        adjust_stock(line.product, line.quantity, "purchase", order.number)
        line.product.cost = line.unit_cost
    order.status = "received"


def cancel_purchase_order(order):
    _require_status(order, ["draft", "ordered"])
    order.status = "cancelled"


def _check_stock(lines):
    needed = defaultdict(int)
    for product, qty, _ in lines:
        needed[product] += qty
    short = [f"{p.sku} (need {q}, have {p.qty_on_hand})" for p, q in needed.items() if p.qty_on_hand < q]
    if short:
        raise ERPError("Insufficient stock: " + ", ".join(short))


def pos_checkout(sale, lines, tendered=None):
    """Complete a POS sale: validate payment and stock, record lines, deduct stock."""
    if sale.payment_method not in PosSale.PAYMENT_METHODS:
        raise ERPError("Select a payment method.")
    _check_stock(lines)
    total = sum((Decimal(price) * qty for _, qty, price in lines), Decimal("0"))
    if sale.payment_method == "cash":
        if tendered is None or tendered < total:
            raise ERPError("Cash tendered is less than the total.")
        sale.tendered = tendered
    else:
        sale.tendered = total
    for product, qty, price in lines:
        sale.lines.append(PosSaleLine(product=product, quantity=qty, unit_price=price))
    db.session.add(sale)
    db.session.flush()
    for line in sale.lines:
        adjust_stock(line.product, -line.quantity, "pos", sale.number)
    return sale


def void_pos_sale(sale):
    if sale.status != "completed":
        raise ERPError(f"{sale.number} is already {sale.status}.")
    for line in sale.lines:
        adjust_stock(line.product, line.quantity, "pos-void", sale.number)
    sale.status = "voided"


SALES_ACTIONS = {
    "confirm": confirm_sales_order,
    "ship": ship_sales_order,
    "invoice": invoice_sales_order,
    "cancel": cancel_sales_order,
}

PURCHASE_ACTIONS = {
    "place": place_purchase_order,
    "receive": receive_purchase_order,
    "cancel": cancel_purchase_order,
}


def parse_lines(form, products, price_field):
    """Build (product, quantity, price) tuples from repeated form fields."""
    lines = []
    for pid, qty, price in zip_longest(
        form.getlist("product_id"), form.getlist("quantity"), form.getlist("price"), fillvalue=""
    ):
        if not pid:
            continue
        product = products.get(int(pid))
        if product is None:
            raise ERPError("Unknown product.")
        try:
            quantity = int(qty)
        except ValueError:
            raise ERPError(f"Invalid quantity for {product.sku}.")
        if quantity <= 0:
            raise ERPError(f"Quantity for {product.sku} must be positive.")
        try:
            unit = Decimal(price) if price.strip() else Decimal(getattr(product, price_field))
        except Exception:
            raise ERPError(f"Invalid price for {product.sku}.")
        if unit < 0:
            raise ERPError(f"Price for {product.sku} cannot be negative.")
        lines.append((product, quantity, unit))
    if not lines:
        raise ERPError("Add at least one line item.")
    return lines
