from django.urls import path

from .views import (
    dashboard_view,
    creditors_list_view,
    creditor_ledger_view,
    upload_summary_view, 
    submit_summary_view,
)

urlpatterns = [
    path("dashboard/", dashboard_view, name="ar-dashboard"),
    path("creditors/", creditors_list_view, name="ar-creditors"),
    path("creditors/<int:creditor_id>/", creditor_ledger_view, name="ar-creditor-ledger"),
    path("summaries/", submit_summary_view, name="ar-summaries"),
    path("summaries/upload/", upload_summary_view, name="ar-summary-upload"),
]