import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def send_invoice_email_task(self, bill_id: int) -> None:
    """Sends the invoice email for a bill and records the outcome on the
    bill itself (email_status/email_sent_at/email_error). Runs in the
    Celery worker process, decoupled from the request that created the bill.
    """
    from billing.models import Bill

    bill = Bill.objects.select_related("customer").prefetch_related(
        "items__product", "tendered_denominations__denomination", "change_denominations__denomination"
    ).get(pk=bill_id)

    subject = f"Your invoice - Bill #{bill.id}"
    text_body = render_to_string("billing/email/invoice.txt", {"bill": bill})
    html_body = render_to_string("billing/email/invoice.html", {"bill": bill})

    try:
        email = EmailMultiAlternatives(subject=subject, body=text_body, to=[bill.customer.email])
        email.attach_alternative(html_body, "text/html")
        email.send()
    except Exception as exc:
        # Only mark as FAILED once retries are exhausted -- while a retry is
        # still scheduled, leave the status as-is rather than flashing a
        # misleading "failed" state for what may still succeed shortly.
        if self.request.retries >= self.max_retries:
            bill.email_status = Bill.EmailStatus.FAILED
            bill.email_error = str(exc)
            bill.save(update_fields=["email_status", "email_error"])
        raise
    else:
        bill.email_status = Bill.EmailStatus.SENT
        bill.email_sent_at = timezone.now()
        bill.email_error = ""
        bill.save(update_fields=["email_status", "email_sent_at", "email_error"])


def enqueue_invoice_email(bill_id: int) -> bool:
    """Best-effort enqueue of send_invoice_email_task -- returns False (and
    logs a warning) instead of raising if the broker is unavailable, since a
    queueing failure must never break the caller's flow, whether that's a
    bill just being created or a manual resend request.
    """
    try:
        send_invoice_email_task.delay(bill_id)
    except Exception:
        logger.warning("Could not enqueue invoice email for bill %s (broker unavailable?)", bill_id, exc_info=True)
        return False
    return True
