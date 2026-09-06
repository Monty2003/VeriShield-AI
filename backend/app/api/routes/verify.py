"""Verification endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.pipeline.orchestrator import analyze_document, verify_case
from app.pipeline.stages.ocr import InjectedTextProvider
from app.rules.mrz import parse_td3
from app.rules.passport import validate_passport
from app.risk.engine import assess_document
from app.schemas.document import DocumentAnalysis, DocumentType, VerificationResult
from app.schemas.signals import Stage

router = APIRouter()

MAX_DOCUMENTS_PER_CASE = 6


async def _read_upload(file: UploadFile) -> bytes:
    """Read an upload, enforcing the size limit."""
    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{file.filename} is {len(data) / 1024 / 1024:.1f} MB, over the "
                f"{settings.max_upload_mb} MB limit."
            ),
        )
    if not data:
        raise HTTPException(status_code=400, detail=f"{file.filename} is empty.")
    return data


@router.post("/verify/document", response_model=DocumentAnalysis)
async def verify_document(
    file: UploadFile = File(..., description="Document image (JPEG or PNG)"),
    declared_type: DocumentType | None = Form(
        None,
        description="Skip classification and apply this type's rulebook directly.",
    ),
    ocr_text: str | None = Form(
        None,
        description=(
            "Supply text instead of running OCR. Two uses: exercising the rule "
            "and risk layers before the ML stack is installed, and re-running an "
            "assessment after a reviewer corrects an OCR misread -- a misread MRZ "
            "character produces a failed checksum that looks exactly like tampering."
        ),
    ),
    enable_copy_move: bool = Form(
        False,
        description=(
            "Enable copy-move forgery detection. Off by default: it is not yet "
            "calibrated and currently returns the same regions for clean and "
            "tampered documents alike."
        ),
    ),
) -> DocumentAnalysis:
    """Assess a single document and return its full evidence trail."""
    data = await _read_upload(file)
    provider = InjectedTextProvider(ocr_text) if ocr_text else None

    return analyze_document(
        data,
        filename=file.filename or "document",
        ocr_provider=provider,
        declared_type=declared_type,
        enable_copy_move=enable_copy_move,
    )


@router.post("/verify/case", response_model=VerificationResult)
async def verify_documents(
    files: list[UploadFile] = File(..., description="Two or more documents"),
    enable_copy_move: bool = Form(False),
) -> VerificationResult:
    """
    Assess several documents together, including cross-document consistency.

    This is the endpoint that answers the question a single-document check
    cannot: do these documents describe the same person?
    """
    if len(files) > MAX_DOCUMENTS_PER_CASE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(files)} documents submitted; the limit is "
                f"{MAX_DOCUMENTS_PER_CASE} per case."
            ),
        )

    documents = [(await _read_upload(f), f.filename or "document") for f in files]
    return verify_case(documents, enable_copy_move=enable_copy_move)


@router.post("/verify/mrz")
def verify_mrz_lines(line1: str = Form(...), line2: str = Form(...)) -> dict[str, object]:
    """
    Validate MRZ lines directly, with no image.

    Useful for testing, for documents whose MRZ was read by a dedicated
    scanner, and for demonstrating that check-digit validation is real
    arithmetic rather than a model's guess.
    """
    mrz = parse_td3(line1, line2)
    signals = validate_passport(mrz)
    risk = assess_document(signals, expected_stages=(Stage.VALIDATE,))

    return {
        "parsed": {
            "document_code": mrz.document_code,
            "issuing_country": mrz.issuing_country,
            "surname": mrz.surname,
            "given_names": mrz.given_names,
            "document_number": mrz.document_number,
            "nationality": mrz.nationality,
            "date_of_birth": mrz.date_of_birth.isoformat() if mrz.date_of_birth else None,
            "sex": mrz.sex,
            "date_of_expiry": mrz.date_of_expiry.isoformat() if mrz.date_of_expiry else None,
        },
        "check_digits": [
            {
                "field": c.field_name,
                "value": c.raw_value,
                "stated": c.stated_digit,
                "computed": c.computed_digit,
                "valid": c.valid,
            }
            for c in mrz.checks
        ],
        "all_checks_valid": mrz.all_checks_valid,
        "signals": [s.model_dump(mode="json") for s in signals],
        "risk": risk.model_dump(mode="json"),
    }
