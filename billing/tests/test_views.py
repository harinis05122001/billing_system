from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Bill, Denomination, Product
from billing.services.billing_service import LineItemInput, create_bill


class BillingFormViewTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            product_id="A1", name="Product A", stock=10, price=Decimal("100.00"), tax_percent=Decimal("10.00")
        )
        for value, count in [(50, 10), (20, 10), (10, 10), (5, 10), (2, 10), (1, 10)]:
            Denomination.objects.create(value=value, available_count=count)

    def test_get_renders_form_with_products_and_denominations(self):
        response = self.client.get(reverse("billing:billing_form"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A1")
        self.assertContains(response, "Rs. 50")

    def test_successful_post_creates_bill_redirects_and_sends_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("billing:billing_form"),
                {
                    "customer_email": "buyer@example.com",
                    "product_id": ["A1"],
                    "quantity": ["2"],
                    "tender_50": "6",  # Rs. 300
                },
            )
        self.assertEqual(Bill.objects.count(), 1)
        bill = Bill.objects.get()
        self.assertRedirects(response, reverse("billing:bill_detail", args=[bill.id]))
        self.assertEqual(bill.rounded_total, Decimal("220.00"))
        self.assertEqual(bill.balance_amount, Decimal("80.00"))
        # CELERY_TASK_ALWAYS_EAGER runs the enqueued task inline during tests.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("buyer@example.com", mail.outbox[0].to)

    def test_unknown_product_shows_error_without_creating_bill(self):
        response = self.client.post(
            reverse("billing:billing_form"),
            {
                "customer_email": "buyer@example.com",
                "product_id": ["NOPE"],
                "quantity": ["1"],
                "tender_50": "6",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Bill.objects.count(), 0)
        self.assertContains(response, "Unknown product ID")

    def test_insufficient_payment_shows_error_without_creating_bill(self):
        response = self.client.post(
            reverse("billing:billing_form"),
            {
                "customer_email": "buyer@example.com",
                "product_id": ["A1"],
                "quantity": ["1"],
                "tender_10": "1",  # Rs. 10, but Rs. 110 is owed
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Bill.objects.count(), 0)
        self.assertContains(response, "Insufficient payment")

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            reverse("billing:billing_form"),
            {
                "customer_email": "not-an-email",
                "product_id": ["A1"],
                "quantity": ["1"],
                "tender_50": "2",
                "tender_10": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Bill.objects.count(), 0)

    def test_blank_trailing_rows_are_ignored(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("billing:billing_form"),
                {
                    "customer_email": "buyer@example.com",
                    "product_id": ["A1", ""],
                    "quantity": ["1", ""],
                    "tender_50": "2",
                    "tender_10": "1",
                },
            )
        self.assertEqual(Bill.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_no_tender_entered_shows_error_without_creating_bill(self):
        response = self.client.post(
            reverse("billing:billing_form"),
            {
                "customer_email": "buyer@example.com",
                "product_id": ["A1"],
                "quantity": ["1"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Bill.objects.count(), 0)


class BillDetailViewTests(TestCase):
    def test_returns_404_for_unknown_bill(self):
        response = self.client.get(reverse("billing:bill_detail", args=[9999]))
        self.assertEqual(response.status_code, 404)


class ResendInvoiceEmailViewTests(TestCase):
    def setUp(self):
        Product.objects.create(
            product_id="A1", name="Product A", stock=10, price=Decimal("100.00"), tax_percent=Decimal("0.00")
        )
        Denomination.objects.create(value=50, available_count=10)
        self.bill = create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="A1", quantity=1)],
            tendered={50: 2},
        )

    def test_resend_requeues_and_flips_failed_bill_back_to_sent(self):
        self.bill.email_status = Bill.EmailStatus.FAILED
        self.bill.email_error = "smtp down"
        self.bill.save(update_fields=["email_status", "email_error"])
        mail.outbox.clear()

        response = self.client.post(reverse("billing:resend_invoice_email", args=[self.bill.id]))

        self.assertRedirects(response, reverse("billing:bill_detail", args=[self.bill.id]))
        self.assertEqual(len(mail.outbox), 1)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.email_status, Bill.EmailStatus.SENT)
        self.assertEqual(self.bill.email_error, "")

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("billing:resend_invoice_email", args=[self.bill.id]))
        self.assertEqual(response.status_code, 405)

    def test_unknown_bill_returns_404(self):
        response = self.client.post(reverse("billing:resend_invoice_email", args=[9999]))
        self.assertEqual(response.status_code, 404)

    def test_broker_failure_shows_error_message_without_crashing(self):
        with patch("billing.tasks.send_invoice_email_task.delay", side_effect=Exception("broker down")):
            response = self.client.post(
                reverse("billing:resend_invoice_email", args=[self.bill.id]), follow=True
            )
        self.assertContains(response, "Could not queue the invoice email")


class CustomerHistoryViewTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            product_id="A1", name="Product A", stock=1000, price=Decimal("100.00"), tax_percent=Decimal("0.00")
        )
        for value, count in [(50, 1000), (20, 1000), (10, 1000)]:
            Denomination.objects.create(value=value, available_count=count)

    def _bill_for(self, email: str) -> Bill:
        return create_bill(
            customer_email=email,
            line_items=[LineItemInput(product_id="A1", quantity=1)],
            tendered={50: 2},
        )

    def test_default_view_lists_all_bills_most_recent_first(self):
        bill1 = self._bill_for("first@example.com")
        bill2 = self._bill_for("second@example.com")

        response = self.client.get(reverse("billing:customer_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Invoice #{bill1.id}")
        self.assertContains(response, f"Invoice #{bill2.id}")
        content = response.content
        self.assertLess(
            content.index(f"Invoice #{bill2.id}".encode()),
            content.index(f"Invoice #{bill1.id}".encode()),
        )

    def test_partial_email_filter_matches(self):
        bill = self._bill_for("history@example.com")
        # The other customer's email still legitimately appears in the
        # autocomplete <datalist> (it always lists every known customer) --
        # what must be excluded is their *bill* from the results list.
        other_bill = self._bill_for("someone-else@example.com")

        response = self.client.get(reverse("billing:customer_history"), {"email": "histo"})

        self.assertContains(response, f"Invoice #{bill.id}")
        self.assertNotContains(response, f"Invoice #{other_bill.id}")

    def test_date_range_filter_excludes_bills_outside_the_range(self):
        bill = self._bill_for("history@example.com")
        future = (timezone.now() + timedelta(days=2)).date().isoformat()

        response = self.client.get(reverse("billing:customer_history"), {"date_from": future})

        self.assertNotContains(response, f"Invoice #{bill.id}")
        self.assertContains(response, "No purchases match these filters")

    def test_date_range_filter_includes_bills_inside_the_range(self):
        bill = self._bill_for("history@example.com")
        past = (timezone.now() - timedelta(days=2)).date().isoformat()
        future = (timezone.now() + timedelta(days=2)).date().isoformat()

        response = self.client.get(reverse("billing:customer_history"), {"date_from": past, "date_to": future})

        self.assertContains(response, f"Invoice #{bill.id}")

    def test_email_status_filter_narrows_results(self):
        # create_bill() called directly (not via captureOnCommitCallbacks) leaves
        # the email task un-run, so the bill stays at its default "pending" status.
        bill = self._bill_for("history@example.com")

        pending_response = self.client.get(reverse("billing:customer_history"), {"email_status": "pending"})
        sent_response = self.client.get(reverse("billing:customer_history"), {"email_status": "sent"})

        self.assertContains(pending_response, f"Invoice #{bill.id}")
        self.assertNotContains(sent_response, f"Invoice #{bill.id}")

    def test_invalid_date_filter_is_ignored_not_a_server_error(self):
        self._bill_for("history@example.com")
        response = self.client.get(reverse("billing:customer_history"), {"date_from": "not-a-date"})
        self.assertEqual(response.status_code, 200)

    def test_no_matches_shows_filter_empty_state_not_no_bills_state(self):
        self._bill_for("history@example.com")
        response = self.client.get(reverse("billing:customer_history"), {"email": "no-such-customer"})
        self.assertContains(response, "No purchases match these filters")
        self.assertNotContains(response, "No bills yet")

    def test_no_bills_at_all_shows_distinct_empty_state(self):
        response = self.client.get(reverse("billing:customer_history"))
        self.assertContains(response, "No bills yet")

    def test_pagination_second_page_preserves_active_filter(self):
        for i in range(25):
            self._bill_for(f"bulk{i}@example.com")

        response = self.client.get(reverse("billing:customer_history"), {"email": "bulk", "page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page 2 of 2")

    def test_selecting_a_purchase_shows_its_items(self):
        bill = self._bill_for("history@example.com")
        response = self.client.get(reverse("billing:bill_detail", args=[bill.id]))
        self.assertContains(response, "A1")
        self.assertContains(response, "history@example.com")
