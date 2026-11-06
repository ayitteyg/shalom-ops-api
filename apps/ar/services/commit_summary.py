"""
Commit service for AR daily summaries.

Takes a list of SummaryBlock payloads (already parsed and previewed),
validates them all, then commits them atomically.

Rules:
  - Every creditor must exist for its site (exact, then iexact). No aliases.
  - If any (site, date) block has a committed DailySummary already → reject whole batch.
  - All-or-nothing: single transaction.
  - Attaches `upload` FK if a SummaryUpload is provided (provenance).
  - CreditSale.is_promo always False (no promo in current data).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.ar.models import (
    Site,
    Creditor,
    DailySummary,
    CreditSale,
    Collection,
    SummaryUpload,
)


ZERO = Decimal("0.00")


class CommitError(Exception):
    """Raised when a block cannot be committed. Carries structured detail."""
    def __init__(self, detail: dict[str, Any]):
        super().__init__(detail.get("detail", "commit failed"))
        self.detail = detail


def _resolve_creditor(site: Site, raw_name: str) -> Creditor | None:
    name = raw_name.strip()
    if not name:
        return None
    c = Creditor.objects.filter(site=site, name=name).first()
    if c:
        return c
    return Creditor.objects.filter(site=site, name__iexact=name).first()


def commit_blocks(
    blocks: list[dict],
    *,
    committed_by: str,
    upload: SummaryUpload | None = None,
) -> list[dict]:
    """
    Commit a list of blocks in one transaction.

    Each block: {
        "site": "ACH",
        "date": "2025-07-30" | date,
        "credit_sales": [{"creditor": "...", "amount": "..."}],
        "collections": [{"creditor": "...", "amount": "...",
                         "payment_method": "cheque", "reference": "..."}],
    }

    Returns list of committed block results.
    Raises CommitError on any problem — nothing is written.
    """
    if not blocks:
        raise CommitError({"detail": "No blocks to commit."})

    # ------------------------------------------------------------------
    # Pre-flight: resolve sites + creditors + check for conflicts.
    # Nothing is written until every block passes.
    # ------------------------------------------------------------------
    prepared: list[dict] = []
    unresolved: list[dict] = []
    conflicts: list[dict] = []

    for block in blocks:
        site_code = block["site"]
        block_date = block["date"]

        site = Site.objects.filter(code=site_code).first()
        if site is None:
            unresolved.append({
                "section": "site", "site": site_code,
                "name": site_code, "reason": "site not found",
            })
            continue

        # duplicate guard
        if DailySummary.objects.filter(
            site=site, summary_date=block_date, status="committed"
        ).exists():
            conflicts.append({
                "site": site_code, "date": str(block_date),
                "reason": "already committed",
            })
            continue

        # resolve creditors
        cs_resolved = []
        for row in block.get("credit_sales", []):
            creditor = _resolve_creditor(site, row["creditor"])
            if creditor is None:
                unresolved.append({
                    "section": "credit_sale", "site": site_code,
                    "name": row["creditor"],
                    "amount": str(row["amount"]),
                })
            else:
                cs_resolved.append((creditor, Decimal(str(row["amount"]))))

        col_resolved = []
        for row in block.get("collections", []):
            creditor = _resolve_creditor(site, row["creditor"])
            if creditor is None:
                unresolved.append({
                    "section": "collection", "site": site_code,
                    "name": row["creditor"],
                    "amount": str(row["amount"]),
                })
            else:
                col_resolved.append({
                    "creditor": creditor,
                    "amount": Decimal(str(row["amount"])),
                    "payment_method": row.get("payment_method", "unspecified"),
                    "reference": row.get("reference", "") or "",
                })

        prepared.append({
            "site": site,
            "date": block_date,
            "credit_sales": cs_resolved,
            "collections": col_resolved,
        })

    if unresolved or conflicts:
        raise CommitError({
            "detail": "Commit rejected.",
            "unresolved": unresolved,
            "conflicts": conflicts,
        })

    # ------------------------------------------------------------------
    # Write everything in one transaction
    # ------------------------------------------------------------------
    results: list[dict] = []
    with transaction.atomic():
        for p in prepared:
            cs_total = sum((amt for _, amt in p["credit_sales"]), ZERO)
            col_total = sum((r["amount"] for r in p["collections"]), ZERO)

            summary = DailySummary.objects.create(
                site=p["site"],
                summary_date=p["date"],
                credit_sales_total=cs_total,
                credit_collection_total=col_total,
                status="committed",
                committed_by=committed_by,
                upload=upload,
            )

            for creditor, amt in p["credit_sales"]:
                CreditSale.objects.create(
                    summary=summary,
                    creditor=creditor,
                    amount=amt,
                    is_promo=False,
                )

            for r in p["collections"]:
                Collection.objects.create(
                    summary=summary,
                    creditor=r["creditor"],
                    amount=r["amount"],
                    payment_method=r["payment_method"],
                    reference=r["reference"],
                )

            results.append({
                "id": summary.id,
                "site": p["site"].code,
                "date": p["date"],
                "credit_sales_count": len(p["credit_sales"]),
                "collections_count": len(p["collections"]),
            })

    if upload is not None:
        upload.status = "committed"
        upload.save(update_fields=["status"])

    return results