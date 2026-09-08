"""
Authority record registry (Layer 8).

Checks a document against records held by whoever issued it. This is the only
layer that can answer "does this document actually exist?" -- everything else
in VeriShield reasons about the artefact in front of it.

The design decision that matters
--------------------------------
Real UIDAI, Income Tax and board verification APIs are not openly accessible.
So this layer is an INTERFACE with a synthetic registry behind it, and the
interface is the deliverable: a real provider drops in without touching any
caller.

What that must never become is a system that quietly behaves as though it
checked a real authority. Two rules enforce that:

  * Every provider states whether it is authoritative. The synthetic one says
    no, and that word travels into the signal a reviewer reads.
  * "Not found" from a synthetic registry is NEVER evidence against a document.
    A test database that holds twelve records does not know about the other
    billion people, and treating absence as suspicion would reject essentially
    everyone. Only an authoritative source may treat absence as a finding.

That second rule is the whole reason this file is careful. A registry lookup
that returns "no match" reads exactly like fraud, and getting it wrong would
make the system confidently accuse real people of forging genuine documents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Protocol

from app.schemas.document import DocumentType, ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal


class MatchOutcome(str, Enum):
    """
    Result of a registry lookup.

    NOT_FOUND and NOT_AVAILABLE are deliberately distinct. "The authority has
    no such record" is a finding; "we could not ask the authority" is a gap in
    our own coverage. Collapsing them would turn an outage into an accusation.
    """

    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_FOUND = "not_found"
    NOT_AVAILABLE = "not_available"
    REVOKED = "revoked"


@dataclass
class RegistryRecord:
    """One authority-held record."""

    document_type: str
    document_number: str
    full_name: str = ""
    date_of_birth: str = ""
    status: str = "active"          # active | revoked | suspended | lost
    reason_code: str = ""
    issued_on: str = ""


@dataclass
class LookupResult:
    outcome: MatchOutcome
    record: RegistryRecord | None = None
    mismatched_fields: list[str] = field(default_factory=list)
    provider: str = ""
    authoritative: bool = False
    note: str = ""


class RegistryProvider(Protocol):
    """
    Contract every registry backend satisfies.

    `authoritative` is part of the contract rather than a detail of the
    implementation, because the meaning of a NOT_FOUND depends entirely on it.
    """

    name: str
    authoritative: bool

    def lookup(
        self, doc_type: DocumentType, number: str, fields: ExtractedFields
    ) -> LookupResult: ...


class SyntheticRegistry:
    """
    A local, made-up registry for development and demonstration.

    Holds invented records. Reports itself as non-authoritative so that
    downstream code cannot mistake a hit here for confirmation by an issuer,
    and a miss here for evidence of forgery.
    """

    name = "synthetic-registry"
    authoritative = False

    def __init__(self, records: list[RegistryRecord] | None = None) -> None:
        self._records: dict[tuple[str, str], RegistryRecord] = {}
        for record in records or []:
            self.add(record)

    def add(self, record: RegistryRecord) -> None:
        key = (record.document_type, _normalise(record.document_number))
        self._records[key] = record

    @classmethod
    def from_file(cls, path: Path) -> SyntheticRegistry:
        """Load records from a JSON file, if one exists."""
        registry = cls()
        if not path.exists():
            return registry
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("records", []):
            registry.add(RegistryRecord(**entry))
        return registry

    def lookup(
        self, doc_type: DocumentType, number: str, fields: ExtractedFields
    ) -> LookupResult:
        record = self._records.get((doc_type.value, _normalise(number)))

        if record is None:
            return LookupResult(
                outcome=MatchOutcome.NOT_FOUND,
                provider=self.name,
                authoritative=False,
                note=(
                    "No record in the local synthetic registry. This registry "
                    "holds a handful of invented records for development, so its "
                    "silence says nothing whatsoever about the document."
                ),
            )

        if record.status != "active":
            return LookupResult(
                outcome=MatchOutcome.REVOKED,
                record=record,
                provider=self.name,
                authoritative=False,
            )

        mismatches = _compare(record, fields)
        return LookupResult(
            outcome=MatchOutcome.MISMATCH if mismatches else MatchOutcome.MATCH,
            record=record,
            mismatched_fields=mismatches,
            provider=self.name,
            authoritative=False,
        )


class NullRegistry:
    """Used when no registry is configured at all."""

    name = "none"
    authoritative = False

    def lookup(
        self, doc_type: DocumentType, number: str, fields: ExtractedFields
    ) -> LookupResult:
        return LookupResult(
            outcome=MatchOutcome.NOT_AVAILABLE,
            provider=self.name,
            authoritative=False,
            note="No authority registry is configured for this deployment.",
        )


def _normalise(value: str) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _compare(record: RegistryRecord, fields: ExtractedFields) -> list[str]:
    """
    Compare a document's fields against the authority's record.

    Name comparison reuses the cross-document matcher rather than testing
    equality, for the reason set out there: Indian identity documents disagree
    about names in entirely innocent ways -- transliteration, ordering,
    honorifics -- and exact matching would flag genuine holders.
    """
    from app.pipeline.stages.cross_document import names_match

    mismatches: list[str] = []

    if record.full_name and fields.full_name.present:
        matched, _ = names_match(record.full_name, str(fields.full_name.value))
        if not matched:
            mismatches.append("full_name")

    if record.date_of_birth and fields.date_of_birth.present:
        value = fields.date_of_birth.value
        stated = value.isoformat() if isinstance(value, date) else str(value)
        if stated != record.date_of_birth:
            mismatches.append("date_of_birth")

    return mismatches


def check_registry(
    doc_type: DocumentType,
    fields: ExtractedFields,
    provider: RegistryProvider | None = None,
) -> list[Signal]:
    """Look the document up and turn the outcome into Signals."""
    provider = provider or NullRegistry()

    if not fields.document_number.present:
        return [
            signal(
                code="registry.no_number",
                stage=Stage.DATABASE,
                title="Authority record check",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "No document number was extracted, so no authority record "
                    "could be looked up."
                ),
            )
        ]

    number = str(fields.document_number.value)
    result = provider.lookup(doc_type, number, fields)
    masked = _mask(doc_type, number)

    if result.outcome == MatchOutcome.NOT_AVAILABLE:
        return [
            signal(
                code="registry.unavailable",
                stage=Stage.DATABASE,
                title="Authority record check",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    f"{result.note} The document was assessed on its own contents "
                    f"only; nothing was confirmed against the issuer."
                ),
                evidence={"provider": provider.name},
            )
        ]

    if result.outcome == MatchOutcome.NOT_FOUND:
        # The rule this whole module exists to get right.
        if not result.authoritative:
            return [
                signal(
                    code="registry.not_found_non_authoritative",
                    stage=Stage.DATABASE,
                    title="Authority record check",
                    status=SignalStatus.SKIP,
                    severity=Severity.INFO,
                    reason=(
                        f"{masked} was not found in the {provider.name}, which is "
                        f"NOT an authoritative source -- it holds a small set of "
                        f"development records. Absence here is not evidence about "
                        f"the document and contributes nothing to its risk score."
                    ),
                    evidence={"provider": provider.name, "authoritative": False},
                )
            ]
        return [
            signal(
                code="registry.not_found",
                stage=Stage.DATABASE,
                title="Authority record check",
                status=SignalStatus.FAIL,
                severity=Severity.CRITICAL,
                confidence=0.9,
                reason=(
                    f"{masked} does not appear in the issuing authority's records. "
                    f"A document number the issuer has never allocated cannot "
                    f"belong to a genuine document."
                ),
                evidence={"provider": provider.name, "authoritative": True},
            )
        ]

    if result.outcome == MatchOutcome.REVOKED:
        record = result.record
        return [
            signal(
                code="registry.revoked",
                stage=Stage.DATABASE,
                title="Authority record status",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH if result.authoritative else Severity.MEDIUM,
                confidence=0.9 if result.authoritative else 0.4,
                blocking=True,
                reason=(
                    f"The issuer's record for {masked} is marked "
                    f"'{record.status}'"
                    + (f" ({record.reason_code})" if record and record.reason_code else "")
                    + ". A document reported lost, suspended or revoked must not be "
                    "accepted as identification even when the artefact itself is "
                    "genuine."
                    + ("" if result.authoritative else " Source is non-authoritative.")
                ),
                evidence={
                    "status": record.status if record else "",
                    "provider": provider.name,
                    "authoritative": result.authoritative,
                },
            )
        ]

    if result.outcome == MatchOutcome.MISMATCH:
        return [
            signal(
                code="registry.field_mismatch",
                stage=Stage.DATABASE,
                title="Authority record comparison",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH if result.authoritative else Severity.MEDIUM,
                confidence=0.85 if result.authoritative else 0.4,
                reason=(
                    f"The record held for {masked} disagrees with the document on: "
                    f"{', '.join(result.mismatched_fields)}. The number exists, but "
                    f"the details printed against it do not match what the issuer "
                    f"holds."
                    + ("" if result.authoritative else " Source is non-authoritative.")
                ),
                evidence={
                    "mismatched_fields": result.mismatched_fields,
                    "provider": provider.name,
                    "authoritative": result.authoritative,
                },
            )
        ]

    return [
        signal(
            code="registry.match",
            stage=Stage.DATABASE,
            title="Authority record check",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            # A hit in a made-up registry is worth very little, and the
            # confidence says so rather than letting a green tick imply the
            # issuer confirmed anything.
            confidence=0.9 if result.authoritative else 0.3,
            reason=(
                f"{masked} matches a registry record and the printed details agree "
                f"with it."
                + (
                    ""
                    if result.authoritative
                    else " Note: this is the synthetic development registry, not "
                    "the issuing authority, so this confirms nothing about the "
                    "real document."
                )
            ),
            evidence={"provider": provider.name, "authoritative": result.authoritative},
        )
    ]


def _mask(doc_type: DocumentType, number: str) -> str:
    """Render a document number safe to log. Aadhaar is masked hardest."""
    if doc_type == DocumentType.AADHAAR:
        from app.rules.aadhaar import mask as mask_aadhaar

        return mask_aadhaar(number)
    if len(number) > 4:
        return f"{'*' * (len(number) - 4)}{number[-4:]}"
    return number
