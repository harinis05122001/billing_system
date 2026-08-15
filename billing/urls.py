from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.index, name="index"),
    path("billing/", views.billing_form_view, name="billing_form"),
    path("billing/<int:bill_id>/", views.bill_detail_view, name="bill_detail"),
    path("billing/<int:bill_id>/resend-email/", views.resend_invoice_email_view, name="resend_invoice_email"),
    path("customers/history/", views.customer_history_view, name="customer_history"),
    path("products/", views.product_list_view, name="product_list"),
    path("products/add/", views.product_create_view, name="product_add"),
    path("products/<int:pk>/edit/", views.product_edit_view, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete_view, name="product_delete"),
]
