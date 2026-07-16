from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from personal_lms.domain import RunState, RunStatus


def test_run_state_defaults() -> None:
    run = RunState(workflow_name="study-session-v1")
    assert isinstance(run.run_id, UUID)
    assert run.status == RunStatus.PENDING
    assert run.created_at.tzinfo is not None
    assert run.completed_steps == []
    assert run.pending_approval_ids == []


def test_run_state_mutable_defaults_are_isolated_between_instances() -> None:
    first = RunState(workflow_name="a")
    second = RunState(workflow_name="b")
    first.completed_steps.append("librarian")
    assert second.completed_steps == []


def test_run_state_rejects_negative_retry_count() -> None:
    with pytest.raises(ValidationError):
        RunState(workflow_name="a", retry_count=-1)


def test_run_state_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        RunState(workflow_name="a", status="on_fire")  # type: ignore[arg-type]


def test_run_state_json_round_trip() -> None:
    run = RunState(workflow_name="study-session-v1", completed_steps=["librarian", "tutor"])
    restored = RunState.model_validate_json(run.model_dump_json())
    assert restored == run
