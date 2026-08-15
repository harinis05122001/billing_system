from django.contrib import admin

from .models import Bill, BillDenomination, BillItem, BillTender, Customer, Denomination, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("product_id", "name", "price", "tax_percent", "stock", "updated_at")
    search_fields = ("product_id", "name")
    list_filter = ("tax_percent",)
    ordering = ("product_id",)


@admin.register(Denomination)
class DenominationAdmin(admin.ModelAdmin):
    list_display = ("value", "available_count")
    ordering = ("-value",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at")
    search_fields = ("email",)
    ordering = ("email",)


class ReadOnlyTabularInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class BillItemInline(ReadOnlyTabularInline):
    model = BillItem
    fields = ("product", "unit_price", "quantity", "tax_percent", "tax_amount", "line_total")
    readonly_fields = fields


class BillTenderInline(ReadOnlyTabularInline):
    model = BillTender
    verbose_name = "Cash received (tendered)"
    verbose_name_plural = "Cash received (tendered)"
    fields = ("denomination", "count")
    readonly_fields = fields


class BillDenominationInline(ReadOnlyTabularInline):
    model = BillDenomination
    verbose_name = "Change given"
    verbose_name_plural = "Change given"
    fields = ("denomination", "count")
    readonly_fields = fields


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    """Bills are financial records created only through the billing flow
    (billing_service.create_bill), never hand-edited -- editing one here
    could desync stock/denomination inventory that has already moved.
    Kept fully read-only: viewable and searchable, never add/change/delete.
    """

    list_display = (
        "id",
        "customer",
        "created_at",
        "rounded_total",
        "paid_amount",
        "balance_amount",
        "email_status",
    )
    list_filter = ("email_status", "created_at")
    search_fields = ("customer__email", "id")
    date_hierarchy = "created_at"
    inlines = [BillItemInline, BillTenderInline, BillDenominationInline]
    readonly_fields = [f.name for f in Bill._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
