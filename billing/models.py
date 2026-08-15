from decimal import Decimal

from django.db import models
from django.db.models import Q


class Product(models.Model):
    """A sellable item. ``product_id`` is the business-facing SKU used on the
    billing form; ``id`` (Django's default PK) is purely an internal DB detail.
    """

    product_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    stock = models.PositiveIntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product_id"]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="product_price_positive"),
            models.CheckConstraint(condition=Q(tax_percent__gte=0), name="product_tax_percent_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} - {self.name}"


class Denomination(models.Model):
    """A currency note/coin value available in the shop's till, with the
    current count on hand. This is the authoritative inventory used to
    compute change for bills.
    """

    value = models.PositiveIntegerField(unique=True)
    available_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-value"]
        constraints = [
            models.CheckConstraint(condition=Q(available_count__gte=0), name="denomination_count_non_negative"),
        ]

    def __str__(self) -> str:
        return f"₹{self.value} x {self.available_count}"


class Customer(models.Model):
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email


class Bill(models.Model):
    """A single billing transaction (invoice). Monetary totals are stored
    directly on the bill rather than recomputed from items every time, since
    a bill represents a historical financial record that must not change even
    if product prices/taxes change later.
    """

    class EmailStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bills")
    created_at = models.DateTimeField(auto_now_add=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2)
    net_total = models.DecimalField(max_digits=12, decimal_places=2)
    rounded_total = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_amount = models.DecimalField(max_digits=12, decimal_places=2)

    email_status = models.CharField(max_length=10, choices=EmailStatus.choices, default=EmailStatus.PENDING)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer", "-created_at"], name="bill_customer_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(subtotal__gte=0), name="bill_subtotal_non_negative"),
            models.CheckConstraint(condition=Q(tax_total__gte=0), name="bill_tax_total_non_negative"),
            models.CheckConstraint(condition=Q(paid_amount__gte=0), name="bill_paid_amount_non_negative"),
            models.CheckConstraint(condition=Q(balance_amount__gte=0), name="bill_balance_amount_non_negative"),
        ]

    def __str__(self) -> str:
        return f"Bill #{self.pk} - {self.customer.email}"


class BillItem(models.Model):
    """A single product line on a bill. Price and tax percent are snapshotted
    at sale time so later changes to the Product don't rewrite billing history.
    """

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bill_items")

    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2)
    quantity = models.PositiveIntegerField()

    line_subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["bill", "product"], name="unique_product_per_bill"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="bill_item_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.product.product_id} x {self.quantity} (Bill #{self.bill_id})"


class BillDenomination(models.Model):
    """Records exactly which denominations, and how many of each, were
    dispensed as change for a bill -- a proper relational audit trail rather
    than a computed/derived value.
    """

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="change_denominations")
    denomination = models.ForeignKey(Denomination, on_delete=models.PROTECT, related_name="bill_denominations")
    count = models.PositiveIntegerField()

    class Meta:
        ordering = ["-denomination__value"]
        constraints = [
            models.UniqueConstraint(fields=["bill", "denomination"], name="unique_denomination_per_bill"),
            models.CheckConstraint(condition=Q(count__gt=0), name="bill_denomination_count_positive"),
        ]

    def __str__(self) -> str:
        return f"₹{self.denomination.value} x {self.count} (Bill #{self.bill_id})"


class BillTender(models.Model):
    """Records exactly which denominations, and how many of each, the
    customer physically handed over to pay a bill. This is what lets the
    shop's Denomination inventory stay accurate over time: cash received
    here is added to available_count in the same transaction that change
    (BillDenomination) is subtracted from it.
    """

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="tendered_denominations")
    denomination = models.ForeignKey(Denomination, on_delete=models.PROTECT, related_name="bill_tenders")
    count = models.PositiveIntegerField()

    class Meta:
        ordering = ["-denomination__value"]
        constraints = [
            models.UniqueConstraint(fields=["bill", "denomination"], name="unique_tender_denomination_per_bill"),
            models.CheckConstraint(condition=Q(count__gt=0), name="bill_tender_count_positive"),
        ]

    def __str__(self) -> str:
        return f"₹{self.denomination.value} x {self.count} tendered (Bill #{self.bill_id})"
