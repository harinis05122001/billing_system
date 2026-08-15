from decimal import Decimal

from django.test import TestCase

from billing.models import Bill, Denomination, Product
from billing.services.billing_service import BillingError, LineItemInput, create_bill


class CreateBillCalculationTests(TestCase):
    def setUp(self):
        self.product_a = Product.objects.create(
            product_id="A1", name="Product A", stock=10, price=Decimal("500.00"), tax_percent=Decimal("12.00")
        )
        self.product_b = Product.objects.create(
            product_id="B1", name="Product B", stock=10, price=Decimal("250.55"), tax_percent=Decimal("18.00")
        )
        for value, count in [(500, 10), (50, 10), (20, 10), (10, 10), (5, 10), (2, 10), (1, 10)]:
            Denomination.objects.create(value=value, available_count=count)

    def test_bill_calculation_matches_expected_totals_and_floors_final_amount(self):
        bill = create_bill(
            customer_email="buyer@example.com",
            line_items=[
                LineItemInput(product_id="A1", quantity=2),
                LineItemInput(product_id="B1", quantity=2),
            ],
            tendered={500: 4},  # Rs. 2000
        )

        self.assertEqual(bill.subtotal, Decimal("1501.10"))
        self.assertEqual(bill.tax_total, Decimal("210.20"))
        self.assertEqual(bill.net_total, Decimal("1711.30"))
        self.assertEqual(bill.rounded_total, Decimal("1711.00"))  # floored -- the 0.30 is waived
        self.assertEqual(bill.paid_amount, Decimal("2000.00"))
        self.assertEqual(bill.balance_amount, Decimal("289.00"))

        # Per-item tax figures (rounded individually) must sum exactly to the bill's tax total.
        item_tax_sum = sum((item.tax_amount for item in bill.items.all()), Decimal("0"))
        self.assertEqual(item_tax_sum, bill.tax_total)

    def test_stock_is_reduced_after_successful_bill(self):
        create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="A1", quantity=3)],
            tendered={500: 3, 50: 3, 20: 1, 10: 1},  # Rs. 1680, exact
        )
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)

    def test_insufficient_stock_raises_and_leaves_database_unchanged(self):
        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                line_items=[LineItemInput(product_id="A1", quantity=999)],
                tendered={500: 200},
            )
        self.assertEqual(Bill.objects.count(), 0)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 10)

    def test_unknown_product_id_raises(self):
        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                line_items=[LineItemInput(product_id="DOES-NOT-EXIST", quantity=1)],
                tendered={50: 2},
            )
        self.assertEqual(Bill.objects.count(), 0)

    def test_duplicate_product_in_bill_is_rejected(self):
        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                line_items=[
                    LineItemInput(product_id="A1", quantity=1),
                    LineItemInput(product_id="A1", quantity=1),
                ],
                tendered={500: 4},
            )

    def test_zero_or_negative_quantity_rejected(self):
        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                line_items=[LineItemInput(product_id="A1", quantity=0)],
                tendered={500: 4},
            )

    def test_insufficient_payment_is_rejected_before_any_inventory_write(self):
        till_50 = Denomination.objects.get(value=50)
        starting_count = till_50.available_count

        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                line_items=[LineItemInput(product_id="A1", quantity=1)],  # Rs. 560 owed
                tendered={50: 2},  # only Rs. 100 tendered
            )

        self.assertEqual(Bill.objects.count(), 0)
        till_50.refresh_from_db()
        self.assertEqual(till_50.available_count, starting_count)

    def test_unknown_denomination_value_rejected(self):
        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                line_items=[LineItemInput(product_id="A1", quantity=1)],
                tendered={7: 1},  # Rs. 7 isn't a denomination the shop tracks
            )
        self.assertEqual(Bill.objects.count(), 0)

    def test_empty_tender_rejected(self):
        with self.assertRaises(BillingError):
            create_bill(customer_email="buyer@example.com", line_items=[], tendered={})

    def test_exact_payment_needs_no_change(self):
        bill = create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="A1", quantity=1)],
            tendered={500: 1, 50: 1, 10: 1},  # Rs. 560, exact
        )
        self.assertEqual(bill.balance_amount, Decimal("0.00"))
        self.assertEqual(bill.change_denominations.count(), 0)

    def test_impossible_change_rolls_back_entire_bill_and_tender(self):
        Denomination.objects.all().delete()
        till_500 = Denomination.objects.create(value=500, available_count=0)
        with self.assertRaises(BillingError):
            create_bill(
                customer_email="buyer@example.com",
                # Rs. 560 owed; tendering two Rs. 500 notes leaves a Rs. 440
                # balance that can't be made from a till holding only Rs. 500 notes.
                line_items=[LineItemInput(product_id="A1", quantity=1)],
                tendered={500: 2},
            )
        self.assertEqual(Bill.objects.count(), 0)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 10)
        till_500.refresh_from_db()
        self.assertEqual(till_500.available_count, 0)  # tender increment was rolled back too

    def test_tendered_cash_is_added_to_till_and_change_is_drawn_from_it(self):
        """Customer pays with a Rs. 500 note for a Rs. 450 bill: the note
        should be recorded and added to the till, and the Rs. 50 change
        should be recorded and subtracted from it -- the exact scenario
        that motivated tracking tendered cash at all.
        """
        Product.objects.create(
            product_id="C1", name="Product C", stock=10, price=Decimal("450.00"), tax_percent=Decimal("0.00")
        )
        till_500 = Denomination.objects.get(value=500)
        till_50 = Denomination.objects.get(value=50)
        starting_500, starting_50 = till_500.available_count, till_50.available_count

        bill = create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="C1", quantity=1)],
            tendered={500: 1},
        )

        self.assertEqual(bill.rounded_total, Decimal("450.00"))
        self.assertEqual(bill.paid_amount, Decimal("500.00"))
        self.assertEqual(bill.balance_amount, Decimal("50.00"))

        tender_row = bill.tendered_denominations.get()
        self.assertEqual(tender_row.denomination.value, 500)
        self.assertEqual(tender_row.count, 1)

        change_row = bill.change_denominations.get()
        self.assertEqual(change_row.denomination.value, 50)
        self.assertEqual(change_row.count, 1)

        till_500.refresh_from_db()
        till_50.refresh_from_db()
        self.assertEqual(till_500.available_count, starting_500 + 1)
        self.assertEqual(till_50.available_count, starting_50 - 1)
