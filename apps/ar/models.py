"""
AR models — Accounts Receivable.

Mirrors the frontend interfaces in `ar-models.ts`.
"""

from django.db import models


class Site(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100, unique=True)
    has_creditors = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.code} — {self.name}"




import hashlib


def summary_upload_path(instance, filename):
    return f"ar/summary_uploads/{instance.sha256[:12]}/{filename}"


class SummaryUpload(models.Model):
    STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("parsed", "Parsed"),
        ("committed", "Committed"),
        ("failed", "Failed"),
    ]

    file = models.FileField(upload_to=summary_upload_path)
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, db_index=True, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100, blank=True)

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="uploaded"
    )

    uploaded_by = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    parse_error = models.TextField(blank=True)
    parse_warnings = models.JSONField(default=list, blank=True)
    parsed_payload = models.JSONField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.file and not self.sha256:
            self.sha256 = self._compute_sha256()
            self.size_bytes = self.file.size
        super().save(*args, **kwargs)

    def _compute_sha256(self):
        h = hashlib.sha256()
        for chunk in self.file.chunks():
            h.update(chunk)
        self.file.seek(0)
        return h.hexdigest()

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Summary Upload"
        verbose_name_plural = "Summary Uploads"

    def __str__(self):
        return f"{self.original_filename} ({self.uploaded_at:%Y-%m-%d %H:%M})"


class Creditor(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('blocked', 'Blocked'),
    ]

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='creditors')
    name = models.CharField(max_length=100)
    customer_group = models.CharField(max_length=100, blank=True, null=True)
    contact_info = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('site', 'name')]
        ordering = ['site', 'name']

    def __str__(self):
        return f"{self.name} ({self.site.code})"


class CreditorAlias(models.Model):
    creditor = models.ForeignKey(Creditor, on_delete=models.CASCADE, related_name='aliases')
    raw_name = models.CharField(max_length=100)

    class Meta:
        unique_together = [('creditor', 'raw_name')]

    def __str__(self):
        return f"{self.raw_name} → {self.creditor.name}"


class OpeningBalance(models.Model):
    creditor = models.ForeignKey(Creditor, on_delete=models.PROTECT, related_name='opening_balances')
    as_of_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    signed_off_by = models.CharField(max_length=150, blank=True)
    signed_off_at = models.DateTimeField(blank=True, null=True)
    locked = models.BooleanField(default=False)
    reason_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of_date']

    def __str__(self):
        return f"{self.creditor.name} @ {self.as_of_date}: {self.amount}"


class DailySummary(models.Model):
    STATUS_CHOICES = [
        ('committed', 'Committed'),
        ('voided', 'Voided'),
    ]

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='summaries')
    summary_date = models.DateField()
    sales_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    credit_sales_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    credit_collection_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='committed')
    committed_by = models.CharField(max_length=150, blank=True)
    committed_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(blank=True, null=True)
    voided_reason = models.TextField(blank=True)
    replaced_by = models.ForeignKey('self', on_delete=models.SET_NULL, blank=True, null=True, related_name='replaces')
    
    
    upload = models.ForeignKey(
        SummaryUpload,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="summaries",
        help_text="Source file for this summary. Null for manual entry.",
    )

    class Meta:
        unique_together = [('site', 'summary_date', 'status')]
        ordering = ['-summary_date', 'site']

    def __str__(self):
        return f"{self.site.code} — {self.summary_date}"


class CreditSale(models.Model):
    summary = models.ForeignKey(DailySummary, on_delete=models.CASCADE, related_name='credit_sales')
    creditor = models.ForeignKey(Creditor, on_delete=models.PROTECT, related_name='credit_sales')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_promo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['summary', 'creditor']

    def __str__(self):
        return f"{self.creditor.name}: {self.amount}"


class Collection(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('cheque', 'Cheque'),
        ('momo', 'Momo'),
        ('bank_transfer', 'Bank Transfer'),
        ('coupon', 'Coupon'),
        ("unspecified", "Unspecified"),
    ]

    summary = models.ForeignKey(DailySummary, on_delete=models.CASCADE, related_name='collections', null=True, blank=True)
    creditor = models.ForeignKey(Creditor, on_delete=models.PROTECT, related_name='collections')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="unspecified",
    )
    reference = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ['summary', 'creditor']

    def __str__(self):
        return f"{self.creditor.name}: {self.amount} ({self.payment_method})"


class Adjustment(models.Model):
    creditor = models.ForeignKey(Creditor, on_delete=models.PROTECT, related_name='adjustments')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason_code = models.CharField(max_length=50)
    note = models.TextField(blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.creditor.name}: {self.amount} ({self.reason_code})"


class LedgerEntry(models.Model):
    ENTRY_TYPE_CHOICES = [
        ('opening', 'Opening'),
        ('credit-sale', 'Credit Sale'),
        ('collection', 'Collection'),
        ('adjustment', 'Adjustment'),
    ]

    creditor = models.ForeignKey(Creditor, on_delete=models.CASCADE, related_name='ledger_entries')
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPE_CHOICES)
    entry_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    source_id = models.PositiveIntegerField(blank=True, null=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creditor', 'entry_date', 'id']

    def __str__(self):
        return f"{self.creditor.name} | {self.entry_type} | {self.amount} | {self.entry_date}"
    
    
    
    
