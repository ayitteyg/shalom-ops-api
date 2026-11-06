"""
Excel import service for AR daily summaries.

Converts an accountant's downloaded Excel file into a structured,
site-partitioned payload that the commit service can validate and persist.

Responsibilities (in order):
  1. Read workbook with openpyxl (no pandas — fewer deps, deterministic).
  2. Validate headers against expected schema.
  3. Iterate rows:
       - Normalize station → site code
       - Normalize creditor name (strip, collapse internal ws)
       - Parse date (handles datetime objects from Excel)
       - Parse amount as Decimal (blank → 0.00)
       - Classify Type: Credit Sale | Payment
  4. Group by (site, date, type) and sum duplicates per creditor.
  5. Resolve every (site, creditor) against the DB.
       - Unknown creditors are collected, NOT raised — the view decides.
  6. Return a ParsedResult dataclass.

This module is pure: no DB writes, no HTTP, no request objects.
The commit path lives in commit_summary.py.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook

from apps.ar.models import Creditor, Site


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map the accountant's station labels → internal site codes.
# Extend if new stations are added to the template.
STATION_TO_CODE = {
    "achimota": "ACH",
    "achimota kumasi": "AKS",
    "achimota ksi": "AKS",
    "shalom 2": "AKS",
    "east legon": "ELG",
    "palmwine": "PWJ",
    "palmwine junction": "PWJ",
    "ofankor": "OFK",
    "bohye": "BOH",
}

# Recognized Type values (case-insensitive, trimmed).
TYPE_CREDIT_SALE = "credit sale"
TYPE_PAYMENT = "payment"

# Keywords to promote from Notes → payment_method.
# Order matters: more specific first.
PAYMENT_METHOD_KEYWORDS = [
    (re.compile(r"\bchq?s?\b|cheque|check", re.I), "cheque"),
    (re.compile(r"\bcash\b", re.I), "cash"),
    (re.compile(r"\bcoupon\b", re.I), "coupon"),
    (re.compile(r"\bmomo\b|mobile\s*money", re.I), "mobile_money"),
    (re.compile(r"\bsc\b|smart\s*card|s/card", re.I), "smart_card"),
    (re.compile(r"\bbank\b|transfer", re.I), "bank_transfer"),
]

# Expected column headers (normalized: lower, stripped, non-alpha collapsed).
EXPECTED_HEADERS = {
    "date": "date",
    "station": "station",
    "creditor": "creditor",
    "type": "type",
    "amount(₵)": "amount",
    "amount": "amount",  # tolerate the ₵ symbol being dropped
    "notes": "notes",
}

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class BlockPreview:
    """One (site, date) block in the payload, ready for commit."""
    site: str
    date: date
    credit_sales: list[dict[str, Any]] = field(default_factory=list)
    collections: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RowError:
    """A row that could not be parsed at all (bad date, bad amount, etc.)."""
    row_number: int
    reason: str
    raw: dict[str, Any]


@dataclass
class UnresolvedCreditor:
    """A creditor name in the file that doesn't exist in the DB for its site."""
    site: str
    name: str
    occurrences: int
    section: str  # "credit_sale" | "collection"


@dataclass
class ParsedResult:
    blocks: list[BlockPreview] = field(default_factory=list)
    unresolved: list[UnresolvedCreditor] = field(default_factory=list)
    row_errors: list[RowError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.unresolved and not self.row_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "site": b.site,
                    "date": b.date.isoformat(),
                    "credit_sales": b.credit_sales,
                    "collections": b.collections,
                }
                for b in self.blocks
            ],
            "unresolved": [
                {
                    "site": u.site,
                    "name": u.name,
                    "occurrences": u.occurrences,
                    "section": u.section,
                }
                for u in self.unresolved
            ],
            "row_errors": [
                {"row": e.row_number, "reason": e.reason, "raw": e.raw}
                for e in self.row_errors
            ],
            "warnings": self.warnings,
            "is_clean": self.is_clean,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def clean_name(raw: str | None) -> str:
    """Trim outer whitespace, collapse internal runs, keep original case."""
    if raw is None:
        return ""
    return _WS_RE.sub(" ", str(raw)).strip()


def parse_amount(raw: Any) -> tuple[Decimal, bool]:
    """
    Return (amount, was_blank). Blank → (0.00, True). Invalid → raise.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return ZERO, True
    if isinstance(raw, (int, float)):
        # openpyxl yields floats for numeric cells; convert via str to avoid drift
        return Decimal(str(raw)).quantize(Decimal("0.01")), False
    s = str(raw).strip().replace(",", "")
    try:
        return Decimal(s).quantize(Decimal("0.01")), False
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount {raw!r}") from exc


def parse_date(raw: Any) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        # Accept ISO and a couple of common accountant formats
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"invalid date {raw!r}")


def classify_type(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s == TYPE_CREDIT_SALE:
        return TYPE_CREDIT_SALE
    if s == TYPE_PAYMENT:
        return TYPE_PAYMENT
    return None


def extract_payment_method(notes: str) -> str:
    """Return a controlled method name, or 'unspecified'."""
    if not notes:
        return "unspecified"
    for pattern, method in PAYMENT_METHOD_KEYWORDS:
        if pattern.search(notes):
            return method
    return "unspecified"


def normalize_header(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    s = re.sub(r"\s+", "", s)
    for expected, canonical in EXPECTED_HEADERS.items():
        if s == re.sub(r"\s+", "", expected):
            return canonical
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_summary_file(file_obj) -> ParsedResult:
    """
    Parse an uploaded Excel file and return a ParsedResult.

    Never raises for row-level problems — those go into row_errors.
    Raises ValueError only for structural problems (missing header, empty sheet).
    """
    result = ParsedResult()

    wb = load_workbook(file_obj, data_only=True, read_only=True)
    ws = wb.active
    if ws is None or ws.max_row < 2:
        raise ValueError("Workbook is empty or has no data rows.")

    # --- header row ---
    header_cells = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    col_index: dict[str, int] = {}
    for idx, raw in enumerate(header_cells):
        canonical = normalize_header(raw)
        if canonical:
            col_index[canonical] = idx

    required = {"date", "station", "creditor", "type", "amount"}
    missing = required - col_index.keys()
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Found headers: {header_cells}"
        )

    # --- group rows into (site, date) blocks ---
    # Keyed by (site, date); values are dicts creditor→Decimal for each section.
    credit_map: dict[tuple[str, date], dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    collection_map: dict[tuple[str, date], dict[str, dict]] = defaultdict(dict)

    # Creditor name resolution cache per (site) — but we resolve against DB later.
    seen_creditor_names: dict[tuple[str, str], int] = defaultdict(int)

    for row_number, row in enumerate(
        ws.iter_rows(min_row=2, values_only=True), start=2
    ):
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue  # skip fully empty rows

        def cell(key: str) -> Any:
            idx = col_index.get(key)
            return row[idx] if idx is not None and idx < len(row) else None

        try:
            row_date = parse_date(cell("date"))
            station_raw = clean_name(cell("station"))
            creditor_raw = clean_name(cell("creditor"))
            type_raw = classify_type(cell("type"))
            amount, was_blank = parse_amount(cell("amount"))
            notes = clean_name(cell("notes")) if "notes" in col_index else ""
        except ValueError as exc:
            result.row_errors.append(RowError(
                row_number=row_number,
                reason=str(exc),
                raw=dict(zip(col_index.keys(), row)),
            ))
            continue

        if not station_raw:
            result.row_errors.append(RowError(
                row_number=row_number, reason="missing station",
                raw=dict(zip(col_index.keys(), row)),
            ))
            continue
        site_code = STATION_TO_CODE.get(station_raw.lower())
        if not site_code:
            result.row_errors.append(RowError(
                row_number=row_number,
                reason=f"unknown station {station_raw!r}",
                raw=dict(zip(col_index.keys(), row)),
            ))
            continue
        if not creditor_raw:
            result.row_errors.append(RowError(
                row_number=row_number, reason="missing creditor",
                raw=dict(zip(col_index.keys(), row)),
            ))
            continue
        if type_raw is None:
            result.row_errors.append(RowError(
                row_number=row_number,
                reason=f"unknown type {cell('type')!r}",
                raw=dict(zip(col_index.keys(), row)),
            ))
            continue
        if was_blank:
            result.warnings.append(
                f"Row {row_number}: blank amount treated as 0.00 "
                f"({station_raw} / {creditor_raw} / {type_raw})"
            )

        key = (site_code, row_date)
        seen_creditor_names[(site_code, creditor_raw)] += 1

        if type_raw == TYPE_CREDIT_SALE:
            credit_map[key][creditor_raw] += amount
        else:  # payment
            # Same-creditor payments on the same day are summed; notes concatenated.
            bucket = collection_map[key].get(creditor_raw)
            if bucket is None:
                collection_map[key][creditor_raw] = {
                    "amount": amount,
                    "notes": [notes] if notes else [],
                }
            else:
                bucket["amount"] += amount
                if notes:
                    bucket["notes"].append(notes)

    # --- build blocks (preserve chronological order) ---
    all_keys = sorted(set(credit_map) | set(collection_map), key=lambda k: (k[1], k[0]))
    for key in all_keys:
        site_code, block_date = key
        block = BlockPreview(site=site_code, date=block_date)

        for creditor_name, total in sorted(credit_map.get(key, {}).items()):
            block.credit_sales.append({
                "creditor": creditor_name,
                "amount": f"{total:.2f}",
            })

        for creditor_name, data in sorted(collection_map.get(key, {}).items()):
            notes_joined = " | ".join(data["notes"]) if data["notes"] else ""
            block.collections.append({
                "creditor": creditor_name,
                "amount": f"{data['amount']:.2f}",
                "payment_method": extract_payment_method(notes_joined),
                "reference": notes_joined,
            })

        result.blocks.append(block)

    # --- resolve creditors against DB ---
    _resolve_creditors(result, seen_creditor_names)

    wb.close()
    return result


def _resolve_creditors(
    result: ParsedResult,
    seen: dict[tuple[str, str], int],
) -> None:
    """
    For each (site, creditor_name) seen, look up in DB.
    Populate result.unresolved for anything missing.
    """
    # Load all sites once to avoid N queries
    sites = {s.code: s for s in Site.objects.all()}

    # Cache resolved (site_code, name_lower) → Creditor or None
    for (site_code, name), occurrences in sorted(seen.items()):
        site = sites.get(site_code)
        if site is None:
            result.unresolved.append(UnresolvedCreditor(
                site=site_code, name=name, occurrences=occurrences,
                section="both",
            ))
            continue

        # Exact match first, then case-insensitive
        found = (
            Creditor.objects.filter(site=site, name=name).exists()
            or Creditor.objects.filter(site=site, name__iexact=name).exists()
        )
        if not found:
            # We don't know yet whether it's credit_sale or collection;
            # the view can refine if needed. Mark section generically.
            result.unresolved.append(UnresolvedCreditor(
                site=site_code, name=name, occurrences=occurrences,
                section="unknown",
            ))