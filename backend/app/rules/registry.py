"""
Rulebook registry (Layer 4 dispatch).

Maps a document type to its validator. Types without a rulebook yet return an
explicit SKIP rather than silently passing -- "we did not check this" and
"we checked this and it was fine" must never look the same to a reviewer.
"""

from __future__ import annotations

from typing import Callable

from app.rules.mrz import MRZData
from app.rules.passport import validate_passport
from app.schemas.document import DocumentType, ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

Validator = Callable[[MRZData | None, ExtractedFields | None], list[Signal]]

_REGISTRY: dict[DocumentType, Validator] = {
    DocumentType.PASSPORT: validate_passport,
}


def validate_for_type(
    doc_type: DocumentType,
    mrz: MRZData | None,
    fields: ExtractedFields | None,
) -> list[Signal]:
    """Run the rulebook for this document type, if one exists."""
    validator = _REGISTRY.get(doc_type)
    if validator is None:
        return [
            signal(
                code="validate.no_rulebook",
                stage=Stage.VALIDATE,
                title="Document rules",
                status=SignalStatus.FAIL,
                severity=Severity.LOW,
                # Blocking rather than heavily scored: no rulebook means no
                # content was verified, which is a gap in coverage rather than
                # evidence against the document. The fraud score should stay
                # low and honest; acceptance is what must be withheld.
                blocking=True,
                reason=(
                    f"No validation rulebook is implemented yet for "
                    f"'{doc_type.value}'. This document received image-level and "
                    f"extraction checks only -- its content was NOT validated, so "
                    f"it cannot be accepted on this assessment. Absence of "
                    f"findings here does not mean the document passed."
                ),
                evidence={
                    "document_type": doc_type.value,
                    "implemented": sorted(t.value for t in _REGISTRY),
                },
            )
        ]
    return validator(mrz, fields)


def supported_types() -> list[str]:
    """Document types with a working rulebook -- surfaced by the API."""
    return sorted(t.value for t in _REGISTRY)
