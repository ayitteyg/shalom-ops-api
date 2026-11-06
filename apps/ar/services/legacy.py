"""
AR business logic — computes aggregates from raw models.

Views call these functions. Nothing here depends on Django REST Framework.

Every function returns plain Python structures (dicts, Decimals, dates)
that serializers then shape into JSON.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum, Q, Min, Max
from django.utils import timezone

from ..models import (
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
# Helpers
# ---------------------------------------------------------------------------

AGING_BUCKETS = ["pre-go-live", "0-7", "8-14", "15-30", "31-60", "60+"]


def _bucket_for_days(days: int) -> str:
    if days <= 7:
        return "0-7"
    if days <= 14:
        return "8-14"
    if days <= 30:
        return "15-30"
    if days <= 60:
        return "31-60"
    return "60+"


def _creditor_ledger_entries(creditor: Creditor) -> list[dict]:
    """
    Build the full event timeline for one creditor:
    opening balances + credit sales (non-promo) + collections + adjustments.
    """
    entries = []

    for ob in creditor.opening_balances.all():
        entries.append({
            "type": "opening",
            "date": ob.as_of_date,
            "amount": ob.amount,
            "description": "Opening balance",
            "paymentMethod": None,
            "reference": None,
            "reason": None,
        })

    for cs in creditor.credit_sales.filter(is_promo=False).select_related("summary"):
        entries.append({
            "type": "credit-sale",
            "date": cs.summary.summary_date,
            "amount": cs.amount,
            "description": "Credit sale",
            "paymentMethod": None,
            "reference": None,
            "reason": None,
        })

    for col in creditor.collections.select_related("summary"):
        entries.append({
            "type": "collection",
            "date": col.summary.summary_date if col.summary else col.created_at.date(),
            "amount": -col.amount,
            "description": f"Collection — {col.payment_method}",
            "paymentMethod": col.payment_method,
            "reference": col.reference,
            "reason": None,
        })

    for adj in creditor.adjustments.all():
        entries.append({
            "type": "adjustment",
            "date": adj.created_at.date(),
            "amount": adj.amount,
            "description": f"Adjustment — {adj.reason_code}",
            "paymentMethod": None,
            "reference": None,
            "reason": adj.note,
        })

    entries.sort(key=lambda e: (e["date"], e["type"]))
    return entries


def _creditor_balance(creditor: Creditor) -> Decimal:
    """Current balance = sum of all signed events."""
    total = Decimal("0.00")

    for ob in creditor.opening_balances.all():
        total += ob.amount
    for cs in creditor.credit_sales.filter(is_promo=False):
        total += cs.amount
    for col in creditor.collections.all():
        total -= col.amount
    for adj in creditor.adjustments.all():
        total += adj.amount

    return total.quantize(Decimal("0.01"))


def _creditor_aging(creditor: Creditor) -> dict[str, Decimal]:
    buckets = {b: Decimal("0.00") for b in AGING_BUCKETS}

    # Opening balances → pre-go-live
    opening_total = Decimal("0.00")
    for ob in creditor.opening_balances.all():
        opening_total += ob.amount

    # Credit sales with aging bucket, oldest first
    credits = []
    for cs in creditor.credit_sales.filter(is_promo=False).select_related("summary"):
        days = (date.today() - cs.summary.summary_date).days
        bucket = _bucket_for_days(days)
        credits.append((cs.summary.summary_date, cs.amount, bucket))
    credits.sort(key=lambda x: x[0])

    # Total collected
    total_collected = creditor.collections.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")

    # Apply collections FIFO: opening first, then credit sales oldest-first
    collected = total_collected
    if collected >= opening_total:
        collected -= opening_total
        buckets["pre-go-live"] = Decimal("0.00")
    else:
        buckets["pre-go-live"] = opening_total - collected
        collected = Decimal("0.00")

    for _, amount, bucket in credits:
        if collected <= 0:
            buckets[bucket] += amount
        elif collected >= amount:
            collected -= amount
        else:
            buckets[bucket] += amount - collected
            collected = Decimal("0.00")

    return buckets


def _last_credit_sale_date(creditor: Creditor) -> date | None:
    result = (
        creditor.credit_sales
        .filter(is_promo=False)
        .aggregate(m=Max("summary__summary_date"))
    )
    return result["m"]


def _last_collection_date(creditor: Creditor) -> date | None:
    result = (
        creditor.collections
        .aggregate(m=Max("summary__summary_date"))
    )
    return result["m"]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def _ar_balance_at(site_code: str | None, as_of: date) -> Decimal:
    """Sum of all signed events on or before `as_of`: opening + sales − collections."""
    from apps.ar.models import OpeningBalance

    cs = CreditSale.objects.filter(
        summary__summary_date__lte=as_of,
        summary__status="committed",
        is_promo=False,
    )
    col = Collection.objects.filter(
        summary__summary_date__lte=as_of,
        summary__status="committed",
    )
    ob = OpeningBalance.objects.filter(as_of_date__lte=as_of)

    if site_code:
        cs = cs.filter(summary__site__code=site_code)
        col = col.filter(summary__site__code=site_code)
        ob = ob.filter(creditor__site__code=site_code)

    cs_total = cs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    col_total = col.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    ob_total = ob.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")

    return ob_total + cs_total - col_total


def compute_dashboard(*, site_code: str | None, from_date: date, to_date: date) -> dict:
    creditors_qs = Creditor.objects.all()
    if site_code:
        creditors_qs = creditors_qs.filter(site__code=site_code)

    creditors = list(creditors_qs.select_related("site"))
    
    
    total_ar = _ar_balance_at(site_code, to_date).quantize(Decimal("0.01"))

    # Total AR
    # total_ar = Decimal("0.00")
    # for c in creditors:
    #     total_ar += _creditor_balance(c)
    # total_ar = total_ar.quantize(Decimal("0.01"))

    # Aging across all creditors in scope
    aging_totals = {b: Decimal("0.00") for b in AGING_BUCKETS}
    creditor_aging_map = {}
    for c in creditors:
        a = _creditor_aging(c)
        creditor_aging_map[c.id] = a
        for b in AGING_BUCKETS:
            aging_totals[b] += a[b]

    over_30 = aging_totals["31-60"] + aging_totals["60+"]

    aging_list = []
    for b in AGING_BUCKETS:
        amt = aging_totals[b]
        aging_list.append({
            "bucket": b,
            "amount": amt.quantize(Decimal("0.01")),
            "creditorCount": sum(1 for c in creditors if creditor_aging_map[c.id][b] > 0),
            "percentOfTotal": float(amt / total_ar) if total_ar > 0 else 0.0,
        })

    # Trend — daily credit sales & collections in range
    trend = []
    running = Decimal("0.00")
    day = from_date
    while day <= to_date:
        cs_qs = CreditSale.objects.filter(
            summary__summary_date=day,
            summary__status="committed",
            is_promo=False,
        )
        col_qs = Collection.objects.filter(
            summary__summary_date=day,
            summary__status="committed",
        )
        if site_code:
            cs_qs = cs_qs.filter(summary__site__code=site_code)
            col_qs = col_qs.filter(summary__site__code=site_code)

        cs_total = cs_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        col_total = col_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        running += cs_total - col_total
        trend.append({
            "date": day,
            "creditSales": cs_total.quantize(Decimal("0.01")),
            "collections": col_total.quantize(Decimal("0.01")),
            "cumulativeAr": running.quantize(Decimal("0.01")),
        })
        day += timedelta(days=1)

    # AR movement = change in actual AR balance over the period
    closing_ar = _ar_balance_at(site_code, to_date)
    opening_ar = _ar_balance_at(site_code, from_date - timedelta(days=1))
    ar_movement = (closing_ar - opening_ar).quantize(Decimal("0.01"))

    period_cs = sum((t["creditSales"] for t in trend), Decimal("0.00"))
    period_col = sum((t["collections"] for t in trend), Decimal("0.00"))
    collection_rate = float(period_col / period_cs) if period_cs > 0 else 0.0

    # Top debtor
    ranked = sorted(
        [(c, _creditor_balance(c)) for c in creditors],
        key=lambda x: x[1],
        reverse=True,
    )
    top = ranked[0] if ranked else None

    # Method mix
    method_totals = {}
    for col in Collection.objects.filter(summary__status="committed"):
        if site_code and col.summary.site.code != site_code:
            continue
        method_totals[col.payment_method] = (
            method_totals.get(col.payment_method, Decimal("0.00")) + col.amount
        )
    mix_total = sum(method_totals.values()) or Decimal("0.00")
    method_mix = [
        {
            "method": m,
            "amount": v.quantize(Decimal("0.01")),
            "percentOfTotal": float(v / mix_total) if mix_total > 0 else 0.0,
        }
        for m, v in method_totals.items()
        if v > 0
    ]

    # Site summaries (always unfiltered — each row is a per-site breakdown)
    site_summaries = []
    for site in Site.objects.filter(has_creditors=True):
        site_creds = list(Creditor.objects.filter(site=site).select_related("site"))
        site_ar = sum((_creditor_balance(c) for c in site_creds), Decimal("0.00"))
        site_cs = (
            CreditSale.objects
            .filter(
                summary__site=site,
                summary__summary_date__range=(from_date, to_date),
                summary__status="committed",
                is_promo=False,
            )
            .aggregate(s=Sum("amount"))["s"]
            or Decimal("0.00")
        )
        site_col = (
            Collection.objects
            .filter(
                summary__site=site,
                summary__summary_date__range=(from_date, to_date),
                summary__status="committed",
            )
            .aggregate(s=Sum("amount"))["s"]
            or Decimal("0.00")
        )
        site_aging = {b: Decimal("0.00") for b in AGING_BUCKETS}
        for c in site_creds:
            a = _creditor_aging(c)
            for b in AGING_BUCKETS:
                site_aging[b] += a[b]
        site_over30 = site_aging["31-60"] + site_aging["60+"]

        site_summaries.append({
            "siteCode": site.code,
            "siteName": site.name,
            "totalAr": site_ar.quantize(Decimal("0.01")),
            "creditSalesPeriod": site_cs.quantize(Decimal("0.01")),
            "collectionsPeriod": site_col.quantize(Decimal("0.01")),
            "collectionRate": float(site_col / site_cs) if site_cs > 0 else 0.0,
            "agingOver30": site_over30.quantize(Decimal("0.01")),
            "creditorCount": len(site_creds),
        })

    # Alerts
    alerts = []
    for c in creditors:
        bal = _creditor_balance(c)
        if bal < 0:
            alerts.append({
                "id": f"neg-{c.id}",
                "type": "negative-balance",
                "severity": "warning",
                "siteCode": c.site.code,
                "creditorId": c.id,
                "creditorName": c.name,
                "message": f"{c.name} has a negative balance.",
                "value": bal,
                "createdAt": timezone.now(),
            })
        if total_ar > 0 and bal / total_ar > 0.4:
            alerts.append({
                "id": f"conc-{c.id}",
                "type": "concentration",
                "severity": "critical",
                "siteCode": c.site.code,
                "creditorId": c.id,
                "creditorName": c.name,
                "message": f"{c.name} is {int((bal/total_ar)*100)}% of AR.",
                "value": bal,
                "createdAt": timezone.now(),
            })

    return {
        "filters": {"siteCode": site_code, "from_date": from_date, "to_date": to_date},
        "kpis": {
            "totalAr": total_ar,
            "arMovement": ar_movement,
            "collectionRate": collection_rate,
            "topDebtorBalance": top[1].quantize(Decimal("0.01")) if top else Decimal("0.00"),
            "topDebtorCreditorId": top[0].id if top else None,
            "topDebtorName": top[0].name if top else None,
            "topDebtorPercentOfTotal": float(top[1] / total_ar) if top and total_ar > 0 else 0.0,
            "agingOver30": over_30.quantize(Decimal("0.01")),
            "agingOver30Percent": float(over_30 / total_ar) if total_ar > 0 else 0.0,
        },
        "trend": trend,
        "aging": aging_list,
        "methodMix": method_mix,
        "siteSummaries": site_summaries,
        "alerts": alerts,
    }


def compute_dashboard(*, site_code: str | None, from_date: date, to_date: date) -> dict:
    creditors_qs = Creditor.objects.all()
    if site_code:
        creditors_qs = creditors_qs.filter(site__code=site_code)

    creditors = list(creditors_qs.select_related("site"))

    # Total AR
    total_ar = Decimal("0.00")
    for c in creditors:
        total_ar += _creditor_balance(c)
    total_ar = total_ar.quantize(Decimal("0.01"))

    # Aging across all creditors
    aging_totals = {b: Decimal("0.00") for b in AGING_BUCKETS}
    creditor_aging_map = {}
    for c in creditors:
        a = _creditor_aging(c)
        creditor_aging_map[c.id] = a
        for b in AGING_BUCKETS:
            aging_totals[b] += a[b]

    over_30 = aging_totals["31-60"] + aging_totals["60+"]

    aging_list = []
    for b in AGING_BUCKETS:
        amt = aging_totals[b]
        aging_list.append({
            "bucket": b,
            "amount": amt.quantize(Decimal("0.01")),
            "creditorCount": sum(1 for c in creditors if creditor_aging_map[c.id][b] > 0),
            "percentOfTotal": float(amt / total_ar) if total_ar > 0 else 0.0,
        })

    # Trend — daily credit sales & collections in range
    trend = []
    running = Decimal("0.00")
    day = from_date
    while day <= to_date:
        cs_qs = CreditSale.objects.filter(
        summary__summary_date=day,
        summary__status="committed",
        is_promo=False,
    )
        col_qs = Collection.objects.filter(
            summary__summary_date=day,
            summary__status="committed",
        )
        if site_code:
            cs_qs = cs_qs.filter(summary__site__code=site_code)
            col_qs = col_qs.filter(summary__site__code=site_code)

        cs_total = cs_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        col_total = col_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        running += cs_total - col_total
        trend.append({
            "date": day,
            "creditSales": cs_total.quantize(Decimal("0.01")),
            "collections": col_total.quantize(Decimal("0.01")),
            "cumulativeAr": running.quantize(Decimal("0.01")),
        })
        day += timedelta(days=1)

    # ar_movement = trend[-1]["cumulativeAr"] - trend[0]["cumulativeAr"] if trend else Decimal("0.00")
    
    closing_ar = _ar_balance_at(site_code, to_date)
    opening_ar = _ar_balance_at(site_code, from_date - timedelta(days=1))
    ar_movement = (closing_ar - opening_ar).quantize(Decimal("0.01"))

    period_cs = sum((t["creditSales"] for t in trend), Decimal("0.00"))
    period_col = sum((t["collections"] for t in trend), Decimal("0.00"))
    collection_rate = float(period_col / period_cs) if period_cs > 0 else 0.0

    # Top debtor
    ranked = sorted(
        [(c, _creditor_balance(c)) for c in creditors],
        key=lambda x: x[1],
        reverse=True,
    )
    top = ranked[0] if ranked else None

    # Method mix
    method_totals = {}
    for col in Collection.objects.filter(summary__status="committed"):
        if site_code and col.summary.site.code != site_code:
            continue
        method_totals[col.payment_method] = method_totals.get(col.payment_method, Decimal("0.00")) + col.amount
    mix_total = sum(method_totals.values()) or Decimal("0.00")
    method_mix = [
        {
            "method": m,
            "amount": v.quantize(Decimal("0.01")),
            "percentOfTotal": float(v / mix_total) if mix_total > 0 else 0.0,
        }
        for m, v in method_totals.items()
        if v > 0
    ]

    # Site summaries
    site_summaries = []
    for site in Site.objects.filter(has_creditors=True):
        site_creds = list(Creditor.objects.filter(site=site).select_related("site"))
        # site_ar = sum((_creditor_balance(c) for c in site_creds), Decimal("0.00"))
        site_ar = _ar_balance_at(site.code, to_date)
        site_cs = (
            CreditSale.objects
            .filter(summary__site=site, summary__summary_date__range=(from_date, to_date),
                    summary__status="committed", is_promo=False)
            .aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        )
        site_col = (
            Collection.objects
            .filter(summary__site=site, summary__summary_date__range=(from_date, to_date),
                    summary__status="committed")
            .aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        )
        site_aging = {b: Decimal("0.00") for b in AGING_BUCKETS}
        for c in site_creds:
            # a = creditor_aging_map.get(c.id, {b: Decimal("0.00") for b in AGING_BUCKETS})
            a = _creditor_aging(c) 
            for b in AGING_BUCKETS:
                site_aging[b] += a[b]
        site_over30 = site_aging["31-60"] + site_aging["60+"]

        site_summaries.append({
            "siteCode": site.code,
            "siteName": site.name,
            "totalAr": site_ar.quantize(Decimal("0.01")),
            "creditSalesPeriod": site_cs.quantize(Decimal("0.01")),
            "collectionsPeriod": site_col.quantize(Decimal("0.01")),
            "collectionRate": float(site_col / site_cs) if site_cs > 0 else 0.0,
            "agingOver30": site_over30.quantize(Decimal("0.01")),
            "creditorCount": len(site_creds),
        })

    # Alerts (simple examples — refine later)
    alerts = []
    for c in creditors:
        bal = _creditor_balance(c)
        if bal < 0:
            alerts.append({
                "id": f"neg-{c.id}",
                "type": "negative-balance",
                "severity": "warning",
                "siteCode": c.site.code,
                "creditorId": c.id,
                "creditorName": c.name,
                "message": f"{c.name} has a negative balance.",
                "value": bal,
                "createdAt": timezone.now(),
            })
        if total_ar > 0 and bal / total_ar > 0.4:
            alerts.append({
                "id": f"conc-{c.id}",
                "type": "concentration",
                "severity": "critical",
                "siteCode": c.site.code,
                "creditorId": c.id,
                "creditorName": c.name,
                "message": f"{c.name} is {int((bal/total_ar)*100)}% of AR.",
                "value": bal,
                "createdAt": timezone.now(),
            })

    return {
        "filters": {"siteCode": site_code, "from_date": from_date, "to_date": to_date},
        "kpis": {
            "totalAr": total_ar,
            "arMovement": ar_movement.quantize(Decimal("0.01")),
            "collectionRate": collection_rate,
            "topDebtorBalance": top[1].quantize(Decimal("0.01")) if top else Decimal("0.00"),
            "topDebtorCreditorId": top[0].id if top else None,
            "topDebtorName": top[0].name if top else None,
            "topDebtorPercentOfTotal": float(top[1] / total_ar) if top and total_ar > 0 else 0.0,
            "agingOver30": over_30.quantize(Decimal("0.01")),
            "agingOver30Percent": float(over_30 / total_ar) if total_ar > 0 else 0.0,
        },
        "trend": trend,
        "aging": aging_list,
        "methodMix": method_mix,
        "siteSummaries": site_summaries,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Creditors list
# ---------------------------------------------------------------------------

def compute_creditors_list(*, site_code=None, aging_bucket=None, status=None, search=None) -> dict:
    creditors = Creditor.objects.all().select_related("site")
    if site_code:
        creditors = creditors.filter(site__code=site_code)

    items = []
    for c in creditors:
        bal = _creditor_balance(c)
        aging = _creditor_aging(c)
        dominant = max(aging, key=lambda b: aging[b]) if any(v > 0 for v in aging.values()) else "0-7"

        last_cs = _last_credit_sale_date(c)
        last_col = _last_collection_date(c)
        days_since = (date.today() - last_col).days if last_col else None

        if dominant == "60+" or bal < 0:
            c_status = "at-risk"
        elif dominant == "31-60" or (days_since is not None and days_since > 30):
            c_status = "watch"
        else:
            c_status = "normal"

        items.append({
            "id": c.id,
            "siteCode": c.site.code,
            "siteName": c.site.name,
            "name": c.name,
            "balance": bal,
            "percentOfTotal": 0.0,
            "agingBucket": dominant,
            "lastCreditSaleDate": last_cs,
            "lastCollectionDate": last_col,
            "daysSinceLastCollection": days_since,
            "status": c_status,
        })

    total = sum(i["balance"] for i in items) or Decimal("0.00")
    for i in items:
        i["percentOfTotal"] = float(i["balance"] / total) if total > 0 else 0.0

    if aging_bucket:
        items = [i for i in items if i["agingBucket"] == aging_bucket]
    if status:
        items = [i for i in items if i["status"] == status]
    if search:
        q = search.lower()
        items = [i for i in items if q in i["name"].lower()]

    items.sort(key=lambda x: x["balance"], reverse=True)

    return {
        "filters": {
            "siteCode": site_code,
            "agingBucket": aging_bucket,
            "status": status,
            "search": search,
        },
        "items": items,
        "totalBalance": sum((i["balance"] for i in items), Decimal("0.00")).quantize(Decimal("0.01")),
        "count": len(items),
    }


# ---------------------------------------------------------------------------
# Creditor ledger
# ---------------------------------------------------------------------------

def compute_creditor_ledger(creditor_id: int) -> dict | None:
    try:
        c = Creditor.objects.select_related("site").get(id=creditor_id)
    except Creditor.DoesNotExist:
        return None

    raw = _creditor_ledger_entries(c)
    running = Decimal("0.00")
    entries = []
    for i, e in enumerate(raw):
        running += e["amount"]
        entries.append({
            "id": i + 1,
            "type": e["type"],
            "date": e["date"],
            "description": e["description"],
            "amount": e["amount"].quantize(Decimal("0.01")),
            "runningBalance": running.quantize(Decimal("0.01")),
            "paymentMethod": e["paymentMethod"],
            "reference": e["reference"],
            "reason": e["reason"],
        })

    aging = _creditor_aging(c)
    aging_rows = [{"bucket": b, "amount": aging[b].quantize(Decimal("0.01"))} for b in AGING_BUCKETS if aging[b] > 0]

    method_totals = {}
    for col in c.collections.all():
        method_totals[col.payment_method] = method_totals.get(col.payment_method, Decimal("0.00")) + col.amount
    method_mix = [{"method": m, "amount": v.quantize(Decimal("0.01"))} for m, v in method_totals.items() if v > 0]

    last_cs = _last_credit_sale_date(c)
    last_col = _last_collection_date(c)
    days_since = (date.today() - last_col).days if last_col else None

    return {
        "creditor": {
            "id": c.id,
            "site_code": c.site.code,
            "site_name": c.site.name,
            "name": c.name,
            "status": c.status,
        },
        "balance": _creditor_balance(c),
        "aging": aging_rows,
        "methodMix": method_mix,
        "lastCreditSaleDate": last_cs,
        "lastCollectionDate": last_col,
        "daysSinceLastCollection": days_since,
        "entries": entries,
    }