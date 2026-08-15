from django import forms

from .models import Product


class ProductForm(forms.ModelForm):
    """Add/edit form for Product management. A plain ModelForm already
    enforces the model's constraints (unique product_id, etc.) via
    full_clean() -- the clean_* methods below only exist to turn the raw
    "Constraint '...' is violated" wording into a message a shop admin
    would actually understand.
    """

    class Meta:
        model = Product
        fields = ["product_id", "name", "stock", "price", "tax_percent"]
        widgets = {
            "stock": forms.NumberInput(attrs={"min": "0"}),
            "price": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "tax_percent": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }

    def clean_stock(self):
        stock = self.cleaned_data["stock"]
        if stock < 0:
            raise forms.ValidationError("Stock cannot be negative.")
        return stock

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price <= 0:
            raise forms.ValidationError("Price must be greater than zero.")
        return price

    def clean_tax_percent(self):
        tax_percent = self.cleaned_data["tax_percent"]
        if tax_percent < 0:
            raise forms.ValidationError("Tax percent cannot be negative.")
        return tax_percent


class BillingForm(forms.Form):
    """Validates the customer email field on the billing page. The repeated
    product_id/quantity rows and the per-denomination cash-received counts
    are parsed separately (see parse_line_items / parse_tendered) since
    they're dynamically rendered from the Product/Denomination tables rather
    than being fixed Django form fields.
    """

    customer_email = forms.EmailField(label="Customer email")


def parse_line_items(post_data) -> tuple[list[tuple[str, str]], list[str]]:
    """Reads the repeated product_id[]/quantity[] fields from a QueryDict.

    Returns (rows, errors) where rows is a list of (product_id, quantity_raw)
    string pairs for every row where a product ID was actually entered
    (blank trailing rows added by the "Add New" button and never filled in
    are silently skipped), and errors is a list of per-row format problems
    (non-integer quantity) suitable for display to the user.
    """
    product_ids = post_data.getlist("product_id")
    quantities = post_data.getlist("quantity")

    rows: list[tuple[str, str]] = []
    errors: list[str] = []
    for row_number, (product_id, quantity_raw) in enumerate(zip(product_ids, quantities), start=1):
        product_id = product_id.strip()
        quantity_raw = quantity_raw.strip()
        if not product_id and not quantity_raw:
            continue
        if not product_id:
            errors.append(f"Row {row_number}: Product ID is required.")
            continue
        if not quantity_raw:
            errors.append(f"Row {row_number}: Quantity is required.")
            continue
        rows.append((product_id, quantity_raw))
    return rows, errors


def parse_tendered(post_data, denomination_values: list[int]) -> tuple[dict[int, int], list[str]]:
    """Reads one ``tender_<value>`` field per known shop denomination (e.g.
    ``tender_500``) -- how many of that note/coin the customer handed over.
    Blank/zero fields are simply omitted from the result rather than being
    an error, since a cashier only fills in the denominations actually used.
    """
    tendered: dict[int, int] = {}
    errors: list[str] = []
    for value in denomination_values:
        raw = post_data.get(f"tender_{value}", "").strip()
        if not raw:
            continue
        try:
            count = int(raw)
        except ValueError:
            errors.append(f"Count received for Rs. {value} must be a whole number.")
            continue
        if count < 0:
            errors.append(f"Count received for Rs. {value} cannot be negative.")
            continue
        if count > 0:
            tendered[value] = count
    return tendered, errors
