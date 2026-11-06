"""
AR serializers — shape models into the JSON structure the frontend expects.

The shapes here mirror `ar-models.ts` exactly:
- DashboardResponse
- CreditorsListResponse
- CreditorLedger
- SummaryPayload (commit)
- SummaryUploadPreview (upload → commit flow)
"""

from rest_framework import serializers

from .models import (
    Site,
    Creditor,
    OpeningBalance,
    DailySummary,
    CreditSale,
    Collection,
    Adjustment,
    LedgerEntry,
)


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------

class SiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Site
        fields = ["code", "name", "has_creditors"]


class CreditorSummarySerializer(serializers.ModelSerializer):
    site_code = serializers.CharField(source="site.code", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = Creditor
        fields = ["id", "site_code", "site_name", "name", "status"]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardKpisSerializer(serializers.Serializer):
    totalAr = serializers.DecimalField(max_digits=16, decimal_places=2)
    arMovement = serializers.DecimalField(max_digits=16, decimal_places=2)
    collectionRate = serializers.FloatField()
    topDebtorBalance = serializers.DecimalField(max_digits=16, decimal_places=2)
    topDebtorCreditorId = serializers.IntegerField(allow_null=True)
    topDebtorName = serializers.CharField(allow_null=True)
    topDebtorPercentOfTotal = serializers.FloatField()
    agingOver30 = serializers.DecimalField(max_digits=16, decimal_places=2)
    agingOver30Percent = serializers.FloatField()


class DailyTrendPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    creditSales = serializers.DecimalField(max_digits=16, decimal_places=2)
    collections = serializers.DecimalField(max_digits=16, decimal_places=2)
    cumulativeAr = serializers.DecimalField(max_digits=16, decimal_places=2)


class AgingBucketTotalSerializer(serializers.Serializer):
    bucket = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)
    creditorCount = serializers.IntegerField()
    percentOfTotal = serializers.FloatField()


class MethodMixSliceSerializer(serializers.Serializer):
    method = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)
    percentOfTotal = serializers.FloatField()


class SiteSummarySerializer(serializers.Serializer):
    siteCode = serializers.CharField()
    siteName = serializers.CharField()
    totalAr = serializers.DecimalField(max_digits=16, decimal_places=2)
    creditSalesPeriod = serializers.DecimalField(max_digits=16, decimal_places=2)
    collectionsPeriod = serializers.DecimalField(max_digits=16, decimal_places=2)
    collectionRate = serializers.FloatField()
    agingOver30 = serializers.DecimalField(max_digits=16, decimal_places=2)
    creditorCount = serializers.IntegerField()


class AlertSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    severity = serializers.CharField()
    siteCode = serializers.CharField(allow_null=True)
    creditorId = serializers.IntegerField(allow_null=True)
    creditorName = serializers.CharField(allow_null=True)
    message = serializers.CharField()
    value = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    createdAt = serializers.DateTimeField()


class DashboardFiltersSerializer(serializers.Serializer):
    siteCode = serializers.CharField(required=False, allow_null=True)
    from_date = serializers.DateField(required=False, allow_null=True)
    to_date = serializers.DateField(required=False, allow_null=True)


class DashboardResponseSerializer(serializers.Serializer):
    filters = DashboardFiltersSerializer()
    kpis = DashboardKpisSerializer()
    trend = DailyTrendPointSerializer(many=True)
    aging = AgingBucketTotalSerializer(many=True)
    methodMix = MethodMixSliceSerializer(many=True)
    siteSummaries = SiteSummarySerializer(many=True)
    alerts = AlertSerializer(many=True)


# ---------------------------------------------------------------------------
# Creditors list
# ---------------------------------------------------------------------------

class CreditorListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    siteCode = serializers.CharField()
    siteName = serializers.CharField()
    name = serializers.CharField()
    balance = serializers.DecimalField(max_digits=16, decimal_places=2)
    percentOfTotal = serializers.FloatField()
    agingBucket = serializers.CharField()
    lastCreditSaleDate = serializers.DateField(allow_null=True)
    lastCollectionDate = serializers.DateField(allow_null=True)
    daysSinceLastCollection = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()


class CreditorsListFiltersSerializer(serializers.Serializer):
    siteCode = serializers.CharField(required=False, allow_null=True)
    agingBucket = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_null=True)
    search = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class CreditorsListResponseSerializer(serializers.Serializer):
    filters = CreditorsListFiltersSerializer()
    items = CreditorListItemSerializer(many=True)
    totalBalance = serializers.DecimalField(max_digits=16, decimal_places=2)
    count = serializers.IntegerField()


# ---------------------------------------------------------------------------
# Creditor ledger
# ---------------------------------------------------------------------------

class LedgerEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField()
    date = serializers.DateField()
    description = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)
    runningBalance = serializers.DecimalField(max_digits=16, decimal_places=2)
    paymentMethod = serializers.CharField(allow_null=True)
    reference = serializers.CharField(allow_blank=True, allow_null=True)
    reason = serializers.CharField(allow_blank=True, allow_null=True)


class CreditorAgingRowSerializer(serializers.Serializer):
    bucket = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)


class CreditorMethodSliceSerializer(serializers.Serializer):
    method = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)


class CreditorLedgerSerializer(serializers.Serializer):
    creditor = CreditorSummarySerializer()
    balance = serializers.DecimalField(max_digits=16, decimal_places=2)
    aging = CreditorAgingRowSerializer(many=True)
    methodMix = CreditorMethodSliceSerializer(many=True)
    lastCreditSaleDate = serializers.DateField(allow_null=True)
    lastCollectionDate = serializers.DateField(allow_null=True)
    daysSinceLastCollection = serializers.IntegerField(allow_null=True)
    entries = LedgerEntrySerializer(many=True)


# ---------------------------------------------------------------------------
# Summary payload — building blocks (shared by upload-preview and commit)
# ---------------------------------------------------------------------------

PAYMENT_METHOD_CHOICES = [choice[0] for choice in Collection.PAYMENT_METHOD_CHOICES]


class CreditSaleRowSerializer(serializers.Serializer):
    """
    One credit-sale row. No is_promo in API — always False at the model layer.
    """
    creditor = serializers.CharField(max_length=255)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class CollectionRowSerializer(serializers.Serializer):
    """
    One collection row. payment_method is optional — defaults to 'unspecified'
    when the source (e.g. Excel Notes column) doesn't indicate one.
    """
    creditor = serializers.CharField(max_length=255)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    payment_method = serializers.ChoiceField(
        choices=PAYMENT_METHOD_CHOICES,
        required=False,
        default="unspecified",
    )
    reference = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )


class SummaryBlockSerializer(serializers.Serializer):
    """
    One (site, date) block. Used in the commit payload.
    Matches the parser's BlockPreview.to_dict() output exactly.
    """
    site = serializers.CharField(max_length=10)
    date = serializers.DateField()
    credit_sales = CreditSaleRowSerializer(many=True, default=list)
    collections = CollectionRowSerializer(many=True, default=list)

    def validate(self, data):
        if not data.get("credit_sales") and not data.get("collections"):
            raise serializers.ValidationError(
                "Block must contain at least one credit sale or collection."
            )
        return data


class SummaryCommitPayloadSerializer(serializers.Serializer):
    """
    Commit endpoint accepts a list of blocks (multi-(site,date) file commit).
    """
    blocks = SummaryBlockSerializer(many=True)


# ---------------------------------------------------------------------------
# Upload preview response (what /summaries/upload/ returns)
# ---------------------------------------------------------------------------

class UnresolvedCreditorSerializer(serializers.Serializer):
    site = serializers.CharField()
    name = serializers.CharField()
    occurrences = serializers.IntegerField()
    section = serializers.CharField()


class RowErrorSerializer(serializers.Serializer):
    row = serializers.IntegerField()
    reason = serializers.CharField()
    raw = serializers.DictField(required=False)


class SummaryUploadPreviewSerializer(serializers.Serializer):
    """
    Preview returned from /summaries/upload/.
    The frontend shows this, then posts `blocks` back to /summaries/.
    """
    blocks = SummaryBlockSerializer(many=True)
    unresolved = UnresolvedCreditorSerializer(many=True)
    row_errors = RowErrorSerializer(many=True)
    warnings = serializers.ListField(child=serializers.CharField())
    is_clean = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Commit result (what /summaries/ returns on success)
# ---------------------------------------------------------------------------

class CommittedBlockSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    site = serializers.CharField()
    date = serializers.DateField()
    credit_sales_count = serializers.IntegerField()
    collections_count = serializers.IntegerField()


class SummaryCommitResultSerializer(serializers.Serializer):
    upload_id = serializers.IntegerField(allow_null=True)
    blocks = CommittedBlockSerializer(many=True)