from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from billing.models import Bill, Customer, Denomination, Product


class ProductModelTests(TestCase):
    def test_product_id_must_be_unique(self):
        Product.objects.create(product_id="P001", name="Widget", stock=10, price=Decimal("10.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(product_id="P001", name="Other Widget", stock=5, price=Decimal("5.00"))

    def test_price_must_be_positive(self):
        product = Product(product_id="P002", name="Free Item", stock=1, price=Decimal("0.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            product.save()

    def test_tax_percent_cannot_be_negative(self):
        product = Product(
            product_id="P003", name="Bad Tax", stock=1, price=Decimal("10.00"), tax_percent=Decimal("-5.00")
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            product.save()


class DenominationModelTests(TestCase):
    def test_value_must_be_unique(self):
        Denomination.objects.create(value=100, available_count=10)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Denomination.objects.create(value=100, available_count=5)


class CustomerModelTests(TestCase):
    def test_email_must_be_unique(self):
        Customer.objects.create(email="a@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Customer.objects.create(email="a@example.com")


class BillRelationshipTests(TestCase):
    def test_bill_items_and_customer_relationship(self):
        customer = Customer.objects.create(email="buyer@example.com")
        bill = Bill.objects.create(
            customer=customer,
            subtotal=Decimal("100.00"),
            tax_total=Decimal("10.00"),
            net_total=Decimal("110.00"),
            rounded_total=Decimal("110.00"),
            paid_amount=Decimal("110.00"),
            balance_amount=Decimal("0.00"),
        )
        self.assertEqual(list(customer.bills.all()), [bill])
        self.assertEqual(bill.email_status, Bill.EmailStatus.PENDING)
