from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from openpyxl import Workbook

from apps.ar.models import Site, Creditor
from apps.ar.services.excel_import import (
    parse_summary_file,
    clean_name,
    parse_amount,
    extract_payment_method,
    STATION_TO_CODE,
)


def build_workbook(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Station", "Creditor", "Type", "Amount (₵)", "Notes"])
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class HelperTests(TestCase):
    def test_clean_name_strips_and_collapses(self):
        assert clean_name("  YOKWA   GAS  LIMITED  ") == "YOKWA GAS LIMITED"
        assert clean_name(None) == ""

    def test_parse_amount_blank_is_zero(self):
        amt, blank = parse_amount(None)
        assert amt == Decimal("0.00") and blank is True
        amt, blank = parse_amount("  ")
        assert amt == Decimal("0.00") and blank is True

    def test_parse_amount_decimal_string(self):
        amt, blank = parse_amount("349,976.50")
        assert amt == Decimal("349976.50") and blank is False

    def test_extract_payment_method(self):
        assert extract_payment_method("CHEQUE") == "cheque"
        assert extract_payment_method("cheque no 123") == "cheque"
        assert extract_payment_method("MR. BISMARK") == "unspecified"
        assert extract_payment_method("") == "unspecified"
        assert extract_payment_method("sc") == "smart_card"

    def test_station_map_complete(self):
        assert STATION_TO_CODE["achimota"] == "ACH"
        assert STATION_TO_CODE["achimota kumasi"] == "AKS"
        assert STATION_TO_CODE["east legon"] == "ELG"
        assert STATION_TO_CODE["palmwine"] == "PWJ"
        assert STATION_TO_CODE["ofankor"] == "OFK"


class ParserTests(TestCase):
    def setUp(self):
        self.ach = Site.objects.create(code="ACH", name="Achimota", is_active=True)
        self.aks = Site.objects.create(code="AKS", name="Achimota Kumasi", is_active=True)
        Creditor.objects.create(site=self.ach, name="PAPA", status="active")
        Creditor.objects.create(site=self.ach, name="KAMAN SUPPLY CHAIN", status="active")
        Creditor.objects.create(site=self.aks, name="GH-Express", status="active")

    def test_groups_and_sums_duplicates(self):
        buf = build_workbook([
            ("2025-08-02", "Achimota", "PAPA", "Credit Sale", 100, None),
            ("2025-08-02", "Achimota", "PAPA", "Credit Sale", 50.5, None),
            ("2025-08-02", "Achimota", "PAPA", "Payment", 30, "CASH"),
        ])
        result = parse_summary_file(buf)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b.site == "ACH"
        assert b.credit_sales == [{"creditor": "PAPA", "amount": "150.50"}]
        assert b.collections == [{
            "creditor": "PAPA", "amount": "30.00",
            "payment_method": "cash", "reference": "CASH",
        }]

    def test_blank_amount_is_zero(self):
        buf = build_workbook([
            ("2025-08-03", "Achimota Kumasi", "GH-Express", "Credit Sale", None, None),
            ("2025-08-03", "Achimota Kumasi", "GH-Express", "Credit Sale", 37632.72, None),
        ])
        result = parse_summary_file(buf)
        b = result.blocks[0]
        assert b.credit_sales == [{"creditor": "GH-Express", "amount": "37632.72"}]
        assert any("blank amount" in w.lower() for w in result.warnings)

    def test_unresolved_creditor(self):
        buf = build_workbook([
            ("2025-08-04", "Achimota", "NONEXISTENT", "Credit Sale", 10, None),
        ])
        result = parse_summary_file(buf)
        assert not result.is_clean
        assert len(result.unresolved) == 1
        assert result.unresolved[0].name == "NONEXISTENT"

    def test_unknown_station_row_error(self):
        buf = build_workbook([
            ("2025-08-04", "Atlantis", "PAPA", "Credit Sale", 10, None),
        ])
        result = parse_summary_file(buf)
        assert len(result.row_errors) == 1
        assert "unknown station" in result.row_errors[0].reason

    def test_multi_site_multi_date_order(self):
        buf = build_workbook([
            ("2025-08-02", "Achimota", "PAPA", "Credit Sale", 100, None),
            ("2025-08-03", "Achimota", "PAPA", "Credit Sale", 100, None),
            ("2025-08-02", "Achimota Kumasi", "GH-Express", "Credit Sale", 100, None),
        ])
        result = parse_summary_file(buf)
        order = [(b.site, b.date.isoformat()) for b in result.blocks]
        assert order == [
            ("ACH", "2025-08-02"),
            ("AKS", "2025-08-02"),
            ("ACH", "2025-08-03"),
        ]