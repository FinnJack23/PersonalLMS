"""Cross-process hash determinism (review finding #1).

A canonical hash that depends on ``PYTHONHASHSEED`` is not canonical. The
first implementation dumped Pydantic models with ``mode="json"`` *before*
canonicalizing, so a ``frozenset`` field reached the encoder as an
already-ordered list whose order came from Python's per-process string
hash randomization. Every "reproducible index hash" claim built on top of
that was false.

These tests run real subprocesses with explicit seeds, because the bug is
invisible inside one process: a single interpreter has one seed, so
in-process repetition always agrees with itself.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

SEEDS = ("0", "1", "2", "3")


def hash_under_seed(program: str, seed: str) -> str:
    """Run ``program`` in a fresh interpreter with ``PYTHONHASHSEED=seed``."""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(program)],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
    )
    return completed.stdout.strip()


ARTIFACT_PROGRAM = """
    from personal_lms.domain.objective_packs import PermittedUse, SourceArtifactRef
    from personal_lms.domain.source_inventory import SourceRightsStatus
    from personal_lms.objective_packs.hashing import hash_record

    artifact = SourceArtifactRef(
        source_id="src-1",
        sha256="a" * 64,
        media_type="application/pdf",
        size_bytes=64,
        title="Synthetic draft fixture",
        rights_status=SourceRightsStatus.OWNED,
        permitted_uses=frozenset(
            {
                PermittedUse.LOCAL_TEACH,
                PermittedUse.LOCAL_EXTRACT,
                PermittedUse.LOCAL_INDEX,
                PermittedUse.DERIVED_ITEM,
                PermittedUse.PORTFOLIO_DEMO,
            }
        ),
    )
    print(hash_record(artifact))
"""

POLICY_PROGRAM = """
    from personal_lms.domain.objective_packs import MasteryPolicy, ObjectiveFacet
    from personal_lms.objective_packs.hashing import hash_record

    policy = MasteryPolicy(
        policy_id="p",
        policy_version="1.0",
        baseline_item_count=12,
        maximum_followup_items=6,
        exit_probe_item_count=1,
        required_facets=frozenset(
            {
                ObjectiveFacet.CONCEPT,
                ObjectiveFacet.CLI_CONFIGURATION,
                ObjectiveFacet.NOVEL_TRANSFER,
                ObjectiveFacet.TOPOLOGY_REASONING,
            }
        ),
    )
    print(hash_record(policy))
"""

SET_PROGRAM = """
    from personal_lms.objective_packs.hashing import hash_record

    print(hash_record({"uses": frozenset({"e", "d", "c", "b", "a", "f", "g", "h"})}))
"""


@pytest.mark.parametrize(
    ("program", "label"),
    [
        (ARTIFACT_PROGRAM, "SourceArtifactRef.permitted_uses"),
        (POLICY_PROGRAM, "MasteryPolicy.required_facets"),
        (SET_PROGRAM, "a bare frozenset"),
    ],
    ids=["permitted_uses", "required_facets", "bare_frozenset"],
)
def test_hashes_agree_across_hash_seeds(program: str, label: str) -> None:
    """The same logical record must hash identically in every process."""
    digests = {seed: hash_under_seed(program, seed) for seed in SEEDS}

    assert len(set(digests.values())) == 1, (
        f"{label} hashes differently across PYTHONHASHSEED values: {digests}"
    )


def test_a_frozenset_field_is_not_order_dependent_in_process() -> None:
    """Two equal frozensets built in different insertion orders agree."""
    from personal_lms.objective_packs.hashing import hash_record

    forward = hash_record({"uses": frozenset(["a", "b", "c", "d"])})
    reverse = hash_record({"uses": frozenset(["d", "c", "b", "a"])})

    assert forward == reverse


def test_enums_datetimes_uuids_and_decimals_normalize_stably() -> None:
    """Every value kind the domain actually uses must canonicalize."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import UUID

    from personal_lms.domain.objective_packs import ObjectiveFacet
    from personal_lms.objective_packs.hashing import canonical_json

    rendered = canonical_json(
        {
            "facet": ObjectiveFacet.CONCEPT,
            "at": datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
            "id": UUID("b1d4c0a6-3f27-4a19-9c5e-2d8f7a6b4013"),
            "amount": Decimal("1.50"),
        }
    )

    assert '"concept"' in rendered
    assert "2026-07-27T12:00:00+00:00" in rendered
    assert "b1d4c0a6-3f27-4a19-9c5e-2d8f7a6b4013" in rendered
    assert '"1.50"' in rendered


def test_an_unsupported_value_is_refused_rather_than_repr_hashed() -> None:
    """Hashing an object's address would make the digest meaningless."""
    from personal_lms.objective_packs.hashing import canonical_json

    with pytest.raises(TypeError):
        canonical_json({"handle": object()})
