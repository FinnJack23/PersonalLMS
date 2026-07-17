from __future__ import annotations

import socket
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionArtifactProvenance,
    ExtractionCapability,
    ExtractionJobStatus,
    ExtractionRequest,
    derive_artifact_id,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceMediaType
from personal_lms.extraction import sqlite as sqlite_module
from personal_lms.extraction.errors import (
    ExtractionArtifactNotFoundError,
    ExtractionJobNotFoundError,
    ExtractionQueueContractError,
    InvalidExtractionJobTransitionError,
)
from personal_lms.extraction.protocol import ExtractionJobFilter
from personal_lms.extraction.sqlite import SQLiteExtractionQueue

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _request(**overrides: object) -> ExtractionRequest:
    defaults: dict[str, object] = {
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "requested_capability": ExtractionCapability.PLAIN_TEXT,
        "media_kind": SourceMediaType.HTML,
        "idempotency_key": f"key-{uuid4()}",
    }
    defaults.update(overrides)
    return ExtractionRequest.model_validate(defaults)


def _artifact_for(job_id: object, **overrides: object) -> ExtractedArtifact:
    provenance_overrides = overrides.pop("provenance_overrides", {})
    provenance_defaults: dict[str, object] = {
        "job_id": job_id,
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "extractor_name": "fake-extractor",
        "extractor_version": "0.0.0-test",
        "extracted_at": _NOW,
    }
    provenance_defaults.update(provenance_overrides)
    provenance = ExtractionArtifactProvenance.model_validate(provenance_defaults)

    content_locator = overrides.get("content_locator", f"candidate://{job_id}")
    content_hash = overrides.get("content_hash")
    defaults: dict[str, object] = {
        "artifact_id": derive_artifact_id(
            job_id=provenance.job_id,
            artifact_kind=ExtractionCapability.PLAIN_TEXT,
            content_hash=content_hash,
            content_locator=content_locator,
        ),
        "artifact_kind": ExtractionCapability.PLAIN_TEXT,
        "content_locator": content_locator,
        "created_at": _NOW,
        "provenance": provenance,
    }
    defaults.update(overrides)
    return ExtractedArtifact.model_validate(defaults)


@pytest.fixture
def store() -> SQLiteExtractionQueue:
    instance = SQLiteExtractionQueue.open(":memory:")
    instance.initialize_schema()
    return instance


# --- migration -----------------------------------------------------------------


def test_fresh_in_memory_migration_succeeds() -> None:
    instance = SQLiteExtractionQueue.open(":memory:")
    instance.initialize_schema()
    instance.close()


def test_repeated_migration_is_idempotent(store: SQLiteExtractionQueue) -> None:
    store.initialize_schema()
    store.initialize_schema()


def test_schema_version_recorded(store: SQLiteExtractionQueue) -> None:
    row = store._connection.execute("SELECT version FROM schema_migrations").fetchone()
    assert row["version"] == 1


def test_foreign_keys_enabled(store: SQLiteExtractionQueue) -> None:
    row = store._connection.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            "INSERT INTO extracted_artifacts "
            "(artifact_id, job_id, inventory_source_id, source_version_id, artifact_kind, "
            " content_locator, created_at, extractor_name, extractor_version, "
            " extraction_warnings, extracted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                str(uuid4()),
                str(uuid4()),
                str(uuid4()),
                "plain_text",
                "loc",
                _NOW.isoformat(),
                "x",
                "1",
                "[]",
                _NOW.isoformat(),
            ),
        )


def test_failed_migration_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = SQLiteExtractionQueue.open(":memory:")
    broken_statements = (*sqlite_module._SCHEMA_STATEMENTS, "THIS IS NOT VALID SQL")
    monkeypatch.setattr(sqlite_module, "_SCHEMA_STATEMENTS", broken_statements)

    with pytest.raises(sqlite_module.ExtractionQueueStorageError):
        instance.initialize_schema()

    tables = {
        row["name"]
        for row in instance._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "extraction_jobs" not in tables


def test_unsupported_future_schema_version_fails_safely(store: SQLiteExtractionQueue) -> None:
    store._connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (999, ?)",
        (_NOW.isoformat(),),
    )
    store._connection.commit()

    with pytest.raises(
        sqlite_module.ExtractionQueueContractError, match="unsupported_schema_version"
    ):
        store.initialize_schema()


# --- enqueue / idempotency ------------------------------------------------------


def test_enqueue_creates_pending_job(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    assert job.status is ExtractionJobStatus.PENDING
    assert job.attempt_count == 0


def test_enqueue_is_idempotent_by_key(store: SQLiteExtractionQueue) -> None:
    request = _request(idempotency_key="stable-key")
    first = store.enqueue(request, now=_NOW)
    second = store.enqueue(request, now=_NOW + timedelta(hours=1))
    assert first.job_id == second.job_id
    assert first == second


def test_enqueue_same_key_different_identity_raises(store: SQLiteExtractionQueue) -> None:
    request = _request(idempotency_key="collide")
    store.enqueue(request, now=_NOW)
    conflicting = _request(idempotency_key="collide", inventory_source_id=uuid4())
    with pytest.raises(ExtractionQueueContractError):
        store.enqueue(conflicting, now=_NOW)


def test_get_unknown_job_raises(store: SQLiteExtractionQueue) -> None:
    with pytest.raises(ExtractionJobNotFoundError):
        store.get(uuid4())


# --- claim ordering and atomicity -----------------------------------------------


def test_claim_next_returns_none_when_empty(store: SQLiteExtractionQueue) -> None:
    assert store.claim_next(worker_id="w1", now=_NOW) is None


def test_claim_next_orders_by_priority_then_created_then_id(store: SQLiteExtractionQueue) -> None:
    low_priority = store.enqueue(_request(priority=200), now=_NOW)
    high_priority = store.enqueue(_request(priority=10), now=_NOW + timedelta(seconds=1))
    claim_time = _NOW + timedelta(seconds=2)
    claimed = store.claim_next(worker_id="w1", now=claim_time)
    assert claimed is not None
    assert claimed.job_id == high_priority.job_id
    assert claimed.job_id != low_priority.job_id


def test_claim_next_ties_break_by_created_at_then_job_id(store: SQLiteExtractionQueue) -> None:
    earlier = store.enqueue(_request(priority=50), now=_NOW)
    store.enqueue(_request(priority=50), now=_NOW + timedelta(seconds=5))
    claim_time = _NOW + timedelta(seconds=10)
    claimed = store.claim_next(worker_id="w1", now=claim_time)
    assert claimed is not None
    assert claimed.job_id == earlier.job_id


def test_claim_next_sets_claimed_fields(store: SQLiteExtractionQueue) -> None:
    store.enqueue(_request(), now=_NOW)
    claimed = store.claim_next(worker_id="worker-9", now=_NOW)
    assert claimed is not None
    assert claimed.status is ExtractionJobStatus.CLAIMED
    assert claimed.worker_id == "worker-9"
    assert claimed.attempt_count == 1
    assert claimed.claimed_at == _NOW


def test_claim_next_empty_worker_id_rejected(store: SQLiteExtractionQueue) -> None:
    store.enqueue(_request(), now=_NOW)
    with pytest.raises(ValueError):
        store.claim_next(worker_id="", now=_NOW)


def test_claim_next_at_most_one_winner_across_two_connections() -> None:
    """Two independent SQLite connections against the same file race for
    the same job — at most one may successfully claim it."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "queue.db"
        setup = SQLiteExtractionQueue.open(db_path)
        setup.initialize_schema()
        job = setup.enqueue(_request(idempotency_key="race"), now=_NOW)
        setup.close()

        results: list[object] = [None, None]
        barrier = threading.Barrier(2)

        def worker(index: int, worker_id: str) -> None:
            connection = SQLiteExtractionQueue.open(db_path)
            barrier.wait()
            results[index] = connection.claim_next(worker_id=worker_id, now=_NOW)
            connection.close()

        t1 = threading.Thread(target=worker, args=(0, "worker-a"))
        t2 = threading.Thread(target=worker, args=(1, "worker-b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert winners[0].job_id == job.job_id


# --- state transitions --------------------------------------------------------------


def test_mark_running_requires_matching_worker(store: SQLiteExtractionQueue) -> None:
    store.enqueue(_request(), now=_NOW)
    claimed = store.claim_next(worker_id="worker-a", now=_NOW)
    assert claimed is not None
    with pytest.raises(ExtractionQueueContractError):
        store.mark_running(claimed.job_id, worker_id="worker-b", now=_NOW)


def test_mark_running_from_pending_rejected_as_contract_violation(
    store: SQLiteExtractionQueue,
) -> None:
    """A never-claimed job has no ``worker_id``, so ``mark_running`` fails
    the worker-match check before it would even reach the transition
    check — a stricter, defense-in-depth rejection of the same invalid
    request."""
    job = store.enqueue(_request(), now=_NOW)
    with pytest.raises(ExtractionQueueContractError):
        store.mark_running(job.job_id, worker_id="anyone", now=_NOW)


def test_mark_running_twice_rejected_as_invalid_transition(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    with pytest.raises(InvalidExtractionJobTransitionError):
        store.mark_running(job.job_id, worker_id="w1", now=_NOW)


def test_full_happy_path_lifecycle(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    claimed = store.claim_next(worker_id="w1", now=_NOW)
    assert claimed is not None
    running = store.mark_running(claimed.job_id, worker_id="w1", now=_NOW)
    assert running.status is ExtractionJobStatus.RUNNING
    artifact = _artifact_for(job.job_id)
    succeeded = store.record_success(job.job_id, artifact, now=_NOW)
    assert succeeded.status is ExtractionJobStatus.SUCCEEDED
    assert succeeded.completed_at == _NOW


def test_record_success_is_idempotent_for_same_artifact(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    artifact = _artifact_for(job.job_id)
    first = store.record_success(job.job_id, artifact, now=_NOW)
    second = store.record_success(job.job_id, artifact, now=_NOW + timedelta(seconds=1))
    assert first == second
    assert len(store.list_artifacts_for_job(job.job_id)) == 1


def test_record_success_with_different_artifact_after_success_raises(
    store: SQLiteExtractionQueue,
) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    store.record_success(job.job_id, _artifact_for(job.job_id), now=_NOW)
    other_artifact = _artifact_for(job.job_id, content_locator="candidate://different")
    with pytest.raises(ExtractionQueueContractError):
        store.record_success(job.job_id, other_artifact, now=_NOW)


def test_record_retryable_failure_and_requeue(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    failed = store.record_retryable_failure(
        job.job_id, error_code="timeout", error_message="extractor timed out", now=_NOW
    )
    assert failed.status is ExtractionJobStatus.FAILED_RETRYABLE
    assert failed.last_error_code == "timeout"

    requeued = store.requeue(job.job_id, now=_NOW)
    assert requeued.status is ExtractionJobStatus.PENDING


def test_record_retryable_failure_idempotent_same_error(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    first = store.record_retryable_failure(
        job.job_id, error_code="timeout", error_message="m", now=_NOW
    )
    second = store.record_retryable_failure(
        job.job_id, error_code="timeout", error_message="m", now=_NOW
    )
    assert first == second


def test_record_retryable_failure_different_error_after_failure_raises(
    store: SQLiteExtractionQueue,
) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    store.record_retryable_failure(job.job_id, error_code="timeout", error_message="m", now=_NOW)
    with pytest.raises(ExtractionQueueContractError):
        store.record_retryable_failure(job.job_id, error_code="other", error_message="m2", now=_NOW)


def test_record_terminal_failure(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    failed = store.record_terminal_failure(
        job.job_id, error_code="corrupt", error_message="unrecoverable", now=_NOW
    )
    assert failed.status is ExtractionJobStatus.FAILED_TERMINAL

    with pytest.raises(InvalidExtractionJobTransitionError):
        store.requeue(job.job_id, now=_NOW)


def test_cancel_from_pending(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    cancelled = store.cancel(job.job_id, now=_NOW)
    assert cancelled.status is ExtractionJobStatus.CANCELLED


def test_cancel_from_claimed(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    cancelled = store.cancel(job.job_id, now=_NOW)
    assert cancelled.status is ExtractionJobStatus.CANCELLED


def test_cancel_from_running_rejected(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    with pytest.raises(InvalidExtractionJobTransitionError):
        store.cancel(job.job_id, now=_NOW)


# --- listing --------------------------------------------------------------------


def test_list_by_status_filters(store: SQLiteExtractionQueue) -> None:
    pending = store.enqueue(_request(), now=_NOW)
    other = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)  # claims one of them

    still_pending = store.list_by_status(
        filters=ExtractionJobFilter(status=ExtractionJobStatus.PENDING)
    )
    assert len(still_pending) == 1
    assert still_pending[0].job_id in {pending.job_id, other.job_id}


def test_list_by_status_filters_by_inventory_source(store: SQLiteExtractionQueue) -> None:
    target_source = uuid4()
    matching = store.enqueue(_request(inventory_source_id=target_source), now=_NOW)
    store.enqueue(_request(), now=_NOW)
    results = store.list_by_status(filters=ExtractionJobFilter(inventory_source_id=target_source))
    assert [r.job_id for r in results] == [matching.job_id]


# --- artifacts ------------------------------------------------------------------


def test_get_artifact_unknown_raises(store: SQLiteExtractionQueue) -> None:
    with pytest.raises(ExtractionArtifactNotFoundError):
        store.get_artifact(uuid4())


def test_get_artifact_round_trips(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(_request(), now=_NOW)
    store.claim_next(worker_id="w1", now=_NOW)
    store.mark_running(job.job_id, worker_id="w1", now=_NOW)
    artifact = _artifact_for(job.job_id, extraction_warnings=("partial page",))
    store.record_success(job.job_id, artifact, now=_NOW)
    fetched = store.get_artifact(artifact.artifact_id)
    assert fetched == artifact


# --- security / hygiene ----------------------------------------------------------


def test_no_network_access(store: SQLiteExtractionQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network access is permitted in the extraction queue")

    monkeypatch.setattr(socket, "socket", _blocked)
    job = store.enqueue(_request(), now=_NOW)
    store.get(job.job_id)


def test_no_production_filesystem_reads(tmp_path: Path) -> None:
    instance = SQLiteExtractionQueue.open(":memory:")
    instance.initialize_schema()
    instance.enqueue(_request(), now=_NOW)
    instance.close()
    assert list(tmp_path.iterdir()) == []


def test_no_extraction_content_stored_in_sqlite(store: SQLiteExtractionQueue) -> None:
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for forbidden in ("payload", "body", "transcript", "embedding", "raw_content"):
        assert not any(forbidden in table for table in tables)


def test_no_provider_or_model_imports() -> None:
    import ast

    source = Path(sqlite_module.__file__).read_text()
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in ("httpx", "crewai", "tutor", "source_verification", "providers"):
        assert forbidden not in imported_roots


def test_privacy_classification_round_trips(store: SQLiteExtractionQueue) -> None:
    job = store.enqueue(
        _request(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY), now=_NOW
    )
    fetched = store.get(job.job_id)
    assert fetched.privacy_classification is PrivacyClassification.RESTRICTED_LOCAL_ONLY
