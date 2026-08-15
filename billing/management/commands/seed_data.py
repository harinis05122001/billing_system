from decimal import Decimal

from django.core.management.base import BaseCommand

from billing.models import Denomination, Product

PRODUCTS = [
    # product_id, name, stock, price, tax_percent
    ("P001", "Notebook A5", 100, Decimal("60.00"), Decimal("5.00")),
    ("P002", "Ballpoint Pen (Box of 10)", 60, Decimal("120.00"), Decimal("12.00")),
    ("P003", "USB-C Cable 1m", 40, Decimal("299.00"), Decimal("18.00")),
    ("P004", "Wireless Mouse", 25, Decimal("799.00"), Decimal("18.00")),
    ("P005", "Coffee Mug", 50, Decimal("199.00"), Decimal("5.00")),
    ("P006", "Desk Lamp", 15, Decimal("899.00"), Decimal("12.00")),
    ("P007", "Sticky Notes Pack", 200, Decimal("45.00"), Decimal("0.00")),
    ("P008", "Bluetooth Speaker", 10, Decimal("1499.00"), Decimal("18.00")),
]

# Matches the denomination values shown in the assignment's wireframe.
DENOMINATIONS = [
    (500, 30),
    (50, 40),
    (20, 40),
    (10, 40),
    (5, 40),
    (2, 40),
    (1, 40),
]


class Command(BaseCommand):
    help = "Seeds sample products and shop denomination inventory. Safe to run multiple times."

    def handle(self, *args, **options):
        products_created = 0
        for product_id, name, stock, price, tax_percent in PRODUCTS:
            _, created = Product.objects.get_or_create(
                product_id=product_id,
                defaults={"name": name, "stock": stock, "price": price, "tax_percent": tax_percent},
            )
            products_created += int(created)

        denominations_created = 0
        for value, count in DENOMINATIONS:
            _, created = Denomination.objects.get_or_create(value=value, defaults={"available_count": count})
            denominations_created += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {products_created} product(s) and {denominations_created} "
                "denomination(s) created (existing records left untouched)."
            )
        )
