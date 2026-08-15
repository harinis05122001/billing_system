# Billing System

A Django billing application: generate a customer bill from a dynamic set of
products, calculate tax/totals, work out the exact change to return from the
shop's limited note/coin inventory, email the invoice to the customer in the
background, and look up a customer's previous purchases.

## 1. Overview

The app implements the assignment's three pages:

- **Page 1** (`/billing/`) — pick products from a searchable, click-to-add
  catalog (or type a product ID with autocomplete), see a live running total
  as you go, enter the cash the customer physically handed over broken down
  by denomination, and submit to generate the bill. A fixed bar at the bottom
  of the screen always shows the subtotal/tax/amount due/change, so the
  cashier never has to scroll to see what to collect.
- **Page 2** (`/billing/<id>/`) — the generated bill: per-item price/tax/total,
  bill-level subtotal/tax/net/rounded/balance, and the denomination breakdown
  of change to hand back to the customer. The invoice is emailed to the
  customer asynchronously in the background at the same time.
- **Purchase history** (`/customers/history/`) — search a customer by email,
  list their past bills, and open one to see exactly what was purchased (reuses
  Page 2's template).

Product, Denomination, and Customer master data are managed through the
Django Admin (`/admin/`).

## 2. Features

- Searchable product catalog with click-to-add, plus a text/autocomplete
  fallback, and a live, client-side order total (subtotal/tax/amount to
  collect/change) that updates on every keystroke, pinned to the bottom of
  the screen so it's always visible.
- Cash is recorded **by denomination** (how many ₹500s, ₹50s, etc. the
  customer handed over), not as a single typed total — see "Till
  reconciliation" below.
- Per-item tax calculation, correctly rounded and summed into bill totals.
- Stock validation and atomic, race-safe stock deduction.
- **Till reconciliation**: cash tendered by the customer is added to the
  shop's `Denomination` inventory, and change given is subtracted from it, in
  the same atomic transaction — so the till stays accurate over time instead
  of only ever being drawn down.
- Change calculation via a bounded dynamic-programming algorithm that respects
  the shop's actual (limited) denomination inventory — not a naive greedy pick.
- Fully atomic billing transactions: a bill, its items, its tendered cash, and
  its change denominations are created together or not at all.
- Asynchronous invoice emailing via Celery + Redis, with delivery status
  (`pending` / `sent` / `failed`) tracked on the bill and visible in the UI/Admin.
- Customer purchase history with drill-down into a past bill's items.
- Django Admin for Product/Denomination/Customer management; Bills are
  admin-viewable but read-only (see Design Decisions).
- 39 automated tests covering models, billing math, stock, till
  reconciliation, the denomination algorithm, views, and the async email task.

## 3. Architecture

Single Django app (`billing`) with a thin view layer and a dedicated service
layer for business logic:

```
Browser
  |
Django View (billing/views.py)         -- parses request, calls the service, renders a template
  |
Form / manual row parsing (billing/forms.py)   -- scalar field validation + dynamic-row parsing
  |
Service layer (billing/services/)
  |-- billing_service.py     -- orchestrates validation, pricing, stock, change, persistence,
  |                              all inside one transaction.atomic() block
  \-- denomination_service.py -- pure function: bounded DP change-making, no DB access
  |
Django ORM / Models (billing/models.py)
  |
Database (SQLite by default)
```

For email:

```
billing_service.create_bill() commits its transaction
  -> transaction.on_commit(...)
  -> Celery task send_invoice_email_task.delay(bill_id)   (broker: Redis)
  -> Celery worker process picks it up, sends the email, updates Bill.email_status
```

Views never touch models directly for anything financial — they only build
`LineItemInput` objects from the request and hand them to
`billing_service.create_bill()`. This keeps `Request -> View -> Validation ->
Service -> Model/DB -> Response` easy to trace end-to-end.

## 4. Technology Stack

- **Django 5.2** (LTS) — chosen over Flask/FastAPI per the assignment's own
  guidance: this task is dominated by models, relationships, admin, ORM, and
  server-rendered forms, which is exactly Django's strength, without needing to
  hand-roll auth, admin, or an ORM on top of a micro-framework.
- **SQLite** by default (zero setup for evaluation); swappable to
  Postgres/MySQL/etc. via `DATABASE_URL` (`dj-database-url`).
- **Celery + Redis** for genuine background/async task processing.
- Plain server-rendered Django templates + vanilla JavaScript (no frontend
  framework, per the assignment's explicit "no need for fancy CSS/Template logic").

## 5. Project Structure

```
config/                      Django project (settings, urls, celery app)
billing/
  models.py                  Product, Denomination, Customer, Bill, BillItem,
                              BillTender (cash received), BillDenomination (change given)
  services/
    billing_service.py       create_bill(): validation, pricing, stock, till reconciliation,
                              change, persistence
    denomination_service.py  compute_change(): bounded DP change-making algorithm
  forms.py                   BillingForm (email) + dynamic row / per-denomination tender parsing
  views.py                   billing_form_view, bill_detail_view, customer_history_view
  tasks.py                   send_invoice_email_task (Celery)
  admin.py                   Django Admin registration
  management/commands/seed_data.py   sample products + denominations
  templates/billing/         billing_form.html, bill_detail.html, customer_history.html, ...
  static/billing/            billing.js (dynamic rows), style.css
  tests/                     test_models, test_billing_service, test_denomination_service,
                              test_views, test_tasks
docker-compose.yml           one-command local Redis
requirements.txt
.env.example
```

## 6. Setup

Requires Python 3.12+ (any recent Python 3.10+ will work).

```bash
# 1. From the project root, create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) configure environment variables
cp .env.example .env      # defaults already work without this step

# 4. Run migrations
python manage.py migrate

# 5. Load sample data (products + denominations)
python manage.py seed_data

# 6. (Optional, for the Admin) create a superuser
python manage.py createsuperuser

# 7. Start the app
python manage.py runserver
```

Open http://127.0.0.1:8000/ — the core billing flow (Page 1 -> Page 2 ->
purchase history) works immediately at this point, using the console email
backend (invoice emails print to the terminal running `runserver`).

### Enabling real background email delivery (Celery + Redis)

The billing flow above works without Redis at all — a bill still commits, and
the email step is simply queued and logged as unavailable (see Design
Decisions: email failures never roll back a bill). To see it actually delivered
asynchronously in the background:

```bash
# In one terminal: start Redis
docker compose up -d

# In another terminal (same venv activated): start the Celery worker
celery -A config worker -l info
```

Now generating a bill enqueues `send_invoice_email_task`, which the worker
picks up and processes in the background; refresh Page 2 (or check the Admin)
to see `email_status` flip from `pending` to `sent`.

## 7. Initial Data

`python manage.py seed_data` creates 8 sample products (`P001`-`P008`, varied
stock/price/tax rates) and the 7 denominations from the assignment's wireframe
(₹500, ₹50, ₹20, ₹10, ₹5, ₹2, ₹1) with starting inventory. It's idempotent
(`get_or_create`) — safe to run more than once. Product IDs and current stock
are also listed directly on the billing page for convenience while testing.
Further products/denominations/customers can be managed via `/admin/`.

## 8. Email Configuration

Defaults to Django's **console backend** — invoice emails print to the
terminal, so there is nothing to configure to see the feature working locally.
To send real email, set in `.env` (see `.env.example` for the full list):

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-address@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=billing@example.com
```

## 9. Testing

```bash
python manage.py test billing
```

39 tests across models, billing calculations (including a golden test that
reproduces the assignment wireframe's floor-rounding behavior), stock
handling/rollback, till reconciliation (tendered cash increments Denomination
inventory, change decrements it, both roll back together on failure), the
denomination change-making algorithm (including a case constructed so a naive
greedy pick would fail but the DP succeeds), views, and the async email task.
Tests run with `CELERY_TASK_ALWAYS_EAGER=True` (enabled
automatically when `manage.py test` runs), so the Celery task executes inline
and email content can be asserted via `django.core.mail.outbox` — no Redis or
worker process needed to run the suite.

## 10. Assumptions

The PDF's Page 2 wireframe gives exact sample figures (subtotal 2102.00, tax
255.60, net 2357.60, rounded 2357.00, balance 643.00), which resolve most of
the arithmetic ambiguity directly — those figures are treated as a
specification, not a suggestion. Remaining ambiguities and the assumption made
for each:

1. **Product ID** is a dedicated business-facing field (`Product.product_id`,
   e.g. `P001`), separate from Django's internal database primary key, since
   the assignment lists "product ID" as a field alongside name/stock/price
   rather than implying it's just the DB row number. Assigned by the shop
   admin (like a SKU) when a product is created via Admin/seed data.
2. **Cash is collected as a denomination breakdown, not a single typed
   total.** The cashier enters how many of each note/coin the customer
   handed over; the paid amount is *derived* from that (`sum(value × count)`),
   never typed directly. This is what makes till reconciliation possible: the
   till's current counts are shown alongside each input for context, but the
   *submitted* counts are what get trusted and written — the database (with
   rows locked inside the transaction) remains the final authority on the
   resulting inventory, so a client can't submit a total that doesn't
   correspond to real notes.
3. **Tax is computed per line item, then summed** into the bill's tax total
   (not computed once on the aggregate subtotal) — this is what the wireframe's
   worked example implies, and it's also the only approach that supports
   per-product tax rates correctly.
4. **Tax is rounded to the nearest paisa per line item** (not just once at the
   end), so the "Tax payable for item" figures shown on the invoice always sum
   exactly to the bill's tax total — a small but real correctness property an
   evaluator can verify by hand-adding the rows.
5. **The final payable amount is floored to the nearest whole rupee** (e.g.
   2357.60 -> 2357.00) since the shop's denominations are all whole-rupee
   notes/coins and change can only be made in whole rupees; the fractional
   remainder is waived in the customer's favor. This exactly reproduces the
   wireframe's own example.
6. **Paid amount is always a whole rupee value** — structurally guaranteed
   rather than separately validated, since it's built entirely from whole-rupee
   denomination values times integer counts. There's no code path that could
   produce a fractional paid amount, unlike a free-typed field.
7. **Duplicate product IDs within one bill submission are rejected**, asking
   the cashier to combine the quantity into a single row, rather than silently
   merging them.
8. **Full payment is required at billing time** — paid amount must cover the
   full rounded total; there's no partial-payment/credit concept, which the
   assignment doesn't mention.
9. **Impossible exact change hard-fails the whole bill** (rolled back, nothing
   persisted) rather than dispensing approximate change or leaving a debt —
   the cashier can then ask for a different payment amount.
10. **No authentication** on the billing/history pages — out of scope per the
    assignment, which only asks for Model/View correctness. Django Admin
    (already auth-protected) handles Product/Denomination management.

## 11. Design Decisions

- **`Decimal` everywhere for money.** Never `float`, despite the assignment
  describing price/tax as "float" conceptually — floats cannot represent
  currency exactly and would eventually produce off-by-a-paisa bugs.
- **Bounded DP for change-making, not greedy.** With a *limited* note supply,
  a one-pass greedy algorithm is not provably correct: it can commit to a
  large denomination that strands the remainder with no valid completion, even
  though skipping it in favor of smaller notes would reach the exact target
  (worked example and test in `denomination_service.py` /
  `test_denomination_service.py`). The DP explores every achievable sum, so
  it's correct by construction, and also minimizes the number of notes
  returned.
- **One `transaction.atomic()` per bill**, with `select_for_update()` on the
  specific `Product` and `Denomination` rows involved (locked in a
  deterministic, sorted order to avoid deadlocking against concurrent billing
  requests). Stock and denomination inventory are only touched after every
  validation has passed, so a failure anywhere leaves the database exactly as
  it was — no partial bill, no partial stock/inventory update.
- **Till reconciliation happens in one transaction, in a specific order.**
  Tendered cash is added to `Denomination.available_count` *before* change is
  computed, so change is correctly drawn from a till that already includes
  what the customer just paid with (exactly like a real cash drawer) — not
  from a stale pre-payment snapshot. Both the increment (`BillTender`) and the
  decrement (`BillDenomination`) live in the same `transaction.atomic()` block
  as the bill itself, so an impossible-change failure rolls back the tender
  too — nothing is ever recorded as received without the corresponding change
  also being resolved.
- **Historical price/tax snapshots on `BillItem`.** `unit_price` and
  `tax_percent` are copied onto the bill item at sale time, so a later change
  to `Product.price`/`tax_percent` never rewrites a past invoice.
- **Bills are read-only in Admin.** A `Bill` is a financial record produced
  only by `billing_service.create_bill()`; hand-editing one in Admin could
  desync it from stock/denomination inventory that has already moved, so
  add/change/delete permissions are disabled for `Bill` — it's viewable and
  searchable only.
- **Celery + Redis over a DB-broker alternative** (e.g. Django-Q2/Huey) for
  the async email — it's the more widely recognized, genuinely production-grade
  choice. The setup-friction tradeoff is mitigated two ways: `docker-compose.yml`
  gets Redis running in one command, and the billing transaction is resilient
  to the broker being unavailable — enqueueing the task is wrapped so a
  connection failure only logs a warning; **the bill itself is never rolled
  back because of an email/infra problem** (see `billing_service._enqueue_invoice_email`).
- **No Django REST Framework / API layer.** The assignment asks for
  server-rendered pages, not an API; adding DRF would be unused surface area.
- **Dynamic rows via `getlist()`, not a Django formset.** The "Add New" button
  clones a plain `<template>` row with no formset management-form bookkeeping
  to keep in sync in JS; the view reads `product_id`/`quantity` as repeated
  fields via `request.POST.getlist()`. Server-side validation (product
  existence, quantity, stock, duplicates) is authoritative regardless of what
  the client sends.

## 12. Known Limitations

- No authentication/authorization on the billing or purchase-history pages
  (see Assumption 10) — anyone who can reach the app can generate bills. Fine
  for this assignment's scope; a real deployment would add `@login_required`
  and probably role-based access for cashiers vs. admins.
- Concurrency correctness (`select_for_update()` row locking) is implemented
  and reasoned about carefully, but SQLite's locking model doesn't exercise
  true multi-connection contention the way Postgres would — the automated
  tests verify the atomicity/rollback behavior directly rather than via actual
  concurrent load.
- The denomination DP is a straightforward bounded-knapsack implementation
  (not the binary-decomposition-optimized version); it's easily fast enough
  for realistic shop-till amounts and note counts, but wouldn't be the right
  choice unmodified for adversarially large inputs.
- No pagination on the purchase-history list — fine for a single customer's
  realistic bill count, but would need it at real scale.
