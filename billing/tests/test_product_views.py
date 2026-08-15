from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from billing.models import Denomination, Product
from billing.services.billing_service import LineItemInput, create_bill


class ProductListViewTests(TestCase):
    def test_lists_existing_products(self):
        Product.objects.create(product_id="P001", name="Widget", stock=5, price=Decimal("10.00"))
        response = self.client.get(reverse("billing:product_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "P001")
        self.assertContains(response, "Widget")

    def test_empty_state_when_no_products(self):
        response = self.client.get(reverse("billing:product_list"))
        self.assertContains(response, "No products yet")


class ProductCreateViewTests(TestCase):
    def test_get_renders_empty_form(self):
        response = self.client.get(reverse("billing:product_add"))
        self.assertEqual(response.status_code, 200)

    def test_valid_post_creates_product_and_redirects(self):
        response = self.client.post(
            reverse("billing:product_add"),
            {"product_id": "P100", "name": "New Gadget", "stock": "20", "price": "150.00", "tax_percent": "18.00"},
        )
        self.assertRedirects(response, reverse("billing:product_list"))
        product = Product.objects.get(product_id="P100")
        self.assertEqual(product.name, "New Gadget")
        self.assertEqual(product.stock, 20)

    def test_duplicate_product_id_rejected(self):
        Product.objects.create(product_id="P001", name="Widget", stock=5, price=Decimal("10.00"))
        response = self.client.post(
            reverse("billing:product_add"),
            {"product_id": "P001", "name": "Duplicate", "stock": "1", "price": "5.00", "tax_percent": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Product.objects.count(), 1)

    def test_zero_price_rejected_with_friendly_message(self):
        response = self.client.post(
            reverse("billing:product_add"),
            {"product_id": "P200", "name": "Free Item", "stock": "1", "price": "0.00", "tax_percent": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Price must be greater than zero.")
        self.assertFalse(Product.objects.filter(product_id="P200").exists())

    def test_negative_tax_percent_rejected(self):
        response = self.client.post(
            reverse("billing:product_add"),
            {"product_id": "P201", "name": "Bad Tax", "stock": "1", "price": "5.00", "tax_percent": "-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tax percent cannot be negative.")
        self.assertFalse(Product.objects.filter(product_id="P201").exists())


class ProductEditViewTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            product_id="P001", name="Widget", stock=5, price=Decimal("10.00"), tax_percent=Decimal("5.00")
        )

    def test_get_prefills_existing_values(self):
        response = self.client.get(reverse("billing:product_edit", args=[self.product.pk]))
        self.assertContains(response, "Widget")

    def test_valid_post_updates_product(self):
        response = self.client.post(
            reverse("billing:product_edit", args=[self.product.pk]),
            {"product_id": "P001", "name": "Widget Pro", "stock": "8", "price": "12.00", "tax_percent": "5.00"},
        )
        self.assertRedirects(response, reverse("billing:product_list"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Widget Pro")
        self.assertEqual(self.product.stock, 8)


class ProductDeleteViewTests(TestCase):
    def test_deletes_product_with_no_sales_history(self):
        product = Product.objects.create(product_id="P001", name="Widget", stock=5, price=Decimal("10.00"))
        response = self.client.post(reverse("billing:product_delete", args=[product.pk]))
        self.assertRedirects(response, reverse("billing:product_list"))
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())

    def test_cannot_delete_product_that_has_been_sold(self):
        product = Product.objects.create(
            product_id="P001", name="Widget", stock=5, price=Decimal("10.00"), tax_percent=Decimal("0.00")
        )
        Denomination.objects.create(value=10, available_count=10)
        create_bill(
            customer_email="buyer@example.com",
            line_items=[LineItemInput(product_id="P001", quantity=1)],
            tendered={10: 1},
        )

        # follow=True so the message set on the redirect target is read from
        # this same response -- assertRedirects performs its own internal
        # fetch of the target URL, which would otherwise consume (and clear)
        # the one-time message before a separate follow-up GET ever sees it.
        response = self.client.post(reverse("billing:product_delete", args=[product.pk]), follow=True)

        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertContains(response, "Cannot delete")
