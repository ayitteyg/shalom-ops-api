"""
AR views — thin DRF views that call services and return JSON.

Endpoints:
  GET  /api/v1/ar/dashboard/
  GET  /api/v1/ar/creditors/
  GET  /api/v1/ar/creditors/<id>/
  POST /api/v1/ar/summaries/upload/     (multipart → preview)
  POST /api/v1/ar/summaries/            (commit blocks)
"""

from datetime import date, timedelta

from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    parser_classes,
)
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services import (
    compute_dashboard,
    compute_creditors_list,
    compute_creditor_ledger,
)
from .services.excel_import import parse_summary_file
from .services.commit_summary import commit_blocks, CommitError
from .serializers import (
    DashboardResponseSerializer,
    CreditorsListResponseSerializer,
    CreditorLedgerSerializer,
    SummaryUploadPreviewSerializer,
    SummaryCommitPayloadSerializer,
    SummaryCommitResultSerializer,
)
from .models import SummaryUpload


def _parse_date(value, default):
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return default


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_view(request):
    site_code = request.query_params.get("site_code") or None
    today = date.today()
    from_date = _parse_date(request.query_params.get("from"), today - timedelta(days=29))
    to_date = _parse_date(request.query_params.get("to"), today)

    data = compute_dashboard(site_code=site_code, from_date=from_date, to_date=to_date)
    serializer = DashboardResponseSerializer(data)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def creditors_list_view(request):
    site_code = request.query_params.get("site_code") or None
    aging_bucket = request.query_params.get("aging_bucket") or None
    status_filter = request.query_params.get("status") or None
    search = request.query_params.get("search") or None

    data = compute_creditors_list(
        site_code=site_code,
        aging_bucket=aging_bucket,
        status=status_filter,
        search=search,
    )
    serializer = CreditorsListResponseSerializer(data)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def creditor_ledger_view(request, creditor_id: int):
    data = compute_creditor_ledger(creditor_id)
    if data is None:
        return Response(
            {"detail": "Creditor not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    serializer = CreditorLedgerSerializer(data)
    return Response(serializer.data)


# ---------------------------------------------------------------------------
# Upload → preview
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_summary_view(request):
    """
    Accepts an Excel file, parses it, resolves creditors against DB,
    returns a preview. Nothing is committed here.
    """
    file_obj = request.FILES.get("file")
    if not file_obj:
        return Response(
            {"detail": "No file provided. Send as multipart field 'file'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Persist for audit / dedupe
    upload = SummaryUpload(
        original_filename=file_obj.name,
        content_type=file_obj.content_type or "",
    )
    upload.file = file_obj
    try:
        upload.save()
    except Exception as exc:  # unique sha256 violation = duplicate file
        existing = SummaryUpload.objects.filter(
            original_filename=file_obj.name
        ).first()
        return Response(
            {
                "detail": "This file has already been uploaded.",
                "existing_upload_id": existing.id if existing else None,
            },
            status=status.HTTP_409_CONFLICT,
        )

    # Parse
    try:
        upload.file.open("rb")
        result = parse_summary_file(upload.file)
    except ValueError as exc:
        upload.status = "failed"
        upload.parse_error = str(exc)
        upload.save(update_fields=["status", "parse_error"])
        return Response(
            {"detail": f"Could not parse file: {exc}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    finally:
        upload.file.close()

    preview = result.to_dict()
    upload.status = "parsed" if result.is_clean else "failed"
    upload.parse_warnings = preview["warnings"]
    upload.parsed_payload = preview
    upload.parse_error = "" if result.is_clean else "Unresolved creditors or row errors."
    upload.save(update_fields=["status", "parse_warnings", "parsed_payload", "parse_error"])

    body = {**preview, "upload_id": upload.id}
    serializer = SummaryUploadPreviewSerializer(body)
    return Response(
        {**serializer.data, "upload_id": upload.id},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser])
def submit_summary_view(request):
    """
    Accepts {blocks: [...]} and commits all blocks atomically.
    Optionally accepts upload_id to link provenance.
    """
    serializer = SummaryCommitPayloadSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    blocks = serializer.validated_data["blocks"]
    upload_id = request.data.get("upload_id")
    upload = SummaryUpload.objects.filter(id=upload_id).first() if upload_id else None

    committed_by = (
        getattr(request.user, "email", None)
        or getattr(request.user, "username", "")
        or "unknown"
    )

    try:
        results = commit_blocks(blocks, committed_by=committed_by, upload=upload)
    except CommitError as exc:
        # Map to 409 if there's a conflict, 400 otherwise
        code = (
            status.HTTP_409_CONFLICT
            if exc.detail.get("conflicts")
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(exc.detail, status=code)

    body = {
        "upload_id": upload.id if upload else None,
        "blocks": results,
    }
    return Response(
        SummaryCommitResultSerializer(body).data,
        status=status.HTTP_201_CREATED,
    )