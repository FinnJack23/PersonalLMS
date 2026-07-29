"""Adversarial tests for the approved external AD-08 evidence contract."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from personal_lms.labs.ccna_mastery.focused_work_ledger import (
    AttestationKind,
    ExternalFocusedWorkLedger,
    FocusedWorkEntry,
    GateCleanupEvaluation,
    GateStartRecord,
    LedgerBindingError,
    LedgerClosureAttestation,
    LedgerFormatError,
    SignerAttestation,
    evaluate_gate_1_cleanup,
    focused_work_entries_hash_for,
    focused_work_scope_hash_for,
    gate_start_hash_for,
    parse_external_focused_work_ledger,
)
from personal_lms.labs.ccna_mastery.gates import GateCheckStatus
from personal_lms.labs.ccna_mastery.wiring import EvidenceGateRunner
from personal_lms.objective_packs.linchpin_fixture import FixtureExtensions
from personal_lms.objective_packs.loader import PackLoadResult

from ..objective_packs._helpers import make_pack

_ATTESTATION_SCHEMA = "personal-lms.focused-work.human-attestation.v1"
_START_SCHEMA = "personal-lms.focused-work.gate-start.v1"
_ENTRY_SCHEMA = "personal-lms.focused-work.entry.v1"
_CLOSURE_SCHEMA = "personal-lms.focused-work.closure.v1"
_LEDGER_SCHEMA = "personal-lms.focused-work.external-ledger.v1"

_GATE_ID = "gate-1"
_GATE_DEFINITION_VERSION = "1.0"
_FIXTURE_READY_SHA256 = "3798e2181eccf666c27df267df0c784460be1615e9229ae101644ff0333997a3"
_CODE_REVISION = "abc123"
_WORK_ITEMS = ("manual-cleanup",)
_STARTED_AT = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
_FOUR_HOURS_US = 4 * 60 * 60 * 1_000_000
_ATTEMPT_ID = UUID(int=1)


def _uuid(number: int) -> UUID:
    return UUID(int=number)


def _attestation(
    *, kind: AttestationKind, signer_id: str, signed_at: datetime, identifier: int
) -> SignerAttestation:
    return SignerAttestation(
        schema=_ATTESTATION_SCHEMA,
        attestation_id=_uuid(identifier),
        signer_id=signer_id,
        kind=kind,
        signed_at=signed_at,
    )


def _start_record(
    *,
    attempt_id: UUID = _ATTEMPT_ID,
    signer_id: str = "alan",
    started_at: datetime = _STARTED_AT,
) -> GateStartRecord:
    return GateStartRecord(
        schema=_START_SCHEMA,
        start_record_id=_uuid(2),
        gate_id=_GATE_ID,
        gate_definition_version=_GATE_DEFINITION_VERSION,
        attempt_id=attempt_id,
        fixture_ready_sha256=_FIXTURE_READY_SHA256,
        started_at=started_at,
        start_code_revision=_CODE_REVISION,
        scope_sha256=focused_work_scope_hash_for(
            gate_id=_GATE_ID,
            gate_definition_version=_GATE_DEFINITION_VERSION,
            authorized_work_item_ids=_WORK_ITEMS,
        ),
        signer_attestation=_attestation(
            kind=AttestationKind.GATE_START,
            signer_id=signer_id,
            signed_at=started_at,
            identifier=3,
        ),
    )


def _entry(
    start: GateStartRecord,
    *,
    index: int,
    started_at: datetime,
    stopped_at: datetime,
    signer_id: str = "alan",
    signed_at: datetime | None = None,
    work_item_id: str = "manual-cleanup",
) -> FocusedWorkEntry:
    return FocusedWorkEntry(
        schema=_ENTRY_SCHEMA,
        entry_id=_uuid(10 + index),
        gate_id=start.gate_id,
        gate_definition_version=start.gate_definition_version,
        attempt_id=start.attempt_id,
        fixture_ready_sha256=start.fixture_ready_sha256,
        gate_start_hash=gate_start_hash_for(start),
        scope_sha256=start.scope_sha256,
        work_item_id=work_item_id,
        description=f"cleanup interval {index}",
        started_at=started_at,
        stopped_at=stopped_at,
        signer_attestation=_attestation(
            kind=AttestationKind.FOCUSED_WORK_ENTRY,
            signer_id=signer_id,
            signed_at=signed_at or stopped_at,
            identifier=30 + index,
        ),
    )


def _closure(
    start: GateStartRecord,
    entries: tuple[FocusedWorkEntry, ...],
    *,
    closed_at: datetime | None = None,
    signer_id: str = "alan",
) -> LedgerClosureAttestation:
    if closed_at is None:
        latest = max(
            (entry.signer_attestation.signed_at for entry in entries),
            default=start.started_at,
        )
        closed_at = latest + timedelta(microseconds=1)
    return LedgerClosureAttestation(
        schema=_CLOSURE_SCHEMA,
        closure_id=_uuid(90),
        gate_id=start.gate_id,
        gate_definition_version=start.gate_definition_version,
        attempt_id=start.attempt_id,
        fixture_ready_sha256=start.fixture_ready_sha256,
        gate_start_hash=gate_start_hash_for(start),
        scope_sha256=start.scope_sha256,
        closed_at=closed_at,
        entry_set_sha256=focused_work_entries_hash_for(entries),
        entry_count=len(entries),
        total_focused_microseconds=sum(entry.focused_microseconds for entry in entries),
        complete=True,
        signer_attestation=_attestation(
            kind=AttestationKind.LEDGER_CLOSURE,
            signer_id=signer_id,
            signed_at=closed_at,
            identifier=91,
        ),
    )


def _ledger(
    entries: tuple[FocusedWorkEntry, ...],
    *,
    start: GateStartRecord | None = None,
    closed_at: datetime | None = None,
    closure_signer_id: str = "alan",
) -> ExternalFocusedWorkLedger:
    gate_start = start or _start_record()
    return ExternalFocusedWorkLedger(
        schema=_LEDGER_SCHEMA,
        gate_start=gate_start,
        entries=entries,
        closure=_closure(
            gate_start,
            entries,
            closed_at=closed_at,
            signer_id=closure_signer_id,
        ),
    )


def _evaluate(
    evidence: ExternalFocusedWorkLedger | None,
    *,
    trusted_signers: tuple[str, ...] = ("alan",),
    ceiling_microseconds: int = _FOUR_HOURS_US,
) -> GateCleanupEvaluation | None:
    start = evidence.gate_start if evidence is not None else _start_record()
    return evaluate_gate_1_cleanup(
        evidence,
        trusted_authorized_signer_ids=trusted_signers,
        authorized_work_item_ids=_WORK_ITEMS,
        expected_gate_id=_GATE_ID,
        expected_gate_definition_version=_GATE_DEFINITION_VERSION,
        expected_attempt_id=start.attempt_id,
        expected_fixture_ready_sha256=_FIXTURE_READY_SHA256,
        expected_start_code_revision=_CODE_REVISION,
        ceiling_microseconds=ceiling_microseconds,
    )


def _load_result_with_focused_time_policy(
    *, embedded_entries: list[object] | None = None
) -> PackLoadResult:
    pack = make_pack()
    return PackLoadResult(
        pack=pack,
        manifest=pack.manifest,
        fixture_manifest_hash=_FIXTURE_READY_SHA256,
        fixture_extensions=FixtureExtensions(
            focused_time_ledger_document={
                "method": "signed_human_start_stop_entries",
                "tied_to": ["fixture_ready_sha256", "gate_start_hash"],
                "gate_1_manual_cleanup_ceiling_hours": 4,
                "gate_3_factory_ceiling_hours": 3,
                "entries": embedded_entries if embedded_entries is not None else [],
            }
        ),
    )


def test_scope_hash_is_order_independent_but_definition_bound() -> None:
    forward = focused_work_scope_hash_for(
        gate_id=_GATE_ID,
        gate_definition_version=_GATE_DEFINITION_VERSION,
        authorized_work_item_ids=_WORK_ITEMS,
    )
    reverse = focused_work_scope_hash_for(
        gate_id=_GATE_ID,
        gate_definition_version=_GATE_DEFINITION_VERSION,
        authorized_work_item_ids=tuple(reversed(_WORK_ITEMS)),
    )
    other_definition = focused_work_scope_hash_for(
        gate_id=_GATE_ID,
        gate_definition_version="1.1",
        authorized_work_item_ids=_WORK_ITEMS,
    )

    assert forward == reverse
    assert forward != other_definition


def test_gate_start_hash_includes_the_unique_attempt_id() -> None:
    first = _start_record(attempt_id=_uuid(1))
    second = first.model_copy(update={"attempt_id": _uuid(4)})

    assert gate_start_hash_for(first) != gate_start_hash_for(second)


def test_external_entries_do_not_create_a_fixture_self_hash_cycle() -> None:
    start = _start_record()
    before_entries = gate_start_hash_for(start)
    first = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    second = _entry(
        start,
        index=2,
        started_at=start.started_at + timedelta(hours=1),
        stopped_at=start.started_at + timedelta(hours=2),
    )

    assert gate_start_hash_for(start) == before_entries
    assert start.fixture_ready_sha256 == _FIXTURE_READY_SHA256
    assert focused_work_entries_hash_for((first,)) != focused_work_entries_hash_for((first, second))


def test_absent_and_empty_external_evidence_remain_not_run() -> None:
    assert parse_external_focused_work_ledger(None) is None
    assert parse_external_focused_work_ledger({}) is None
    assert _evaluate(None) is None


def test_partial_envelope_is_rejected_instead_of_treated_as_empty() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    payload = _ledger((entry,), start=start).model_dump(mode="json", by_alias=True)
    payload.pop("closure")

    with pytest.raises(LedgerFormatError, match="closure"):
        parse_external_focused_work_ledger(payload)


@pytest.mark.parametrize("entries", [[], [{"reviewer_id": "alan"}]])
def test_legacy_embedded_contract_has_no_acceptance_path(
    entries: list[dict[str, str]],
) -> None:
    with pytest.raises(LedgerFormatError):
        parse_external_focused_work_ledger(
            {
                "method": "signed_human_start_stop_entries",
                "tied_to": ["manifest_self_sha256", "gate_start_hash"],
                "gate_1_manual_cleanup_ceiling_hours": 4,
                "entries": entries,
            }
        )


def test_unauthorized_entry_signer_is_rejected() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        signer_id="mallory",
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )

    with pytest.raises(LedgerBindingError, match="not authorized"):
        _evaluate(_ledger((entry,), start=start), trusted_signers=("alan",))


def test_entry_must_start_strictly_after_gate_start() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at,
        stopped_at=start.started_at + timedelta(hours=1),
    )

    with pytest.raises(LedgerBindingError, match="strictly after"):
        _evaluate(_ledger((entry,), start=start))


def test_entry_cannot_extend_past_signed_closure() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=2),
    )
    evidence = _ledger(
        (entry,),
        start=start,
        closed_at=start.started_at + timedelta(hours=1),
    )

    with pytest.raises(LedgerBindingError, match="after ledger closed_at"):
        _evaluate(evidence)


@pytest.mark.parametrize(
    ("started_delta", "stopped_delta"),
    [
        (timedelta(hours=1), timedelta(hours=1)),
        (timedelta(hours=2), timedelta(hours=1)),
    ],
)
def test_zero_or_negative_intervals_are_rejected(
    started_delta: timedelta, stopped_delta: timedelta
) -> None:
    start = _start_record()

    with pytest.raises(ValidationError, match="strictly after"):
        _entry(
            start,
            index=1,
            started_at=start.started_at + started_delta,
            stopped_at=start.started_at + stopped_delta,
        )


def test_overlap_is_rejected_for_the_same_signer() -> None:
    start = _start_record()
    first = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=2),
    )
    second = _entry(
        start,
        index=2,
        started_at=start.started_at + timedelta(hours=1),
        stopped_at=start.started_at + timedelta(hours=3),
    )

    with pytest.raises(LedgerBindingError, match="overlap"):
        _evaluate(_ledger((first, second), start=start))


def test_concurrent_distinct_signers_count_as_person_time() -> None:
    start = _start_record()
    first = _entry(
        start,
        index=1,
        signer_id="alan",
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=2, microseconds=1),
    )
    second = _entry(
        start,
        index=2,
        signer_id="reviewer-2",
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=2, microseconds=1),
    )

    evaluation = _evaluate(
        _ledger((first, second), start=start),
        trusted_signers=("alan", "reviewer-2"),
    )

    assert evaluation is not None
    assert evaluation.total_focused_microseconds == _FOUR_HOURS_US
    assert evaluation.within_ceiling


def test_fractional_second_over_ceiling_is_not_truncated() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=4, microseconds=2),
    )

    evaluation = _evaluate(_ledger((entry,), start=start))

    assert evaluation is not None
    assert evaluation.total_focused_microseconds == _FOUR_HOURS_US + 1
    assert not evaluation.within_ceiling


@pytest.mark.parametrize("payload", [42, [], [None], "not-an-object"])
def test_malformed_top_level_scalars_and_lists_fail_closed(payload: object) -> None:
    with pytest.raises(LedgerFormatError, match="must be an object"):
        parse_external_focused_work_ledger(payload)


@pytest.mark.parametrize("bad_entries", [42, "entry", [None], [17]])
def test_malformed_entry_collections_fail_closed(bad_entries: object) -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    payload = _ledger((entry,), start=start).model_dump(mode="json", by_alias=True)
    payload["entries"] = bad_entries

    with pytest.raises(LedgerFormatError):
        parse_external_focused_work_ledger(payload)


def test_unknown_top_level_and_nested_fields_fail_closed() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    base = _ledger((entry,), start=start).model_dump(mode="json", by_alias=True)
    top_level: dict[str, Any] = deepcopy(base)
    top_level["unknown"] = True
    nested: dict[str, Any] = deepcopy(base)
    nested["entries"][0]["unknown"] = True

    with pytest.raises(LedgerFormatError, match="extra_forbidden"):
        parse_external_focused_work_ledger(top_level)
    with pytest.raises(LedgerFormatError, match="extra_forbidden"):
        parse_external_focused_work_ledger(nested)


def test_numeric_and_submicrosecond_timestamp_scalars_fail_closed() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    base = _ledger((entry,), start=start).model_dump(mode="json", by_alias=True)
    numeric = deepcopy(base)
    numeric["entries"][0]["started_at"] = 1_722_174_400
    too_precise = deepcopy(base)
    too_precise["entries"][0]["started_at"] = "2026-07-28T14:00:00.0000001Z"

    with pytest.raises(LedgerFormatError, match="canonical UTC"):
        parse_external_focused_work_ledger(numeric)
    with pytest.raises(LedgerFormatError, match="canonical UTC"):
        parse_external_focused_work_ledger(too_precise)


def test_duplicate_entry_or_attestation_ids_are_rejected() -> None:
    start = _start_record()
    first = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    second = _entry(
        start,
        index=2,
        started_at=start.started_at + timedelta(hours=1),
        stopped_at=start.started_at + timedelta(hours=2),
    )
    payload = _ledger((first, second), start=start).model_dump(mode="json", by_alias=True)
    payload["entries"][1]["entry_id"] = payload["entries"][0]["entry_id"]

    with pytest.raises(LedgerFormatError, match="globally unique"):
        parse_external_focused_work_ledger(payload)


@pytest.mark.parametrize(
    "closure_update",
    [
        {"entry_count": 2},
        {"total_focused_microseconds": 1},
        {"entry_set_sha256": "f" * 64},
    ],
)
def test_closure_must_match_exact_count_total_and_entry_set(
    closure_update: dict[str, object],
) -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    evidence = _ledger((entry,), start=start)
    mismatched = evidence.model_copy(
        update={"closure": evidence.closure.model_copy(update=closure_update)}
    )

    with pytest.raises(LedgerBindingError, match="does not match"):
        _evaluate(mismatched)


def test_closure_completeness_must_be_explicit_boolean_true() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    payload = _ledger((entry,), start=start).model_dump(mode="json", by_alias=True)
    payload["closure"]["complete"] = False

    with pytest.raises(LedgerFormatError, match="literal boolean true"):
        parse_external_focused_work_ledger(payload)


def test_entry_attempt_binding_cannot_be_replayed() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    ).model_copy(update={"attempt_id": _uuid(100)})
    evidence = _ledger((entry,), start=start)

    with pytest.raises(LedgerBindingError, match="different attempt_id"):
        _evaluate(evidence)


def test_entry_outside_authorized_work_scope_is_rejected() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        work_item_id="unapproved-work",
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )

    with pytest.raises(LedgerBindingError, match="outside the authorized scope"):
        _evaluate(_ledger((entry,), start=start))


def test_valid_json_shape_round_trips_without_losing_microseconds() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(seconds=1, microseconds=2),
    )
    original = _ledger((entry,), start=start)

    parsed = parse_external_focused_work_ledger(original.model_dump(mode="json", by_alias=True))

    assert parsed == original
    assert parsed is not None
    assert parsed.entries[0].focused_microseconds == 1_000_001


def test_runner_reports_not_run_when_external_evidence_is_absent() -> None:
    result = EvidenceGateRunner._focused_time_ledger_check(
        _load_result_with_focused_time_policy(),
        evidence=None,
        expected_attempt_id=None,
        trusted_signer_ids=(),
        code_revision=_CODE_REVISION,
    )

    assert result.status is GateCheckStatus.NOT_RUN
    assert result.reason_code == "external_focused_time_ledger_absent"


def test_runner_forbids_nonempty_embedded_entries() -> None:
    result = EvidenceGateRunner._focused_time_ledger_check(
        _load_result_with_focused_time_policy(embedded_entries=[{"reviewer_id": "self-asserted"}]),
        evidence=None,
        expected_attempt_id=None,
        trusted_signer_ids=(),
        code_revision=_CODE_REVISION,
    )

    assert result.status is GateCheckStatus.FAILED
    assert result.reason_code == "embedded_focused_work_entries_forbidden"


def test_runner_passes_a_complete_valid_external_ledger() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    evidence = _ledger((entry,), start=start)

    result = EvidenceGateRunner._focused_time_ledger_check(
        _load_result_with_focused_time_policy(),
        evidence=evidence,
        expected_attempt_id=start.attempt_id,
        trusted_signer_ids=("alan",),
        code_revision=_CODE_REVISION,
    )

    assert result.status is GateCheckStatus.PASSED
    assert result.reason_code == "focused_cleanup_within_ceiling"


def test_runner_fails_closed_for_the_wrong_expected_attempt() -> None:
    start = _start_record()
    entry = _entry(
        start,
        index=1,
        started_at=start.started_at + timedelta(microseconds=1),
        stopped_at=start.started_at + timedelta(hours=1),
    )
    evidence = _ledger((entry,), start=start)

    result = EvidenceGateRunner._focused_time_ledger_check(
        _load_result_with_focused_time_policy(),
        evidence=evidence,
        expected_attempt_id=_uuid(200),
        trusted_signer_ids=("alan",),
        code_revision=_CODE_REVISION,
    )

    assert result.status is GateCheckStatus.FAILED
    assert result.reason_code == "focused_time_ledger_binding_violation"
