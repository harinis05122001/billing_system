from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import BillingForm, ProductForm, parse_line_items, parse_tendered
from .models import Bill, Customer, Denomination, Product
from .services.billing_service import BillingError, LineItemInput, create_bill
from .tasks import enqueue_invoice_email

PURCHASES_PER_PAGE = 20


def index(request):
    return render(request, "billing/index.html")


@require_http_methods(["GET", "POST"])
def billing_form_view(request):
    """Page 1: collects the customer email, dynamically-added product
    rows, and the cash paid, then hands everything to the billing service.
    All validation here is a thin pass-through to the service layer -- the
    service is the single source of truth for business rules.
    """
    form = BillingForm(request.POST or None)
    rows: list[tuple[str, str]] = []
    denominations = Denomination.objects.all()

    if request.method == "POST":
        rows, row_errors = parse_line_items(request.POST)
        line_items, quantity_errors = _build_line_items(rows)
        tendered, tender_errors = parse_tendered(request.POST, [d.value for d in denominations])
        errors = row_errors + quantity_errors + tender_errors

        if form.is_valid() and not errors:
            try:
                bill = create_bill(
                    customer_email=form.cleaned_data["customer_email"],
                    line_items=line_items,
                    tendered=tendered,
                )
            except BillingError as exc:
                errors.append(str(exc))
            else:
                return redirect("billing:bill_detail", bill_id=bill.id)

        for error in errors:
            messages.error(request, error)

    products = Product.objects.all()
    context = {
        "form": form,
        "rows": rows or [("", ""), ("", ""), ("", "")],
        "products": products,
        "products_json": _products_for_js(products),
        "denominations": denominations,
    }
    return render(request, "billing/billing_form.html", context)


def _products_for_js(products) -> list[dict]:
    """Serializes products for the client-side product picker and live
    order-total preview (billing.js) -- Decimal fields are converted to
    strings since Decimal isn't JSON-serializable directly. This is only
    ever used for an instant, informational preview in the browser; the
    server-side billing_service recalculates everything authoritatively
    when the form is submitted.
    """
    return [
        {
            "id": p.product_id,
            "name": p.name,
            "price": str(p.price),
            "tax_percent": str(p.tax_percent),
            "stock": p.stock,
        }
        for p in products
    ]


def _build_line_items(rows: list[tuple[str, str]]) -> tuple[list[LineItemInput], list[str]]:
    line_items = []
    errors = []
    for product_id, quantity_raw in rows:
        try:
            quantity = int(quantity_raw)
        except ValueError:
            errors.append(f"Quantity for product '{product_id}' must be a whole number.")
            continue
        line_items.append(LineItemInput(product_id=product_id, quantity=quantity))
    return line_items, errors


def bill_detail_view(request, bill_id):
    """Page 2: the generated bill. Also reused as the "selected purchase"
    detail view from customer purchase history, since the information shown
    is identical either way.
    """
    bill = get_object_or_404(
        Bill.objects.select_related("customer").prefetch_related(
            "items__product", "tendered_denominations__denomination", "change_denominations__denomination"
        ),
        pk=bill_id,
    )
    return render(request, "billing/bill_detail.html", {"bill": bill})


@require_http_methods(["POST"])
def resend_invoice_email_view(request, bill_id):
    """Manually re-queues the invoice email -- for a customer asking for
    another copy, or retrying after email_status == "failed". Resets the
    status to pending immediately so the page reflects "in progress" right
    away rather than showing a stale sent/failed state until the worker
    picks it up.
    """
    bill = get_object_or_404(Bill, pk=bill_id)
    bill.email_status = Bill.EmailStatus.PENDING
    bill.email_error = ""
    bill.save(update_fields=["email_status", "email_error"])

    if enqueue_invoice_email(bill.id):
        messages.success(request, f"Invoice email queued for resend to {bill.customer.email}.")
    else:
        messages.error(
            request,
            "Could not queue the invoice email for resending -- the background worker/broker may be unavailable.",
        )
    return redirect("billing:bill_detail", bill_id=bill.id)


def customer_history_view(request):
    """Lists every bill, most recent first, optionally narrowed by the
    filters below; each row links to bill_detail_view to show what was
    purchased. Filters are all optional and combine (AND) -- with none set,
    this simply lists everything.
    """
    email = request.GET.get("email", "").strip()
    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()
    email_status = request.GET.get("email_status", "").strip()

    date_from = _parse_date(date_from_raw)
    date_to = _parse_date(date_to_raw)
    valid_email_statuses = {choice for choice, _ in Bill.EmailStatus.choices}

    bills = Bill.objects.select_related("customer").order_by("-created_at")
    if email:
        bills = bills.filter(customer__email__icontains=email)
    if date_from:
        bills = bills.filter(created_at__date__gte=date_from)
    if date_to:
        bills = bills.filter(created_at__date__lte=date_to)
    if email_status in valid_email_statuses:
        bills = bills.filter(email_status=email_status)

    paginator = Paginator(bills, PURCHASES_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        "page_obj": page_obj,
        "querystring": querystring.urlencode(),
        "email": email,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
        "email_status": email_status,
        "email_status_choices": Bill.EmailStatus.choices,
        "has_any_bills": Bill.objects.exists(),
        "known_emails": Customer.objects.order_by("email").values_list("email", flat=True),
    }
    return render(request, "billing/customer_history.html", context)


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def product_list_view(request):
    """The in-app product catalog: everything Admin's Product page offers,
    but reachable without a staff login and styled like the rest of the app.
    """
    products = Product.objects.all()
    return render(request, "billing/product_list.html", {"products": products})


@require_http_methods(["GET", "POST"])
def product_create_view(request):
    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            messages.success(request, f"Product '{product.product_id}' created.")
            return redirect("billing:product_list")
    else:
        form = ProductForm()
    return render(request, "billing/product_form.html", {"form": form, "is_edit": False})


@require_http_methods(["GET", "POST"])
def product_edit_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f"Product '{product.product_id}' updated.")
            return redirect("billing:product_list")
    else:
        form = ProductForm(instance=product)
    return render(request, "billing/product_form.html", {"form": form, "is_edit": True, "product": product})


@require_http_methods(["POST"])
def product_delete_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    try:
        product.delete()
    except ProtectedError:
        # BillItem.product is on_delete=PROTECT -- a product that has ever
        # been sold must stay around so past invoices keep making sense.
        messages.error(
            request,
            f"Cannot delete '{product.product_id}': it has already been sold in one or more bills. "
            "Set its stock to 0 instead if it shouldn't be sold anymore.",
        )
    else:
        messages.success(request, f"Product '{product.product_id}' deleted.")
    return redirect("billing:product_list")
