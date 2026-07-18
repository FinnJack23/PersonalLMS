from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionArtifactProvenance,
    ExtractionCapability,
    ExtractionJob,
    derive_artifact_id,
)
from personal_lms.domain.source_inventory import SourceMediaType
from personal_lms.extraction.fake import FakeExtractor

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _job(job_id: object | None = None, **overrides: object) -> ExtractionJob:
    defaults: dict[str, object] = {
        "job_id": job_id or uuid4(),
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "requested_capability": ExtractionCapability.PLAIN_TEXT,
        "media_kind": SourceMediaType.HTML,
        "created_at": _NOW,
        "updated_at": _NOW,
        "idempotency_key": "k",
    }
    defaults.update(overrides)
    return ExtractionJob.model_validate(defaults)


def _artifact(job_id: object) -> ExtractedArtifact:
    return ExtractedArtifact(
        artifact_id=derive_artifact_id(
            job_id=job_id,
            artifact_kind=ExtractionCapability.PLAIN_TEXT,
            content_hash=None,
            content_locator="candidate://x",
        ),
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_locator="candidate://x",
        created_at=_NOW,
        provenance=ExtractionArtifactProvenance(
            job_id=job_id,
            inventory_source_id=uuid4(),
            source_version_id=uuid4(),
            extractor_name="fake-extractor",
            extractor_version="0.0.0-test",
            extracted_at=_NOW,
        ),
    )


def test_configured_success_is_returned() -> None:
    extractor = FakeExtractor()
    job = _job()
    artifact = _artifact(job.job_id)
    extractor.configure_success(job.job_id, artifact)

    result = extractor.extract(job)
    assert result.artifact == artifact
    assert result.failure is None
    assert extractor.call_count == 1


def test_configured_retryable_failure() -> None:
    extractor = FakeExtractor()
    job = _job()
    extractor.configure_retryable_failure(job.job_id, error_code="timeout", error_message="slow")
    result = extractor.extract(job)
    assert result.artifact is None
    assert result.failure is not None
    assert result.failure.retryable is True


def test_configured_terminal_failure() -> None:
    extractor = FakeExtractor()
    job = _job()
    extractor.configure_terminal_failure(job.job_id, error_code="corrupt", error_message="bad")
    result = extractor.extract(job)
    assert result.failure is not None
    assert result.failure.retryable is False


def test_unconfigured_job_raises() -> None:
    extractor = FakeExtractor()
    job = _job()
    with pytest.raises(LookupError):
        extractor.extract(job)


def test_call_count_tracks_every_call() -> None:
    extractor = FakeExtractor()
    job = _job()
    extractor.configure_success(job.job_id, _artifact(job.job_id))
    extractor.extract(job)
    extractor.extract(job)
    assert extractor.call_count == 2
    assert extractor.calls == [job, job]


def test_fake_extractor_performs_no_file_reads(tmp_path: object) -> None:
    import ast
    from pathlib import Path

    from personal_lms.extraction import fake as fake_module

    source = Path(fake_module.__file__).read_text()
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "open" not in calls
    assert "socket" not in calls


def test_fake_extractor_module_has_no_subprocess_or_network_imports() -> None:
    import ast
    from pathlib import Path

    from personal_lms.extraction import fake as fake_module

    source = Path(fake_module.__file__).read_text()
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
    for forbidden in ("subprocess", "socket", "httpx", "requests", "urllib"):
        assert forbidden not in imported_roots
