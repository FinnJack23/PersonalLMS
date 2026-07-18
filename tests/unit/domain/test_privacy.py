from __future__ import annotations

import json

import pytest

from personal_lms.domain import PrivacyClassification


def test_privacy_classification_values() -> None:
    assert {member.value for member in PrivacyClassification} == {
        "public",
        "internal",
        "sensitive",
        "restricted_local_only",
    }


def test_restricted_local_only_is_a_valid_member() -> None:
    assert (
        PrivacyClassification("restricted_local_only")
        is PrivacyClassification.RESTRICTED_LOCAL_ONLY
    )


def test_invalid_privacy_value_rejected() -> None:
    with pytest.raises(ValueError):
        PrivacyClassification("top_secret")


def test_privacy_classification_is_json_serializable() -> None:
    dumped = json.dumps({"privacy": PrivacyClassification.RESTRICTED_LOCAL_ONLY})
    restored = json.loads(dumped)
    assert PrivacyClassification(restored["privacy"]) is PrivacyClassification.RESTRICTED_LOCAL_ONLY
