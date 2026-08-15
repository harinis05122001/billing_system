"""Business logic for turning a set of (product, quantity) selections plus
the cash the customer physically handed over into a persisted, correctly
priced Bill.

Keeps the request/response cycle thin: Views only parse input into
``LineItemInput`` objects and a ``tendered`` denomination breakdown, then
call :func:`create_bill`; all validation, pricing, stock handling, till
reconciliation, and change calculation live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db import transaction

from billing.models import Bill, BillDenomination, BillItem, BillTender, Customer, Denomination, Product
from billing.services.denomination_service import ChangeResult, compute_change

CENT = Decimal("0.01")
RUPEE = Decimal("1")


class BillingError(Exception):
    """Raised for any validation failure while creating a bill.

    The message is written to be safe to display directly to the user.
    """


@dataclass(frozen=True)
class LineItemInput:
    product_id: str
    quantity: int


def _round_currency(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


@transaction.atomic
def create_bill(*, customer_email: str, line_items: list[LineItemInput], tendered: dict[int, int]) -> Bill:
    """``tendered`` maps a denomination value (e.g. 500) to how many of that
    note/coin the customer handed over -- not a single typed total. This is
    what lets the shop's till stay accurate: the notes received here are
    added to Denomination inventory in the same transaction that change is
    drawn from it, rather than only ever being drawn down.
    """
    if not line_items:
        raise BillingError("Add at least one product to the bill.")

    _reject_duplicate_products(line_items)
    _reject_invalid_quantities(line_items)
    _reject_invalid_tender(tendered)

    products = _lock_products(line_items)
    items_data = _price_line_items(line_items, products)

    subtotal = sum((d["line_subtotal"] for d in items_data), Decimal("0"))
    tax_total = sum((d["tax_amount"] for d in items_data), Decimal("0"))
    net_total = subtotal + tax_total
    rounded_total = net_total.quantize(RUPEE, rounding=ROUND_DOWN)

    locked_denominations = {d.value: d for d in Denomination.objects.select_for_update().order_by("-value")}
    _reject_unknown_denominations(tendered, locked_denominations)

    # Built entirely from whole-rupee denomination values and integer
    # counts, so this is always a whole rupee amount -- no separate
    # "must be a whole number" validation is needed, unlike a free-typed field.
    paid_amount = sum((Decimal(value) * count for value, count in tendered.items()), Decimal("0"))

    if paid_amount < rounded_total:
        raise BillingError(
            f"Insufficient payment: amount payable is Rs. {rounded_total}, but only Rs. {paid_amount} was tendered."
        )

    balance_amount = paid_amount - rounded_total
    # Change is drawn from the till *after* the customer's cash has gone
    # in -- exactly like a real cash drawer -- so the just-tendered notes
    # are included in what's available to make change from.
    available_after_tender = {
        value: denomination.available_count + tendered.get(value, 0)
        for value, denomination in locked_denominations.items()
    }
    change_result = _resolve_change(balance_amount, available_after_tender)

    customer, _ = Customer.objects.get_or_create(email=customer_email)

    bill = Bill.objects.create(
        customer=customer,
        subtotal=subtotal,
        tax_total=tax_total,
        net_total=net_total,
        rounded_total=rounded_total.quantize(CENT),
        paid_amount=paid_amount.quantize(CENT),
        balance_amount=balance_amount.quantize(CENT),
    )

    _create_bill_items_and_reduce_stock(bill, items_data)
    _create_tender_records_and_increase_inventory(bill, tendered, locked_denominations)
    _create_change_denominations_and_reduce_inventory(bill, change_result, locked_denominations)

    transaction.on_commit(lambda: _enqueue_invoice_email(bill.id))
    return bill


def _reject_duplicate_products(line_items: list[LineItemInput]) -> None:
    seen: set[str] = set()
    for item in line_items:
        if item.product_id in seen:
            raise BillingError(
                f"Product '{item.product_id}' appears more than once. Combine its quantity into a single row."
            )
        seen.add(item.product_id)


def _reject_invalid_quantities(line_items: list[LineItemInput]) -> None:
    for item in line_items:
        if item.quantity <= 0:
            raise BillingError(f"Quantity for product '{item.product_id}' must be a positive whole number.")


def _reject_invalid_tender(tendered: dict[int, int]) -> None:
    if not tendered:
        raise BillingError("Enter how many of each denomination the customer paid with.")
    for value, count in tendered.items():
        if count <= 0:
            raise BillingError(f"Count received for Rs. {value} must be a positive whole number.")


def _reject_unknown_denominations(tendered: dict[int, int], known: dict[int, Denomination]) -> None:
    unknown = sorted((value for value in tendered if value not in known), reverse=True)
    if unknown:
        values = ", ".join(f"Rs. {value}" for value in unknown)
        raise BillingError(f"Unknown denomination value(s): {values}.")


def _lock_products(line_items: list[LineItemInput]) -> dict[str, Product]:
    """Row-locks every referenced product, in a deterministic (sorted)
    order, so concurrent billing requests serialize instead of deadlocking
    or racing each other's stock checks.
    """
    product_ids = sorted({item.product_id for item in line_items})
    locked = Product.objects.select_for_update().filter(product_id__in=product_ids).order_by("product_id")
    products = {p.product_id: p for p in locked}
    missing = [pid for pid in product_ids if pid not in products]
    if missing:
        raise BillingError(f"Unknown product ID(s): {', '.join(missing)}.")
    return products


def _price_line_items(line_items: list[LineItemInput], products: dict[str, Product]) -> list[dict]:
    """Computes per-line pricing. Tax is rounded to the nearest paisa on
    each line *before* being summed into the bill total, so the "Tax
    payable for item" figures shown on the invoice always add up exactly to
    the bill's tax total.
    """
    results = []
    for item in line_items:
        product = products[item.product_id]
        if item.quantity > product.stock:
            raise BillingError(
                f"Insufficient stock for '{product.product_id}': requested {item.quantity}, "
                f"only {product.stock} available."
            )
        line_subtotal = _round_currency(product.price * item.quantity)
        tax_amount = _round_currency(line_subtotal * product.tax_percent / Decimal("100"))
        line_total = line_subtotal + tax_amount
        results.append(
            {
                "product": product,
                "quantity": item.quantity,
                "line_subtotal": line_subtotal,
                "tax_amount": tax_amount,
                "line_total": line_total,
            }
        )
    return results


def _resolve_change(balance_amount: Decimal, available: dict[int, int]) -> ChangeResult | None:
    if balance_amount <= 0:
        return None
    change_result = compute_change(int(balance_amount), available)
    if change_result is None:
        raise BillingError(
            f"Cannot make exact change of Rs. {balance_amount} with the denominations currently "
            "available in the shop (including what the customer just handed over)."
        )
    return change_result


def _create_bill_items_and_reduce_stock(bill: Bill, items_data: list[dict]) -> None:
    for data in items_data:
        product = data["product"]
        BillItem.objects.create(
            bill=bill,
            product=product,
            unit_price=product.price,
            tax_percent=product.tax_percent,
            quantity=data["quantity"],
            line_subtotal=data["line_subtotal"],
            tax_amount=data["tax_amount"],
            line_total=data["line_total"],
        )
        product.stock -= data["quantity"]
        product.save(update_fields=["stock", "updated_at"])


def _create_tender_records_and_increase_inventory(
    bill: Bill, tendered: dict[int, int], locked_denominations: dict[int, Denomination]
) -> None:
    for value, count in tendered.items():
        denomination = locked_denominations[value]
        BillTender.objects.create(bill=bill, denomination=denomination, count=count)
        denomination.available_count += count
        denomination.save(update_fields=["available_count"])


def _create_change_denominations_and_reduce_inventory(
    bill: Bill, change_result: ChangeResult | None, locked_denominations: dict[int, Denomination]
) -> None:
    if change_result is None:
        return
    for value, count in change_result.breakdown.items():
        denomination = locked_denominations[value]
        BillDenomination.objects.create(bill=bill, denomination=denomination, count=count)
        denomination.available_count -= count
        denomination.save(update_fields=["available_count"])


def _enqueue_invoice_email(bill_id: int) -> None:
    # The bill itself is already committed -- a broker outage should never
    # undo a completed sale. Email is a best-effort side effect, so any
    # failure here is only logged (by enqueue_invoice_email itself).
    from billing.tasks import enqueue_invoice_email

    enqueue_invoice_email(bill_id)
