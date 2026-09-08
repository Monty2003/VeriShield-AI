"""Verification endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.pipeline.orchestrator import analyze_document, verify_case
from app.pipeline.stages.ocr import InjectedTextProvider
from app.registry.authority import SyntheticRegistry
from app.risk.engine import assess_document
from app.rules.mrz import parse_td3
from app.rules.passport import validate_passport
from app.schemas.document import DocumentAnalysis, DocumentType, VerificationResult
from app.schemas.signals import Stage
from app.storage.audit import audit_store, document_fingerprint, object_store

router = APIRouter()

# Document types whose identifier must not be echoed by default.
#
# Masking was already applied to audit records and to every reason string --
# and then the API returned the number in `fields.document_number` anyway,
# which made all of that pointless. A response travels into browser history,
# frontend state, server logs and demo screenshots; a reason string that says
# "the number ending 2613" next to a field containing the whole number is not
# protecting anything.
#
# So identifiers are masked by default and revealed only when a caller
# explicitly asks. An integration that genuinely needs the number can have it;
# nobody gets it by accident.
_MASKED_IDENTIFIER_TYPES = {
    DocumentType.AADHAAR,
    DocumentType.PAN,
    DocumentType.PASSPORT,
    DocumentType.VOTER_ID,
    DocumentType.DRIVING_LICENCE,
}


def _mask_identifiers(analysis: DocumentAnalysis) -> DocumentAnalysis:
    """Replace the document number in a response with a masked form."""
    if analysis.document_type not in _MASKED_IDENTIFIER_TYPES:
        return analysis
    field = analysis.fields.document_number
    if not field.present:
        return analysis

    text = str(field.value)
    field.value = (
        f"{'X' * (len(text) - 4)}{text[-4:]}" if len(text) > 4 else "X" * len(text)
    )
    field.raw = field.value
    # The full text is dropped too: it carries the number verbatim.
    analysis.fields.raw_text.value = None
    analysis.fields.raw_text.raw = None
    return analysis

MAX_DOCUMENTS_PER_CASE = 6

# One shared synthetic registry for the process. It reports itself as
# non-authoritative, so a hit here can never be mistaken for confirmation by an
# issuing authority -- see app/registry/authority.py.
_REGISTRY = SyntheticRegistry.from_file(
    Path(__file__).resolve().parents[3] / "data" / "synthetic_registry.json"
)


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


def _persist(analysis: DocumentAnalysis, data: bytes) -> None:
    """
    Record the assessment and retain the image, if the stores are reachable.

    Deliberately silent on failure. Persistence supports review after the fact;
    it is not part of deciding, and a storage outage must not turn a working
    verification into an error the caller has to handle.
    """
    fingerprint = document_fingerprint(data)
    audit_store.record_document(analysis, fingerprint)
    object_store.put(f"documents/{analysis.document_id}", data)


@router.post("/verify/document", response_model=DocumentAnalysis)
async def verify_document(
    file: UploadFile = File(..., description="Document image (JPEG, PNG or HEIC)"),
    declared_type: DocumentType | None = Form(
        None, description="Skip classification and apply this type's rulebook directly."
    ),
    ocr_text: str | None = Form(
        None,
        description=(
            "Supply text instead of running OCR. Two uses: exercising the rule "
            "and risk layers without the ML stack, and re-running an assessment "
            "after a reviewer corrects an OCR misread -- a misread digit produces "
            "a failed checksum that looks exactly like tampering."
        ),
    ),
    enable_copy_move: bool = Form(
        False,
        description=(
            "Enable copy-move forgery detection. Off by default: calibration "
            "measured it at chance (see docs/FORENSICS.md)."
        ),
    ),
    reveal_identifiers: bool = Form(
        False,
        description=(
            "Return the document number in full instead of masked. Off by "
            "default: responses reach logs, browser history and screenshots, "
            "and a national identity number should not travel there without "
            "someone deciding that it should."
        ),
    ),
) -> DocumentAnalysis:
    """Assess a single document and return its full evidence trail."""
    data = await _read_upload(file)
    analysis = analyze_document(
        data,
        filename=file.filename or "document",
        ocr_provider=InjectedTextProvider(ocr_text) if ocr_text else None,
        registry=_REGISTRY,
        declared_type=declared_type,
        enable_copy_move=enable_copy_move,
    )
    # Persist BEFORE masking: the audit store applies its own redaction, and
    # the validators upstream have already used the real value.
    _persist(analysis, data)
    return analysis if reveal_identifiers else _mask_identifiers(analysis)


@router.post("/verify/case", response_model=VerificationResult)
async def verify_documents(
    files: list[UploadFile] = File(..., description="One or more documents"),
    selfie: UploadFile | None = File(
        None,
        description=(
            "A photograph of the person presenting the documents. Compared "
            "against the portrait printed on them -- the one question the "
            "documents alone cannot answer."
        ),
    ),
    enable_copy_move: bool = Form(False),
    reveal_identifiers: bool = Form(False),
) -> VerificationResult:
    """
    Assess several documents together, with cross-document consistency.

    This is the endpoint that answers what a single-document check cannot: do
    these documents describe the same person, and is that person here?
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
    selfie_bytes = await _read_upload(selfie) if selfie is not None else None

    result = verify_case(
        documents,
        selfie=selfie_bytes,
        registry=_REGISTRY,
        enable_copy_move=enable_copy_move,
    )

    for analysis, (data, _) in zip(result.documents, documents):
        _persist(analysis, data)
    audit_store.record_case(result)

    if not reveal_identifiers:
        result.documents = [_mask_identifiers(d) for d in result.documents]
    return result


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
            "full_name": mrz.full_name,
            "document_number": mrz.document_number,
            "nationality": mrz.nationality,
            "date_of_birth": mrz.date_of_birth.isoformat() if mrz.date_of_birth else None,
            "sex": mrz.sex,
            "date_of_expiry": mrz.date_of_expiry.isoformat() if mrz.date_of_expiry else None,
        },
        "line2_reconstructed": mrz.line2_reconstructed,
        "reconstruction_note": mrz.reconstruction_note,
        "check_digits": [
            {
                "field": c.field_name,
                "value": c.raw_value,
                "stated": c.stated_digit,
                "computed": c.computed_digit,
                "valid": c.valid,
                # A check excluded from `trustworthy_checks` verifies against
                # filler this service inserted, not against anything the
                # document said, so it is reported as not evaluated.
                "evaluated": c in mrz.trustworthy_checks,
            }
            for c in mrz.checks
        ],
        "all_trusted_checks_valid": mrz.all_checks_valid,
        "signals": [s.model_dump(mode="json") for s in signals],
        "risk": risk.model_dump(mode="json"),
    }


@router.get("/cases/recent")
def recent_cases(limit: int = 20) -> dict[str, object]:
    """
    Recent verification outcomes from the audit trail.

    Identifiers are masked at write time, so this is safe to expose to a
    reviewer without handing back the identity numbers themselves.
    """
    if not audit_store.available:
        return {
            "available": False,
            "reason": (
                "The audit store is unreachable, so no history is available. "
                "Verifications still run; they are simply not being recorded."
            ),
            "cases": [],
        }
    return {"available": True, "cases": audit_store.recent_cases(limit)}


@router.get("/documents/{fingerprint}/history")
def document_history(fingerprint: str) -> dict[str, object]:
    """
    Previous assessments of a byte-identical document.

    Resubmitting a refused document is itself a signal, and it is only visible
    from history.
    """
    if not audit_store.available:
        return {"available": False, "submissions": []}
    submissions = audit_store.find_by_fingerprint(fingerprint)
    return {
        "available": True,
        "submissions": submissions,
        "resubmission": len(submissions) > 1,
    }
