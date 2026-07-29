"""External focused-work evidence for ``G1-NG-05``.

The focused-work ledger is deliberately *outside* the frozen fixture. A
ledger entry cannot live inside a manifest while also containing that
manifest's final self-hash: doing so asks for a SHA-256 fixed point. AD-08
therefore uses immutable, append-only records in a trusted local evidence
store. The frozen fixture-ready hash is an input to those records, never an
output that is changed by them.

This module defines the canonical records and their deterministic evaluation.
It does not create attestations, read a clock, or decide who is trusted. The
caller supplies signer IDs obtained from the trusted external authority. A
``SignerAttestation`` is an explicit human attestation recorded by that
authority; an ID in an envelope is not, by itself, a cryptographic signature.

Absent evidence evaluates to ``None`` so the gate caller can report
``NOT_RUN``. Once any envelope is present, however, it must contain a complete
start/entries/closure set and every field is validated strictly. Partial,
unknown, malformed, replayed, out-of-window, or internally inconsistent
evidence fails closed.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from personal_lms.domain.base import StrictModel
from personal_lms.objective_packs.hashing import hash_record

__all__ = [
    "AttestationKind",
    "ExternalFocusedWorkLedger",
    "FocusedWorkEntry",
    "GateCleanupEvaluation",
    "GateStartRecord",
    "LedgerBindingError",
    "LedgerClosureAttestation",
    "LedgerFormatError",
    "SignerAttestation",
    "evaluate_gate_1_cleanup",
    "focused_work_entries_hash_for",
    "focused_work_scope_hash_for",
    "gate_start_hash_for",
    "parse_external_focused_work_ledger",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_REVISION_PATTERN = r"^[^\s]{1,200}$"
_UTC_TEXT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
_CANONICAL_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

GATE_START_SCHEMA = "personal-lms.focused-work.gate-start.v1"
ENTRY_SCHEMA = "personal-lms.focused-work.entry.v1"
CLOSURE_SCHEMA = "personal-lms.focused-work.closure.v1"
LEDGER_SCHEMA = "personal-lms.focused-work.external-ledger.v1"
ATTESTATION_SCHEMA = "personal-lms.focused-work.human-attestation.v1"
SCOPE_HASH_SCHEMA = "personal-lms.focused-work.scope-hash.v1"
ENTRY_SET_HASH_SCHEMA = "personal-lms.focused-work.entry-set-hash.v1"


def _parse_utc_instant(value: object) -> datetime:
    """Parse one canonical UTC instant without lossy epoch coercion."""
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if not _UTC_TEXT_PATTERN.fullmatch(value):
            raise ValueError(
                "must be canonical UTC ISO-8601 text with no more than six fractional digits"
            )
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("must be a valid UTC instant") from exc
    else:
        raise ValueError("must be a UTC datetime or canonical UTC ISO-8601 string")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("must be UTC, not naive or offset local time")
    return parsed.astimezone(UTC)


def _parse_canonical_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str) and _CANONICAL_UUID_PATTERN.fullmatch(value):
        parsed = UUID(value)
    else:
        raise ValueError("must be a canonical lowercase hyphenated UUID")
    if parsed.int == 0:
        raise ValueError("must not be the nil UUID")
    return parsed


UTCInstant = Annotated[datetime, BeforeValidator(_parse_utc_instant)]
CanonicalUUID = Annotated[UUID, BeforeValidator(_parse_canonical_uuid)]
Sha256Hex = Annotated[
    str,
    Field(strict=True, min_length=64, max_length=64, pattern=_SHA256_PATTERN),
]
OpaqueId = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128, pattern=_OPAQUE_ID_PATTERN),
]
Revision = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=200, pattern=_REVISION_PATTERN),
]


class _ImmutableLedgerModel(StrictModel):
    """Strict, immutable base for records persisted in the append-only store."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=False)


class AttestationKind(StrEnum):
    """The exact human statement represented by an external attestation."""

    GATE_START = "gate_start_authorized"
    FOCUSED_WORK_ENTRY = "focused_work_entry_accurate"
    LEDGER_CLOSURE = "ledger_complete_and_final"


class SignerAttestation(_ImmutableLedgerModel):
    """An explicit human attestation captured by a trusted external authority.

    The evidence repository authenticates the human action and supplies the
    trusted signer allowlist to evaluation. These fields preserve that
    authority's immutable audit record; they are not a detached cryptographic
    signature and must never be treated as one.
    """

    schema_id: Literal["personal-lms.focused-work.human-attestation.v1"] = Field(alias="schema")
    attestation_id: CanonicalUUID
    signer_id: OpaqueId
    kind: AttestationKind
    signed_at: UTCInstant


class GateStartRecord(_ImmutableLedgerModel):
    """Canonical persisted authorization to begin one unique Gate 1 attempt."""

    schema_id: Literal["personal-lms.focused-work.gate-start.v1"] = Field(alias="schema")
    start_record_id: CanonicalUUID
    gate_id: OpaqueId
    gate_definition_version: OpaqueId
    attempt_id: CanonicalUUID
    fixture_ready_sha256: Sha256Hex
    started_at: UTCInstant
    start_code_revision: Revision
    scope_sha256: Sha256Hex
    signer_attestation: SignerAttestation

    @model_validator(mode="after")
    def _start_attestation_is_contemporaneous(self) -> Self:
        if self.signer_attestation.kind is not AttestationKind.GATE_START:
            raise ValueError("gate start requires a gate_start_authorized attestation")
        if self.signer_attestation.signed_at != self.started_at:
            raise ValueError("gate start attestation signed_at must equal started_at")
        return self


class FocusedWorkEntry(_ImmutableLedgerModel):
    """One externally persisted, human-attested focused-work interval."""

    schema_id: Literal["personal-lms.focused-work.entry.v1"] = Field(alias="schema")
    entry_id: CanonicalUUID
    gate_id: OpaqueId
    gate_definition_version: OpaqueId
    attempt_id: CanonicalUUID
    fixture_ready_sha256: Sha256Hex
    gate_start_hash: Sha256Hex
    scope_sha256: Sha256Hex
    work_item_id: OpaqueId
    description: str = Field(strict=True, min_length=1, max_length=500)
    started_at: UTCInstant
    stopped_at: UTCInstant
    signer_attestation: SignerAttestation

    @field_validator("description")
    @classmethod
    def _description_has_no_ambiguous_outer_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("description must not contain leading or trailing whitespace")
        return value

    @model_validator(mode="after")
    def _interval_and_attestation_are_valid(self) -> Self:
        if self.stopped_at <= self.started_at:
            raise ValueError("stopped_at must be strictly after started_at")
        if self.signer_attestation.kind is not AttestationKind.FOCUSED_WORK_ENTRY:
            raise ValueError(
                "focused-work entry requires a focused_work_entry_accurate attestation"
            )
        if self.signer_attestation.signed_at < self.stopped_at:
            raise ValueError("entry attestation cannot be signed before stopped_at")
        return self

    @property
    def focused_microseconds(self) -> int:
        """Exact person-time represented by this interval."""

        return (self.stopped_at - self.started_at) // timedelta(microseconds=1)


class LedgerClosureAttestation(_ImmutableLedgerModel):
    """Signed closure stating that the external ledger is complete and final."""

    schema_id: Literal["personal-lms.focused-work.closure.v1"] = Field(alias="schema")
    closure_id: CanonicalUUID
    gate_id: OpaqueId
    gate_definition_version: OpaqueId
    attempt_id: CanonicalUUID
    fixture_ready_sha256: Sha256Hex
    gate_start_hash: Sha256Hex
    scope_sha256: Sha256Hex
    closed_at: UTCInstant
    entry_set_sha256: Sha256Hex
    entry_count: int = Field(strict=True, ge=0)
    total_focused_microseconds: int = Field(strict=True, ge=0)
    complete: Literal[True]
    signer_attestation: SignerAttestation

    @field_validator("complete", mode="before")
    @classmethod
    def _complete_is_literal_boolean_true(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("complete must be the literal boolean true")
        return value

    @model_validator(mode="after")
    def _closure_attestation_is_contemporaneous(self) -> Self:
        if self.signer_attestation.kind is not AttestationKind.LEDGER_CLOSURE:
            raise ValueError("ledger closure requires a ledger_complete_and_final attestation")
        if self.signer_attestation.signed_at != self.closed_at:
            raise ValueError("ledger closure attestation signed_at must equal closed_at")
        return self


class ExternalFocusedWorkLedger(_ImmutableLedgerModel):
    """One complete external evidence envelope for a single gate attempt."""

    schema_id: Literal["personal-lms.focused-work.external-ledger.v1"] = Field(alias="schema")
    gate_start: GateStartRecord
    entries: tuple[FocusedWorkEntry, ...]
    closure: LedgerClosureAttestation

    @field_validator("entries", mode="before")
    @classmethod
    def _entries_must_be_an_array(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            raise ValueError("entries must be an array of focused-work entry objects")
        return value

    @model_validator(mode="after")
    def _record_and_attestation_ids_are_unique(self) -> Self:
        record_ids = [
            self.gate_start.start_record_id,
            *(entry.entry_id for entry in self.entries),
            self.closure.closure_id,
        ]
        attestation_ids = [
            self.gate_start.signer_attestation.attestation_id,
            *(entry.signer_attestation.attestation_id for entry in self.entries),
            self.closure.signer_attestation.attestation_id,
        ]
        all_ids = [self.gate_start.attempt_id, *record_ids, *attestation_ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError(
                "attempt, record, entry, closure, and attestation IDs must be globally unique"
            )
        return self


class GateCleanupEvaluation(_ImmutableLedgerModel):
    """Exact person-time result for a complete, valid external ledger."""

    total_focused_microseconds: int = Field(strict=True, ge=0)
    ceiling_microseconds: int = Field(strict=True, gt=0)
    entry_count: int = Field(strict=True, ge=0)

    @property
    def within_ceiling(self) -> bool:
        return self.total_focused_microseconds <= self.ceiling_microseconds


class LedgerFormatError(ValueError):
    """An external evidence document is present but malformed or incomplete."""


class LedgerBindingError(ValueError):
    """Well-shaped evidence does not bind honestly to the requested attempt."""


def _validated_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(_OPAQUE_ID_PATTERN, value):
        raise ValueError(f"{label} must be a non-empty opaque identifier")
    return value


def _validated_revision(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(_REVISION_PATTERN, value):
        raise ValueError(f"{label} must be a non-empty revision without whitespace")
    return value


def _validated_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(_SHA256_PATTERN, value):
        raise ValueError(f"{label} must be exactly 64 lowercase hexadecimal characters")
    return value


def _validated_unique_strings(
    values: Collection[str],
    *,
    label: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(values, Collection):
        raise ValueError(f"{label} must be a collection of opaque identifiers")
    validated = tuple(_validated_identifier(value, label=label) for value in values)
    if not validated:
        raise ValueError(f"{label} must not be empty")
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label} must not contain duplicates")
    return validated


def focused_work_scope_hash_for(
    *,
    gate_id: str,
    gate_definition_version: str,
    authorized_work_item_ids: Collection[str],
) -> str:
    """Hash the exact authorized cleanup scope deterministically.

    Work-item order is not authority, so the canonical payload sorts the
    unique IDs. The gate identity and definition version prevent replay under
    a different criterion.
    """

    canonical_work_items = sorted(
        _validated_unique_strings(
            authorized_work_item_ids,
            label="authorized_work_item_ids",
        )
    )
    return hash_record(
        {
            "schema": SCOPE_HASH_SCHEMA,
            "gate_id": _validated_identifier(gate_id, label="gate_id"),
            "gate_definition_version": _validated_identifier(
                gate_definition_version,
                label="gate_definition_version",
            ),
            "authorized_work_item_ids": canonical_work_items,
        }
    )


def gate_start_hash_for(start_record: GateStartRecord) -> str:
    """Hash the complete persisted start record, with no circular dependency."""

    if not isinstance(start_record, GateStartRecord):
        raise TypeError("start_record must be a validated GateStartRecord")
    return hash_record(start_record.model_dump(mode="python", by_alias=True))


def focused_work_entries_hash_for(entries: Sequence[FocusedWorkEntry]) -> str:
    """Hash the exact set of signed entries, independent of presentation order."""

    if isinstance(entries, (str, bytes, bytearray)) or not isinstance(entries, Sequence):
        raise ValueError("entries must be a sequence of FocusedWorkEntry records")
    if any(not isinstance(entry, FocusedWorkEntry) for entry in entries):
        raise ValueError("entries must contain only validated FocusedWorkEntry records")
    entry_ids = [entry.entry_id for entry in entries]
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("entries must not contain duplicate entry_id values")
    ordered = sorted(entries, key=lambda entry: str(entry.entry_id))
    return hash_record(
        {
            "schema": ENTRY_SET_HASH_SCHEMA,
            "entries": [entry.model_dump(mode="python", by_alias=True) for entry in ordered],
        }
    )


def parse_external_focused_work_ledger(
    document: object | None,
) -> ExternalFocusedWorkLedger | None:
    """Parse one external ledger envelope with a strict, fail-closed boundary.

    ``None`` and an empty mapping mean the external authority returned no
    evidence, which remains ``NOT_RUN`` at the caller. Any other present value
    must be a complete mapping. Legacy embedded ``method``/``tied_to``/
    ``entries`` fixture sections have no compatibility acceptance path.
    """

    if document is None or document == {}:
        return None
    if not isinstance(document, dict):
        raise LedgerFormatError("external focused-work evidence must be an object")
    try:
        return ExternalFocusedWorkLedger.model_validate(document)
    except ValidationError as exc:
        raise LedgerFormatError(f"malformed external focused-work evidence: {exc}") from exc


def _require_authorized_signer(
    attestation: SignerAttestation,
    *,
    trusted_authorized_signer_ids: frozenset[str],
) -> None:
    if attestation.signer_id not in trusted_authorized_signer_ids:
        raise LedgerBindingError(
            f"attestation signer {attestation.signer_id!r} is not authorized by "
            "the trusted external authority"
        )


def _reject_per_signer_overlap(entries: Sequence[FocusedWorkEntry]) -> None:
    by_signer: dict[str, list[FocusedWorkEntry]] = {}
    for entry in entries:
        by_signer.setdefault(entry.signer_attestation.signer_id, []).append(entry)

    for signer_id, signer_entries in by_signer.items():
        ordered = sorted(
            signer_entries,
            key=lambda entry: (entry.started_at, entry.stopped_at, str(entry.entry_id)),
        )
        if not ordered:
            continue
        active = ordered[0]
        for later in ordered[1:]:
            if later.started_at < active.stopped_at:
                raise LedgerBindingError(
                    f"focused-work entries {active.entry_id} and {later.entry_id} overlap "
                    f"for signer {signer_id!r}; one person's time cannot be counted twice"
                )
            if later.stopped_at > active.stopped_at:
                active = later


def evaluate_gate_1_cleanup(
    evidence: ExternalFocusedWorkLedger | None,
    *,
    trusted_authorized_signer_ids: Collection[str],
    authorized_work_item_ids: Collection[str],
    expected_gate_id: str,
    expected_gate_definition_version: str,
    expected_attempt_id: UUID,
    expected_fixture_ready_sha256: str,
    expected_start_code_revision: str,
    ceiling_microseconds: int,
) -> GateCleanupEvaluation | None:
    """Validate and evaluate one complete Gate 1 focused-work envelope.

    Time is summed as exact integer microseconds of *person-time*. Overlap is
    rejected only for entries signed by the same person; simultaneous work by
    two distinct authorized signers is legitimate and both intervals count.
    """

    if evidence is None:
        return None
    if not isinstance(evidence, ExternalFocusedWorkLedger):
        raise LedgerBindingError("evidence must be a validated ExternalFocusedWorkLedger")
    if not isinstance(expected_attempt_id, UUID) or expected_attempt_id.int == 0:
        raise LedgerBindingError("expected_attempt_id must be a non-nil UUID")
    if type(ceiling_microseconds) is not int or ceiling_microseconds <= 0:
        raise LedgerBindingError("ceiling_microseconds must be a positive integer")

    try:
        trusted_signers = frozenset(
            _validated_unique_strings(
                trusted_authorized_signer_ids,
                label="trusted_authorized_signer_ids",
            )
        )
        authorized_work_items = frozenset(
            _validated_unique_strings(
                authorized_work_item_ids,
                label="authorized_work_item_ids",
            )
        )
        expected_scope_sha256 = focused_work_scope_hash_for(
            gate_id=expected_gate_id,
            gate_definition_version=expected_gate_definition_version,
            authorized_work_item_ids=authorized_work_items,
        )
        expected_fixture = _validated_sha256(
            expected_fixture_ready_sha256,
            label="expected_fixture_ready_sha256",
        )
        expected_gate = _validated_identifier(expected_gate_id, label="expected_gate_id")
        expected_definition = _validated_identifier(
            expected_gate_definition_version,
            label="expected_gate_definition_version",
        )
        expected_revision = _validated_revision(
            expected_start_code_revision,
            label="expected_start_code_revision",
        )
    except ValueError as exc:
        raise LedgerBindingError(str(exc)) from exc

    start = evidence.gate_start
    if start.gate_id != expected_gate:
        raise LedgerBindingError(
            f"gate start binds gate_id {start.gate_id!r}, not {expected_gate!r}"
        )
    if start.gate_definition_version != expected_definition:
        raise LedgerBindingError("gate start binds a different gate definition version")
    if start.attempt_id != expected_attempt_id:
        raise LedgerBindingError("gate start binds a different attempt_id")
    if start.fixture_ready_sha256 != expected_fixture:
        raise LedgerBindingError("gate start binds a different fixture-ready hash")
    if start.start_code_revision != expected_revision:
        raise LedgerBindingError("gate start binds a different start code revision")
    if start.scope_sha256 != expected_scope_sha256:
        raise LedgerBindingError("gate start binds a different authorized work scope")
    _require_authorized_signer(
        start.signer_attestation,
        trusted_authorized_signer_ids=trusted_signers,
    )

    start_hash = gate_start_hash_for(start)
    closure = evidence.closure
    if closure.gate_id != start.gate_id:
        raise LedgerBindingError("ledger closure binds a different gate_id")
    if closure.gate_definition_version != start.gate_definition_version:
        raise LedgerBindingError("ledger closure binds a different gate definition version")
    if closure.attempt_id != start.attempt_id:
        raise LedgerBindingError("ledger closure binds a different attempt_id")
    if closure.fixture_ready_sha256 != start.fixture_ready_sha256:
        raise LedgerBindingError("ledger closure binds a different fixture-ready hash")
    if closure.gate_start_hash != start_hash:
        raise LedgerBindingError("ledger closure binds a different gate-start hash")
    if closure.scope_sha256 != start.scope_sha256:
        raise LedgerBindingError("ledger closure binds a different authorized work scope")
    if closure.closed_at <= start.started_at:
        raise LedgerBindingError("ledger closed_at must be strictly after gate started_at")
    _require_authorized_signer(
        closure.signer_attestation,
        trusted_authorized_signer_ids=trusted_signers,
    )

    for entry in evidence.entries:
        if entry.gate_id != start.gate_id:
            raise LedgerBindingError(f"entry {entry.entry_id} binds a different gate_id")
        if entry.gate_definition_version != start.gate_definition_version:
            raise LedgerBindingError(
                f"entry {entry.entry_id} binds a different gate definition version"
            )
        if entry.attempt_id != start.attempt_id:
            raise LedgerBindingError(f"entry {entry.entry_id} binds a different attempt_id")
        if entry.fixture_ready_sha256 != start.fixture_ready_sha256:
            raise LedgerBindingError(f"entry {entry.entry_id} binds a different fixture-ready hash")
        if entry.gate_start_hash != start_hash:
            raise LedgerBindingError(f"entry {entry.entry_id} binds a different gate-start hash")
        if entry.scope_sha256 != start.scope_sha256:
            raise LedgerBindingError(
                f"entry {entry.entry_id} binds a different authorized work scope"
            )
        if entry.work_item_id not in authorized_work_items:
            raise LedgerBindingError(
                f"entry {entry.entry_id} names work item {entry.work_item_id!r} "
                "outside the authorized scope"
            )
        if entry.started_at <= start.started_at:
            raise LedgerBindingError(
                f"entry {entry.entry_id} must start strictly after gate started_at"
            )
        if entry.stopped_at > closure.closed_at:
            raise LedgerBindingError(f"entry {entry.entry_id} stops after ledger closed_at")
        if entry.signer_attestation.signed_at > closure.closed_at:
            raise LedgerBindingError(f"entry {entry.entry_id} was attested after ledger closed_at")
        _require_authorized_signer(
            entry.signer_attestation,
            trusted_authorized_signer_ids=trusted_signers,
        )

    _reject_per_signer_overlap(evidence.entries)

    entry_set_sha256 = focused_work_entries_hash_for(evidence.entries)
    total_focused_microseconds = sum(entry.focused_microseconds for entry in evidence.entries)
    if closure.entry_set_sha256 != entry_set_sha256:
        raise LedgerBindingError("ledger closure entry_set_sha256 does not match the entries")
    if closure.entry_count != len(evidence.entries):
        raise LedgerBindingError("ledger closure entry_count does not match the entries")
    if closure.total_focused_microseconds != total_focused_microseconds:
        raise LedgerBindingError(
            "ledger closure total_focused_microseconds does not match exact person-time"
        )

    if not evidence.entries:
        return None
    return GateCleanupEvaluation(
        total_focused_microseconds=total_focused_microseconds,
        ceiling_microseconds=ceiling_microseconds,
        entry_count=len(evidence.entries),
    )
