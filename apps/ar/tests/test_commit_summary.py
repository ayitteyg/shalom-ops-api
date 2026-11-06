from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.ar.models import (
    Site,
    Creditor,
    DailySummary,
    CreditSale,
    Collection,
    SummaryUpload,
)
from apps.ar.services.commit_summary import commit_blocks, CommitError


class CommitBlocksServiceTests(TestCase):
    def setUp(self):
        self.ach = Site.objects.create(code="ACH", name="Achimota", is_active=True)
        self.papa = Creditor.objects.create(site=self.ach, name="PAPA", status="active")
        self.kaman = Creditor.objects.create(
            site=self.ach, name="KAMAN SUPPLY CHAIN", status="active"
        )

    def _block(self, **overrides):
        base = {
            "site": "ACH",
            "date": date(2025, 8, 1),
            "credit_sales": [
                {"creditor": "PAPA", "amount": "10000.00"},
                {"creditor": "KAMAN SUPPLY CHAIN", "amount": "5000.00"},
            ],
            "collections": [
                {"creditor": "PAPA", "amount": "3000.00",
                 "payment_method": "cash", "reference": "CASH"},
            ],
        }
        base.update(overrides)
        return base

    def test_commit_happy_path(self):
        results = commit_blocks([self._block()], committed_by="tester")
        assert len(results) == 1
        summary = DailySummary.objects.get(id=results[0]["id"])
        assert summary.credit_sales_total == Decimal("15000.00")
        assert summary.credit_collection_total == Decimal("3000.00")
        assert summary.status == "committed"
        assert CreditSale.objects.filter(summary=summary).count() == 2
        assert CreditSale.objects.filter(summary=summary, is_promo=False).count() == 2
        col = Collection.objects.get(summary=summary)
        assert col.payment_method == "cash"
        assert col.reference == "CASH"

    def test_commit_rejects_unknown_creditor(self):
        bad = self._block()
        bad["credit_sales"].append({"creditor": "GHOST", "amount": "1.00"})
        with self.assertRaises(CommitError) as ctx:
            commit_blocks([bad], committed_by="tester")
        assert any(u["name"] == "GHOST" for u in ctx.exception.detail["unresolved"])
        assert DailySummary.objects.count() == 0  # atomic — nothing written

    def test_commit_rejects_duplicate_date(self):
        commit_blocks([self._block()], committed_by="tester")
        with self.assertRaises(CommitError) as ctx:
            commit_blocks([self._block()], committed_by="tester")
        assert len(ctx.exception.detail["conflicts"]) == 1
        assert DailySummary.objects.count() == 1  # unchanged

    def test_multi_block_rollback_on_error(self):
        good = self._block()
        bad = {
            "site": "ACH",
            "date": date(2025, 8, 2),
            "credit_sales": [{"creditor": "GHOST", "amount": "1.00"}],
            "collections": [],
        }
        with self.assertRaises(CommitError):
            commit_blocks([good, bad], committed_by="tester")
        assert DailySummary.objects.count() == 0  # whole batch rolled back

    def test_creditor_case_insensitive_match(self):
        block = self._block()
        block["credit_sales"] = [{"creditor": "papa", "amount": "100.00"}]
        block["collections"] = []
        results = commit_blocks([block], committed_by="tester")
        summary = DailySummary.objects.get(id=results[0]["id"])
        assert CreditSale.objects.get(summary=summary).creditor_id == self.papa.id


class SubmitSummaryEndpointTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(code="ACH", name="Achimota", is_active=True)
        Creditor.objects.create(site=self.site, name="PAPA", status="active")
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username="tester", email="t@example.com", password="pass"
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_201_on_commit(self):
        resp = self.client.post(
            "/api/v1/ar/summaries/",
            {
                "blocks": [{
                    "site": "ACH", "date": "2025-08-01",
                    "credit_sales": [{"creditor": "PAPA", "amount": "100.00"}],
                    "collections": [],
                }]
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert resp.data["blocks"][0]["site"] == "ACH"

    def test_409_on_duplicate(self):
        payload = {
            "blocks": [{
                "site": "ACH", "date": "2025-08-01",
                "credit_sales": [{"creditor": "PAPA", "amount": "100.00"}],
                "collections": [],
            }]
        }
        self.client.post("/api/v1/ar/summaries/", payload, format="json")
        resp = self.client.post("/api/v1/ar/summaries/", payload, format="json")
        assert resp.status_code == 409
        assert resp.data["conflicts"][0]["site"] == "ACH"

    def test_400_on_unknown_creditor(self):
        resp = self.client.post(
            "/api/v1/ar/summaries/",
            {
                "blocks": [{
                    "site": "ACH", "date": "2025-08-01",
                    "credit_sales": [{"creditor": "GHOST", "amount": "1.00"}],
                    "collections": [],
                }]
            },
            format="json",
        )
        assert resp.status_code == 400
        assert resp.data["unresolved"][0]["name"] == "GHOST"

    def test_401_when_unauthenticated(self):
        self.client.credentials()  # clear token
        resp = self.client.post(
            "/api/v1/ar/summaries/",
            {"blocks": []},
            format="json",
        )
        assert resp.status_code == 401


class UploadEndpointTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.ach = Site.objects.create(code="ACH", name="Achimota", is_active=True)
        Creditor.objects.create(site=self.ach, name="PAPA", status="active")
        self.user = get_user_model().objects.create_user(
            username="uploader", email="u@example.com", password="pass"
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def _excel(self, rows):
        from io import BytesIO
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["Date", "Station", "Creditor", "Type", "Amount (₵)", "Notes"])
        for r in rows:
            ws.append(r)
        buf = BytesIO(); wb.save(buf); buf.seek(0); buf.name = "test.xlsx"
        return buf

    def test_upload_preview_returns_blocks_and_unresolved(self):
        buf = self._excel([
            ("2025-08-01", "Achimota", "PAPA", "Credit Sale", 100, None),
            ("2025-08-01", "Achimota", "GHOST", "Credit Sale", 50, None),
        ])
        resp = self.client.post(
            "/api/v1/ar/summaries/upload/", {"file": buf}, format="multipart"
        )
        assert resp.status_code == 200, resp.content
        assert resp.data["is_clean"] is False
        assert resp.data["unresolved"][0]["name"] == "GHOST"
        assert len(resp.data["blocks"]) == 1
        assert resp.data["upload_id"] is not None
        assert SummaryUpload.objects.count() == 1

    def test_duplicate_file_rejected(self):
        buf1 = self._excel([("2025-08-01", "Achimota", "PAPA", "Credit Sale", 100, None)])
        self.client.post("/api/v1/ar/summaries/upload/", {"file": buf1}, format="multipart")
        buf2 = self._excel([("2025-08-01", "Achimota", "PAPA", "Credit Sale", 100, None)])
        resp = self.client.post(
            "/api/v1/ar/summaries/upload/", {"file": buf2}, format="multipart"
        )
        assert resp.status_code == 409
        assert resp.data["existing_upload_id"] is not None