from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from personal_lms.domain.enums import SourceType
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.promotion import (
    PromotionCandidate,
    PromotionDecision,
    PromotionDecisionOutcome,
    PromotionEligibility,
    PromotionEvent,
    PromotionExecution,
    PromotionExecutionState,
    PromotionMapping,
)
from personal_lms.promotion import sqlite as sqlite_module
from personal_lms.promotion.errors import (
    PromotionCandidateNotFoundError,
    PromotionMappingConflictError,
    PromotionRepositoryContractError,
)
from personal_lms.promotion.sqlite import SQLitePromotionRepository

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _candidate(**overrides: object) -> PromotionCandidate:
    defaults: dict[str, object] = {
        "candidate_id": uuid4(),
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "extracted_artifact_id": uuid4(),
        "proposed_catalog_source_id": "cat-1",
        "proposed_title": "A Title",
        "proposed_source_type": SourceType.DOCUMENT,
        "proposed_privacy_classification": PrivacyClassification.INTERNAL,
        "eligibility": PromotionEligibility.ELIGIBLE,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return PromotionCandidate.model_validate(defaults)


@pytest.fixture
def store() -> SQLitePromotionRepository:
    instance = SQLitePromotionRepository.open(":memory:")
    instance.initialize_schema()
    return instance


# --- migration -----------------------------------------------------------------


def test_fresh_migration_succeeds() -> None:
    instance = SQLitePromotionRepository.open(":memory:")
    instance.initialize_schema()
    instance.close()


def test_repeated_migration_idempotent(store: SQLitePromotionRepository) -> None:
    store.initialize_schema()
    store.initialize_schema()


def test_schema_version_recorded(store: SQLitePromotionRepository) -> None:
    row = store._connection.execute("SELECT version FROM schema_migrations").fetchone()
    assert row["version"] == 1


def test_foreign_keys_enabled(store: SQLitePromotionRepository) -> None:
    row = store._connection.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            "INSERT INTO promotion_decisions "
            "(decision_id, candidate_id, outcome, reviewer, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), str(uuid4()), "approve", "alan", _NOW.isoformat()),
        )


def test_failed_migration_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = SQLitePromotionRepository.open(":memory:")
    broken = (*sqlite_module._SCHEMA_STATEMENTS, "THIS IS NOT VALID SQL")
    monkeypatch.setattr(sqlite_module, "_SCHEMA_STATEMENTS", broken)
    with pytest.raises(sqlite_module.PromotionRepositoryStorageError):
        instance.initialize_schema()
    tables = {
        row["name"]
        for row in instance._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "promotion_candidates" not in tables


def test_unsupported_future_schema_version_fails_safely(store: SQLitePromotionRepository) -> None:
    store._connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (999, ?)", (_NOW.isoformat(),)
    )
    store._connection.commit()
    with pytest.raises(PromotionRepositoryContractError, match="unsupported_schema_version"):
        store.initialize_schema()


# --- candidates -----------------------------------------------------------------


def test_create_and_get_candidate(store: SQLitePromotionRepository) -> None:
    candidate = _candidate()
    store.create_candidate(candidate)
    fetched = store.get_candidate(candidate.candidate_id)
    assert fetched == candidate


def test_create_candidate_idempotent(store: SQLitePromotionRepository) -> None:
    candidate = _candidate()
    first = store.create_candidate(candidate)
    second = store.create_candidate(candidate)
    assert first == second


def test_create_candidate_id_reused_with_different_identity_rejected(
    store: SQLitePromotionRepository,
) -> None:
    candidate = _candidate()
    store.create_candidate(candidate)
    conflicting = candidate.model_copy(update={"source_version_id": uuid4()})
    with pytest.raises(PromotionRepositoryContractError):
        store.create_candidate(conflicting)


def test_get_unknown_candidate_raises(store: SQLitePromotionRepository) -> None:
    with pytest.raises(PromotionCandidateNotFoundError):
        store.get_candidate(uuid4())


def test_list_candidates_for_source(store: SQLitePromotionRepository) -> None:
    source_id = uuid4()
    a = store.create_candidate(_candidate(inventory_source_id=source_id))
    store.create_candidate(_candidate())  # different source
    results = store.list_candidates_for_source(source_id)
    assert [c.candidate_id for c in results] == [a.candidate_id]


# --- decisions -----------------------------------------------------------------


def test_record_decision_and_get_latest(store: SQLitePromotionRepository) -> None:
    candidate = store.create_candidate(_candidate())
    decision = PromotionDecision(
        candidate_id=candidate.candidate_id,
        outcome=PromotionDecisionOutcome.APPROVE,
        reviewer="alan",
        created_at=_NOW,
    )
    store.record_decision(decision)
    latest = store.get_latest_decision(candidate.candidate_id)
    assert latest == decision


def test_record_decision_for_unknown_candidate_raises(store: SQLitePromotionRepository) -> None:
    decision = PromotionDecision(
        candidate_id=uuid4(),
        outcome=PromotionDecisionOutcome.APPROVE,
        reviewer="alan",
        created_at=_NOW,
    )
    with pytest.raises(PromotionCandidateNotFoundError):
        store.record_decision(decision)


def test_decision_history_is_immutable_and_append_only(store: SQLitePromotionRepository) -> None:
    candidate = store.create_candidate(_candidate())
    first = PromotionDecision(
        candidate_id=candidate.candidate_id,
        outcome=PromotionDecisionOutcome.DEFER,
        reviewer="alan",
        created_at=_NOW,
    )
    second = PromotionDecision(
        candidate_id=candidate.candidate_id,
        outcome=PromotionDecisionOutcome.APPROVE,
        reviewer="alan",
        created_at=_NOW + timedelta(hours=1),
    )
    store.record_decision(first)
    store.record_decision(second)

    history = store.list_decisions(candidate.candidate_id)
    assert history == (first, second)
    assert store.get_latest_decision(candidate.candidate_id) == second


def test_get_latest_decision_none_when_no_decisions(store: SQLitePromotionRepository) -> None:
    candidate = store.create_candidate(_candidate())
    assert store.get_latest_decision(candidate.candidate_id) is None


# --- mappings -------------------------------------------------------------------


def test_create_and_get_mapping(store: SQLitePromotionRepository) -> None:
    mapping = PromotionMapping(
        inventory_source_id=uuid4(),
        source_version_id=uuid4(),
        catalog_source_id="cat-abc",
        mapping_version=1,
        created_at=_NOW,
    )
    store.create_mapping(mapping)
    fetched = store.get_mapping(mapping.inventory_source_id)
    assert fetched == mapping


def test_create_mapping_idempotent(store: SQLitePromotionRepository) -> None:
    mapping = PromotionMapping(
        inventory_source_id=uuid4(),
        source_version_id=uuid4(),
        catalog_source_id="cat-abc",
        mapping_version=1,
        created_at=_NOW,
    )
    first = store.create_mapping(mapping)
    second = store.create_mapping(mapping)
    assert first.created_at == second.created_at
    assert first.catalog_source_id == second.catalog_source_id


def test_create_mapping_updates_source_version_in_place(store: SQLitePromotionRepository) -> None:
    inventory_source_id = uuid4()
    first_mapping = PromotionMapping(
        inventory_source_id=inventory_source_id,
        source_version_id=uuid4(),
        catalog_source_id="cat-stable",
        mapping_version=1,
        created_at=_NOW,
    )
    store.create_mapping(first_mapping)

    later_version_id = uuid4()
    second_mapping = PromotionMapping(
        inventory_source_id=inventory_source_id,
        source_version_id=later_version_id,
        catalog_source_id="cat-stable",
        mapping_version=1,
        created_at=_NOW + timedelta(days=1),
    )
    updated = store.create_mapping(second_mapping)
    assert updated.source_version_id == later_version_id
    assert updated.catalog_source_id == "cat-stable"
    assert updated.created_at == first_mapping.created_at  # created_at preserved


def test_create_mapping_conflicting_catalog_id_rejected(store: SQLitePromotionRepository) -> None:
    inventory_source_id = uuid4()
    store.create_mapping(
        PromotionMapping(
            inventory_source_id=inventory_source_id,
            source_version_id=uuid4(),
            catalog_source_id="cat-a",
            mapping_version=1,
            created_at=_NOW,
        )
    )
    with pytest.raises(PromotionMappingConflictError):
        store.create_mapping(
            PromotionMapping(
                inventory_source_id=inventory_source_id,
                source_version_id=uuid4(),
                catalog_source_id="cat-b",
                mapping_version=1,
                created_at=_NOW,
            )
        )


def test_get_mapping_missing_returns_none(store: SQLitePromotionRepository) -> None:
    assert store.get_mapping(uuid4()) is None


# --- executions -----------------------------------------------------------------


def test_save_and_get_execution(store: SQLitePromotionRepository) -> None:
    candidate = store.create_candidate(_candidate())
    execution = PromotionExecution(
        candidate_id=candidate.candidate_id,
        decision_id=uuid4(),
        catalog_source_id="cat-x",
        execution_state=PromotionExecutionState.CATALOG_WRITE_STARTED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    store.save_execution(execution)
    fetched = store.get_execution(candidate.candidate_id)
    assert fetched == execution


def test_save_execution_preserves_created_at_on_update(store: SQLitePromotionRepository) -> None:
    candidate = store.create_candidate(_candidate())
    execution = PromotionExecution(
        candidate_id=candidate.candidate_id,
        decision_id=uuid4(),
        catalog_source_id="cat-x",
        execution_state=PromotionExecutionState.CATALOG_WRITE_STARTED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    store.save_execution(execution)
    updated = execution.model_copy(
        update={
            "execution_state": PromotionExecutionState.COMPLETED,
            "updated_at": _NOW + timedelta(minutes=5),
        }
    )
    result = store.save_execution(updated)
    assert result.created_at == _NOW
    assert result.execution_state is PromotionExecutionState.COMPLETED


def test_get_execution_missing_returns_none(store: SQLitePromotionRepository) -> None:
    assert store.get_execution(uuid4()) is None


def test_has_completed_promotion_for_source_version(store: SQLitePromotionRepository) -> None:
    source_id = uuid4()
    version_id = uuid4()
    candidate = store.create_candidate(
        _candidate(inventory_source_id=source_id, source_version_id=version_id)
    )
    assert store.has_completed_promotion_for_source_version(source_id, version_id) is False

    store.save_execution(
        PromotionExecution(
            candidate_id=candidate.candidate_id,
            decision_id=uuid4(),
            catalog_source_id="cat-x",
            execution_state=PromotionExecutionState.COMPLETED,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    assert store.has_completed_promotion_for_source_version(source_id, version_id) is True
    assert (
        store.has_completed_promotion_for_source_version(
            source_id, version_id, exclude_candidate_id=candidate.candidate_id
        )
        is False
    )
    # A different version of the same source is unaffected — Strategy B
    # allows a later version to update the same curated record.
    other_version_id = uuid4()
    assert store.has_completed_promotion_for_source_version(source_id, other_version_id) is False


# --- events ---------------------------------------------------------------------


def test_events_are_append_only_and_ordered(store: SQLitePromotionRepository) -> None:
    candidate = store.create_candidate(_candidate())
    first = PromotionEvent(
        candidate_id=candidate.candidate_id,
        event_type="candidate_created",
        occurred_at=_NOW,
        detail={},
    )
    second = PromotionEvent(
        candidate_id=candidate.candidate_id,
        event_type="decision_recorded",
        occurred_at=_NOW + timedelta(seconds=1),
        detail={"outcome": "approve"},
    )
    store.record_event(first)
    store.record_event(second)
    events = store.list_events(candidate.candidate_id)
    assert events == (first, second)


# --- security / hygiene ----------------------------------------------------------


def test_no_secrets_or_content_tables(store: SQLitePromotionRepository) -> None:
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for forbidden in ("secret", "credential", "password", "content", "body"):
        assert not any(forbidden in table for table in tables)
