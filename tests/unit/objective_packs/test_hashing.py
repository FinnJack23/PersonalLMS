"""Canonical hashing tests.

The rule under test is that a logical hash depends on logical content and
nothing else: not key order, not set iteration order, not process
identity, and never storage bytes.
"""

from __future__ import annotations

import pytest

from personal_lms.objective_packs.hashing import (
    canonical_bytes,
    canonical_json,
    hash_record,
    hash_records,
)

from ._helpers import make_pack, make_source


class TestCanonicalJson:
    def test_key_order_does_not_affect_the_canonical_form(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_set_iteration_order_does_not_affect_the_canonical_form(self) -> None:
        assert canonical_json({frozenset({"z", "a"})}) == canonical_json({frozenset({"a", "z"})})

    def test_sequence_order_is_preserved(self) -> None:
        assert canonical_json([1, 2]) != canonical_json([2, 1])

    def test_the_canonical_form_is_compact(self) -> None:
        assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'

    def test_non_ascii_content_survives_intact(self) -> None:
        assert "é" in canonical_json({"note": "é"})

    def test_pydantic_models_are_dumped_in_json_mode(self) -> None:
        rendered = canonical_json(make_source())

        assert '"application/pdf"' in rendered

    def test_a_non_serializable_value_raises_rather_than_hashing_its_address(self) -> None:
        with pytest.raises(TypeError):
            canonical_json({"handle": object()})

    def test_floats_are_refused_outright(self) -> None:
        """No canonical decimal form exists; the domain uses basis points."""
        with pytest.raises(TypeError, match="no canonical form"):
            canonical_json({"value": 1.5})

    def test_nan_is_refused(self) -> None:
        with pytest.raises(TypeError):
            canonical_json({"value": float("nan")})

    def test_canonical_bytes_is_utf8_of_canonical_json(self) -> None:
        payload = {"a": "é"}

        assert canonical_bytes(payload) == canonical_json(payload).encode("utf-8")


class TestHashRecord:
    def test_the_same_record_hashes_identically_across_calls(self) -> None:
        pack = make_pack()

        assert hash_record(pack) == hash_record(pack)

    def test_a_changed_field_changes_the_hash(self) -> None:
        assert hash_record(make_source()) != hash_record(make_source(source_id="src-other"))

    def test_the_hash_is_lowercase_hex_of_the_expected_width(self) -> None:
        digest = hash_record(make_source())

        assert len(digest) == 64
        assert digest == digest.lower()


class TestHashRecords:
    def test_sorted_hashing_is_order_independent(self) -> None:
        assert hash_records([{"a": 1}, {"b": 2}]) == hash_records([{"b": 2}, {"a": 1}])

    def test_unsorted_hashing_is_order_sensitive(self) -> None:
        first = hash_records([{"a": 1}, {"b": 2}], sort=False)
        second = hash_records([{"b": 2}, {"a": 1}], sort=False)

        assert first != second

    def test_concatenation_is_unambiguous(self) -> None:
        """Length prefixing stops ["ab","c"] colliding with ["a","bc"]."""
        assert hash_records(["ab", "c"], sort=False) != hash_records(["a", "bc"], sort=False)

    def test_an_empty_collection_has_a_stable_hash(self) -> None:
        assert hash_records([]) == hash_records([])

    def test_adding_a_record_changes_the_hash(self) -> None:
        assert hash_records([{"a": 1}]) != hash_records([{"a": 1}, {"b": 2}])
