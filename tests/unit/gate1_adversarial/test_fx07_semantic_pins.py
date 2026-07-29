"""G1-FX-07 semantic-pin admission tests.

The frozen fixture tree is never edited. Negative cases copy it, change the
minimum relevant bytes, and re-pin those bytes only when the test needs to
reach semantic validation beyond the raw-hash boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from personal_lms.objective_packs.errors import (
    PackHashMismatchError,
    PackManifestError,
    PackSchemaError,
)
from personal_lms.objective_packs.linchpin_fixture import (
    FixtureExtensions,
    compute_manifest_self_hash,
    load_frozen_fixture,
)
from personal_lms.objective_packs.loader import PackFileReader, PackLoadResult

pytest.importorskip("yaml", reason="requires the ccna-lab extra (uv sync --extra ccna-lab)")

PROJECT_ROOT = Path(__file__).parents[3]
LINCHPIN_ROOT = PROJECT_ROOT / "tests" / "linchpin"
SCENARIO_PATH = "packs/objective-2.2/scenario-trunk-native-vlan-mismatch.yaml"
STARTING_STATE_SHA256 = "1a690fc8dd23e944db746347732a5e3b063baad7dac05290e964946c90e93f90"
TARGET_STATE_SHA256 = "6a7d2785a9765a2e953ff6f64925bf4d3084cfe3475b35b3608d9f86ae820b38"


def _load(root: Path, directory: str = "linchpin") -> PackLoadResult:
    return load_frozen_fixture(PackFileReader(roots=[root]), fixture_directory=directory)


@pytest.fixture
def copied_tree(tmp_path: Path) -> Path:
    shutil.copytree(LINCHPIN_ROOT, tmp_path / "linchpin")
    return tmp_path


def test_frozen_tree_exposes_all_p0_semantic_pins() -> None:
    result = load_frozen_fixture(
        PackFileReader(roots=[PROJECT_ROOT]), fixture_directory="tests/linchpin"
    )
    extensions = result.fixture_extensions
    assert isinstance(extensions, FixtureExtensions)

    by_id = {pin.learner_vector_id: pin for pin in extensions.scripted_learner_pins}
    assert tuple(by_id) == ("clean-pass", "native-gap", "ambiguous", "injection")
    assert {learner_id: pin.expected_overall_m for learner_id, pin in by_id.items()} == {
        "clean-pass": "100.00",
        "native-gap": "82.83",
        "ambiguous": None,
        "injection": "100.00",
    }
    assert all(len(pin.baseline_responses) == 12 for pin in by_id.values())
    assert by_id["native-gap"].expected_followup_trigger_codes == (
        "native_vlan_behavior_error",
        "verification_one_sided",
    )
    assert by_id["ambiguous"].baseline_responses[1].expected_disposition == ("review_required")
    assert by_id["ambiguous"].baseline_responses[1].expected_reason_codes == (
        "response_too_vague_to_score",
    )
    assert by_id["injection"].must_equal_learner_vector_id == "clean-pass"
    assert (
        by_id["injection"].expected_facet_derivation_sha256
        == by_id["clean-pass"].expected_facet_derivation_sha256
    )
    assert by_id["injection"].response_vector_sha256 != by_id["clean-pass"].response_vector_sha256
    assert all(
        pin.raw_sha256 == result.verified_file_hashes[pin.relative_path] for pin in by_id.values()
    )

    scenario = extensions.scenario_state_hash_pins
    assert scenario is not None
    assert scenario.relative_path == SCENARIO_PATH
    assert scenario.starting_state_sha256 == STARTING_STATE_SHA256
    assert scenario.target_repaired_state_sha256 == TARGET_STATE_SHA256
    assert scenario.raw_sha256 == result.verified_file_hashes[SCENARIO_PATH]
    assert all(
        pin.cli_expected_final_state_sha256 == scenario.target_repaired_state_sha256
        for pin in by_id.values()
    )

    assert {
        pin.profile: (pin.provider_ids, pin.offline_only, pin.allow_domain_result_writes)
        for pin in extensions.allowed_profile_provider_pins
    } == {
        "test": (("fake-deterministic",), True, None),
        "live_local": (("ollama-qwen-local",), True, None),
        "smoke_local_ungraded": (("ollama-qwen-local",), True, False),
    }
    assert extensions.hosted_profiles_enabled == ()
    assert extensions.hosted_spend_ceiling_usd == "0.00"


def test_unrepinned_malformed_learner_fails_at_raw_hash_before_json_decode(
    copied_tree: Path,
) -> None:
    learner = copied_tree / "linchpin" / "learners" / "clean-pass.json"
    learner.write_bytes(b"{not-json")

    with pytest.raises(PackHashMismatchError):
        _load(copied_tree)


def test_repinned_duplicate_json_key_fails_closed(copied_tree: Path) -> None:
    learner = copied_tree / "linchpin" / "learners" / "clean-pass.json"
    text = learner.read_text(encoding="utf-8").replace(
        '  "status": "candidate_pending_technical_review",',
        '  "learner_vector_id": "duplicate",\n  "status": "candidate_pending_technical_review",',
        1,
    )
    learner.write_text(text, encoding="utf-8")
    _repin(_manifest(copied_tree), "learners/clean-pass.json", learner)

    with pytest.raises(PackManifestError, match="strict, well-formed JSON"):
        _load(copied_tree)


def test_duplicate_learner_ids_fail_closed_after_manifest_body_crosscheck(
    copied_tree: Path,
) -> None:
    learner = copied_tree / "linchpin" / "learners" / "native-gap.json"
    document = _read_json(learner)
    document["learner_vector_id"] = "clean-pass"
    _write_json(learner, document)

    manifest = _manifest(copied_tree)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "{learner_vector_id: native-gap,", "{learner_vector_id: clean-pass,", 1
        ),
        encoding="utf-8",
    )
    _repin(manifest, "learners/native-gap.json", learner)

    with pytest.raises(PackManifestError, match="scripted learner vector ids must be unique"):
        _load(copied_tree)


def test_manifest_summary_score_drift_fails_closed(copied_tree: Path) -> None:
    manifest = _manifest(copied_tree)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'expected_overall_M: "82.83"', 'expected_overall_M: "82.84"', 1
        ),
        encoding="utf-8",
    )
    _refresh_self_hash(manifest)

    with pytest.raises(PackManifestError, match="overall score.*disagrees"):
        _load(copied_tree)


def test_byte_verified_learner_outcome_drift_from_summary_fails_closed(
    copied_tree: Path,
) -> None:
    learner = copied_tree / "linchpin" / "learners" / "clean-pass.json"
    document = _read_json(learner)
    _mapping(document, "expected_outcome")["achievement_status"] = "retained_mastery"
    _write_json(learner, document)
    _repin(_manifest(copied_tree), "learners/clean-pass.json", learner)

    with pytest.raises(PackManifestError, match="outcome statuses disagree"):
        _load(copied_tree)


def test_injection_authority_projection_drift_fails_even_when_summary_matches(
    copied_tree: Path,
) -> None:
    learner = copied_tree / "linchpin" / "learners" / "injection.json"
    document = _read_json(learner)
    _mapping(document, "expected_outcome")["review_status"] = "none"
    _write_json(learner, document)

    manifest = _manifest(copied_tree)
    text = manifest.read_text(encoding="utf-8")
    old_line = next(line for line in text.splitlines() if "learner_vector_id: injection" in line)
    new_line = old_line.replace("expected_review_status: scheduled", "expected_review_status: none")
    assert new_line != old_line
    manifest.write_text(text.replace(old_line, new_line, 1), encoding="utf-8")
    _repin(manifest, "learners/injection.json", learner)

    with pytest.raises(PackManifestError, match="authority projection drifts"):
        _load(copied_tree)


def test_followup_selection_drift_from_trigger_mapping_fails_closed(
    copied_tree: Path,
) -> None:
    learner = copied_tree / "linchpin" / "learners" / "native-gap.json"
    document = _read_json(learner)
    selection = _mapping(document, "expected_followup_selection")
    selection["approved_item_ids"] = list(reversed(selection["approved_item_ids"]))
    _write_json(learner, document)
    _repin(_manifest(copied_tree), "learners/native-gap.json", learner)

    with pytest.raises(PackManifestError, match="do not exactly implement"):
        _load(copied_tree)


def test_cli_grade_total_drift_fails_closed(copied_tree: Path) -> None:
    learner = copied_tree / "linchpin" / "learners" / "clean-pass.json"
    document = _read_json(learner)
    cli = _mapping(document, "cli_attempt")
    _mapping(cli, "expected_lab_grade")["total"] = 99
    _write_json(learner, document)
    _repin(_manifest(copied_tree), "learners/clean-pass.json", learner)

    with pytest.raises(PackManifestError, match="total does not equal"):
        _load(copied_tree)


def test_malformed_response_disposition_fails_closed(copied_tree: Path) -> None:
    learner = copied_tree / "linchpin" / "learners" / "clean-pass.json"
    document = _read_json(learner)
    responses = document["baseline_responses"]
    assert isinstance(responses, list)
    first = responses[0]
    assert isinstance(first, dict)
    first["expected_disposition"] = "auto_approved"
    _write_json(learner, document)
    _repin(_manifest(copied_tree), "learners/clean-pass.json", learner)

    with pytest.raises(PackSchemaError, match="expected_disposition"):
        _load(copied_tree)


def test_scenario_target_drift_from_executed_equivalence_cases_fails_closed(
    copied_tree: Path,
) -> None:
    scenario = copied_tree / "linchpin" / SCENARIO_PATH
    scenario.write_text(
        scenario.read_text(encoding="utf-8").replace(
            f"target_repaired_state_sha256: {TARGET_STATE_SHA256}",
            f"target_repaired_state_sha256: {'a' * 64}",
            1,
        ),
        encoding="utf-8",
    )
    _repin(_manifest(copied_tree), SCENARIO_PATH, scenario)

    with pytest.raises(PackManifestError, match="not the scenario target"):
        _load(copied_tree)


def test_scenario_start_hash_is_recomputed_from_initial_state(copied_tree: Path) -> None:
    scenario = copied_tree / "linchpin" / SCENARIO_PATH
    text = scenario.read_text(encoding="utf-8")
    changed = text.replace(
        "          native_vlan: 1\n          allowed_vlans: [10, 99]",
        "          native_vlan: 2\n          allowed_vlans: [10, 99]",
        1,
    )
    assert changed != text
    scenario.write_text(changed, encoding="utf-8")
    _repin(_manifest(copied_tree), SCENARIO_PATH, scenario)

    with pytest.raises(PackManifestError, match="canonical initial_state hash"):
        _load(copied_tree)


def test_duplicate_allowed_profile_fails_closed(copied_tree: Path) -> None:
    manifest = _manifest(copied_tree)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("{profile: live_local,", "{profile: test,", 1),
        encoding="utf-8",
    )
    _refresh_self_hash(manifest)

    with pytest.raises(PackManifestError, match="allowed execution profiles must be unique"):
        _load(copied_tree)


def _manifest(root: Path) -> Path:
    return root / "linchpin" / "fixture-manifest.yaml"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _mapping(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document[key]
    assert isinstance(value, dict)
    return value


def _repin(manifest: Path, relative_path: str, changed: Path) -> None:
    text = manifest.read_text(encoding="utf-8")
    digest = hashlib.sha256(changed.read_bytes()).hexdigest()
    text, count = re.subn(
        rf'(- \{{path: "{re.escape(relative_path)}", sha256: )[0-9a-f]{{64}}',
        rf"\g<1>{digest}",
        text,
    )
    assert count == 1
    manifest.write_text(text, encoding="utf-8")
    _refresh_self_hash(manifest)


def _refresh_self_hash(manifest: Path) -> None:
    text = manifest.read_text(encoding="utf-8")
    updated = compute_manifest_self_hash(text.encode("utf-8"))
    text, count = re.subn(r"(manifest_self_sha256: )[0-9a-f]{64}", rf"\g<1>{updated}", text)
    assert count == 1
    manifest.write_text(text, encoding="utf-8")
