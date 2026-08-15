from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase

from billing.models import Bill, Denomination, Product
from billing.services.billing_service import LineItemInput, create_bill
from billing.tasks import enqueue_invoice_email, send_invoice_email_task


class SendInvoiceEmailTaskTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            product_id="A1", name="Product A", stock=10, price=Decimal("100.00"), tax_percent=Decimal("10.00")
        )
        Denomination.objects.create(value=50, available_count=10)
        Denomination.objects.create(value=20, available_count=10)

    def _create_bill(self) -> Bill:
        # Not wrapped in captureOnCommitCallbacks, so the on_commit hook that
        # would enqueue the email is queued but discarded at test rollback --
        # this lets the tests below call/inspect the task deterministically.
        return create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="A1", quantity=1)],
            tendered={50: 1, 20: 3},  # Rs. 110, exact
        )

    def test_bill_creation_enqueues_the_email_task(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._create_bill()
        self.assertEqual(len(mail.outbox), 1)

    def test_task_sends_email_with_correct_content_and_marks_bill_sent(self):
        bill = self._create_bill()
        mail.outbox.clear()

        send_invoice_email_task(bill.id)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("buyer@example.com", sent.to)
        self.assertIn(f"Bill #{bill.id}", sent.subject)
        self.assertIn("A1", sent.body)
        self.assertIn("110.00", sent.body)
        self.assertIn("Rs. 50 x 1", sent.body)  # cash received breakdown
        self.assertIn("Rs. 20 x 3", sent.body)

        # An HTML alternative is attached alongside the plain-text body, and
        # carries the same key figures so the two never drift apart.
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mimetype = sent.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("A1", html_body)
        self.assertIn("Rs. 110.00", html_body)
        self.assertIn("Rs. 50 &times;1", html_body)  # cash received chip
        self.assertIn("Rs. 20 &times;3", html_body)

        bill.refresh_from_db()
        self.assertEqual(bill.email_status, Bill.EmailStatus.SENT)
        self.assertIsNotNone(bill.email_sent_at)
        self.assertEqual(bill.email_error, "")

    def test_task_marks_bill_failed_after_retries_are_exhausted(self):
        bill = self._create_bill()
        # Simulate the final retry attempt (retries == max_retries) rather
        # than driving all 3 real retries through Celery's retry/backoff
        # machinery -- what matters here is the task's own bookkeeping once
        # it knows this was the last attempt.
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp down")):
            with self.assertRaises(Exception):
                send_invoice_email_task.apply(args=[bill.id], retries=3)

        bill.refresh_from_db()
        self.assertEqual(bill.email_status, Bill.EmailStatus.FAILED)
        self.assertIn("smtp down", bill.email_error)


class EnqueueInvoiceEmailTests(TestCase):
    def test_returns_true_and_delivers_when_broker_available(self):
        product = Product.objects.create(
            product_id="A1", name="Product A", stock=10, price=Decimal("100.00"), tax_percent=Decimal("0.00")
        )
        Denomination.objects.create(value=50, available_count=10)
        bill = create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="A1", quantity=1)],
            tendered={50: 2},
        )
        mail.outbox.clear()

        result = enqueue_invoice_email(bill.id)

        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)  # CELERY_TASK_ALWAYS_EAGER runs it inline

    def test_returns_false_and_logs_instead_of_raising_when_broker_unavailable(self):
        with patch("billing.tasks.send_invoice_email_task.delay", side_effect=Exception("connection refused")):
            result = enqueue_invoice_email(bill_id=123)
        self.assertFalse(result)
