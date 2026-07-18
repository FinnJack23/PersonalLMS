from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from personal_lms.catalog import SourceSearchFilters, SourceSearchMode
from personal_lms.catalog.sqlite import SQLiteSourceCatalog
from personal_lms.domain import (
    KnowledgeScope,
    PrivacyClassification,
    ProvenanceMetadata,
    SourceAssetRelationship,
    SourceProcessingStatus,
    SourceRecord,
    SourceRelationshipType,
    SourceType,
)

_VALID_SHA256 = "a" * 64


def _record(**overrides: object) -> SourceRecord:
    defaults: dict[str, object] = {
        "source_id": "src-00000001",
        "source_type": SourceType.IMAGE,
        "original_location": "archive/screenshots/img001.png",
        "filename": "img001.png",
        "mime_type": "image/png",
        "sha256_hash": _VALID_SHA256,
        "byte_size": 204_800,
    }
    defaults.update(overrides)
    return SourceRecord.model_validate(defaults)


@pytest.fixture
def catalog(tmp_path: Path) -> SQLiteSourceCatalog:
    store = SQLiteSourceCatalog.open(tmp_path / "catalog.sqlite3")
    store.initialize_schema()
    return store


# --- schema initialization --------------------------------------------------


def test_schema_initialization_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteSourceCatalog.open(tmp_path / "catalog.sqlite3")
    store.initialize_schema()
    store.initialize_schema()  # must not raise

    store.upsert_source(_record())
    assert store.get_source("src-00000001") is not None
    store.close()


def test_schema_initialization_does_not_wipe_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    store = SQLiteSourceCatalog.open(path)
    store.initialize_schema()
    store.upsert_source(_record())
    store.close()

    reopened = SQLiteSourceCatalog.open(path)
    reopened.initialize_schema()
    assert reopened.get_source("src-00000001") is not None
    reopened.close()


# --- insert / get / list -----------------------------------------------


def test_get_source_returns_none_when_absent(catalog: SQLiteSourceCatalog) -> None:
    assert catalog.get_source("does-not-exist") is None


def test_upsert_then_get_returns_equal_record(catalog: SQLiteSourceCatalog) -> None:
    record = _record()
    catalog.upsert_source(record)

    fetched = catalog.get_source("src-00000001")

    assert fetched == record


def test_list_sources_returns_all_upserted_records(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(_record(source_id="src-1"))
    catalog.upsert_source(_record(source_id="src-2"))

    sources = catalog.list_sources()

    assert {s.source_id for s in sources} == {"src-1", "src-2"}


def test_list_sources_empty_catalog_returns_empty_tuple(catalog: SQLiteSourceCatalog) -> None:
    assert catalog.list_sources() == ()


# --- JSON fidelity -----------------------------------------------------


def test_json_round_trip_preserves_nested_and_optional_fields(
    catalog: SQLiteSourceCatalog,
) -> None:
    record = _record(
        source_id="src-round-trip",
        privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        status=SourceProcessingStatus.CATALOGED,
        knowledge_scopes=[
            KnowledgeScope(certification="CCNA", objective_framework="1.1.a"),
            KnowledgeScope(course="D419"),
        ],
        provenance=ProvenanceMetadata(imported_by="alan", acquisition_note="scanned batch 3"),
    )
    catalog.upsert_source(record)

    fetched = catalog.get_source("src-round-trip")

    assert fetched == record
    assert fetched is not None
    assert fetched.knowledge_scopes[0].certification == "CCNA"
    assert fetched.provenance.acquisition_note == "scanned batch 3"


# --- duplicate source handling -------------------------------------------


def test_upsert_with_same_source_id_replaces_the_row(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(_record(filename="first.png"))
    catalog.upsert_source(_record(filename="second.png", status=SourceProcessingStatus.CATALOGED))

    fetched = catalog.get_source("src-00000001")
    all_sources = catalog.list_sources()

    assert fetched is not None
    assert fetched.filename == "second.png"
    assert fetched.status == SourceProcessingStatus.CATALOGED
    assert len(all_sources) == 1


def test_content_duplicate_sources_persist_as_separate_records(
    catalog: SQLiteSourceCatalog,
) -> None:
    """Two different source_ids sharing the same sha256_hash (a genuine
    content duplicate, not a primary-key collision) both persist
    independently; the duplication is recorded as a relationship, not a
    merge."""
    original = _record(source_id="src-original", sha256_hash=_VALID_SHA256)
    duplicate = _record(source_id="src-duplicate", sha256_hash=_VALID_SHA256)
    catalog.upsert_source(original)
    catalog.upsert_source(duplicate)
    catalog.add_relationship(
        SourceAssetRelationship(
            source_id="src-duplicate",
            related_source_id="src-original",
            relationship_type=SourceRelationshipType.DUPLICATE_OF,
        )
    )

    assert catalog.get_source("src-original") == original
    assert catalog.get_source("src-duplicate") == duplicate
    assert len(catalog.list_sources()) == 2
    relationships = catalog.list_relationships("src-duplicate")
    assert len(relationships) == 1
    assert relationships[0].relationship_type == SourceRelationshipType.DUPLICATE_OF


# --- relationship persistence -------------------------------------------


def test_relationship_round_trip(catalog: SQLiteSourceCatalog) -> None:
    relationship = SourceAssetRelationship(
        source_id="src-pdf",
        related_source_id="src-page-001",
        relationship_type=SourceRelationshipType.RECONSTRUCTED_FROM,
        note="page 1 of reconstructed PDF",
    )
    catalog.add_relationship(relationship)

    from_subject = catalog.list_relationships("src-pdf")
    from_object = catalog.list_relationships("src-page-001")

    assert from_subject == (relationship,)
    assert from_object == (relationship,)


def test_list_relationships_returns_empty_tuple_when_none_exist(
    catalog: SQLiteSourceCatalog,
) -> None:
    assert catalog.list_relationships("src-lonely") == ()


def test_add_relationship_with_same_id_replaces_the_row(catalog: SQLiteSourceCatalog) -> None:
    relationship_id = uuid4()
    catalog.add_relationship(
        SourceAssetRelationship(
            relationship_id=relationship_id,
            source_id="src-1",
            related_source_id="src-2",
            relationship_type=SourceRelationshipType.DUPLICATE_OF,
        )
    )
    catalog.add_relationship(
        SourceAssetRelationship(
            relationship_id=relationship_id,
            source_id="src-1",
            related_source_id="src-2",
            relationship_type=SourceRelationshipType.SUPERSEDES,
        )
    )

    relationships = catalog.list_relationships("src-1")
    assert len(relationships) == 1
    assert relationships[0].relationship_type == SourceRelationshipType.SUPERSEDES


# --- FTS keyword search: mode behavior --------------------------------


def test_all_terms_mode_matches_non_adjacent_multiword_metadata(
    catalog: SQLiteSourceCatalog,
) -> None:
    """All four query words are present in the record's metadata, but
    scattered — never adjacent, never in query order. ALL_TERMS (the
    default) must still match: it only requires every term to appear
    somewhere, not in sequence."""
    catalog.upsert_source(
        _record(
            source_id="src-scattered",
            filename="notes.png",
            provenance=ProvenanceMetadata(
                acquisition_note=(
                    "ip addressing notes, neighbor discovery, ospf basics, show commands"
                )
            ),
        )
    )

    hits = catalog.search("show ip ospf neighbor")

    assert [hit.source_id for hit in hits] == ["src-scattered"]


def test_exact_phrase_mode_does_not_match_the_same_scattered_record(
    catalog: SQLiteSourceCatalog,
) -> None:
    """The exact same record as above never matches under EXACT_PHRASE:
    the four words never appear as one adjacent, ordered sequence."""
    catalog.upsert_source(
        _record(
            source_id="src-scattered",
            filename="notes.png",
            provenance=ProvenanceMetadata(
                acquisition_note=(
                    "ip addressing notes, neighbor discovery, ospf basics, show commands"
                )
            ),
        )
    )

    hits = catalog.search("show ip ospf neighbor", mode=SourceSearchMode.EXACT_PHRASE)

    assert hits == ()


def test_exact_phrase_mode_matches_an_exact_command_phrase(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-cli",
            filename="show-ip-ospf-neighbor.png",
            provenance=ProvenanceMetadata(acquisition_note="output of show ip ospf neighbor"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-unrelated",
            filename="unrelated.png",
            provenance=ProvenanceMetadata(acquisition_note="ip routing overview slide"),
        )
    )

    hits = catalog.search("show ip ospf neighbor", mode=SourceSearchMode.EXACT_PHRASE)

    assert [hit.source_id for hit in hits] == ["src-cli"]


def test_all_terms_mode_also_matches_the_exact_command_record(
    catalog: SQLiteSourceCatalog,
) -> None:
    """The default mode is a superset for this fixture: a record that
    satisfies the exact phrase necessarily satisfies "all terms present"
    too, and the unrelated record (missing three of the four words)
    still does not match."""
    catalog.upsert_source(
        _record(
            source_id="src-cli",
            filename="show-ip-ospf-neighbor.png",
            provenance=ProvenanceMetadata(acquisition_note="output of show ip ospf neighbor"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-unrelated",
            filename="unrelated.png",
            provenance=ProvenanceMetadata(acquisition_note="ip routing overview slide"),
        )
    )

    hits = catalog.search("show ip ospf neighbor")

    assert [hit.source_id for hit in hits] == ["src-cli"]


def test_search_matches_acronym(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-ospf",
            filename="ospf-overview.png",
            knowledge_scopes=[KnowledgeScope(topic="OSPF")],
        )
    )
    catalog.upsert_source(_record(source_id="src-other", filename="bgp-overview.png"))

    hits = catalog.search("OSPF")

    assert [hit.source_id for hit in hits] == ["src-ospf"]


def test_search_matches_error_message(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-error",
            filename="error-screenshot.png",
            provenance=ProvenanceMetadata(acquisition_note="destination host unreachable"),
        )
    )

    hits = catalog.search("destination host unreachable")

    assert [hit.source_id for hit in hits] == ["src-error"]


def test_search_hits_are_typed_with_source_id_record_score_and_snippet(
    catalog: SQLiteSourceCatalog,
) -> None:
    record = _record(
        source_id="src-typed",
        provenance=ProvenanceMetadata(acquisition_note="GigabitEthernet0/0/1 configuration"),
    )
    catalog.upsert_source(record)

    hits = catalog.search("GigabitEthernet0/0/1")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.source_id == "src-typed"
    assert hit.record == record
    assert isinstance(hit.score, float)
    assert hit.snippet is not None
    assert "GigabitEthernet0/0/1" in hit.snippet


# --- FTS keyword search: technical identifiers ----------------------------


def test_search_matches_exact_ipv4_address(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-ip-a",
            filename="router-config-a.png",
            provenance=ProvenanceMetadata(acquisition_note="interface configured with 192.168.1.1"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-ip-b",
            filename="router-config-b.png",
            provenance=ProvenanceMetadata(acquisition_note="interface configured with 192.168.1.2"),
        )
    )

    hits = catalog.search("192.168.1.1")

    assert [hit.source_id for hit in hits] == ["src-ip-a"]


def test_dotted_ipv4_does_not_match_hyphenated_variant(catalog: SQLiteSourceCatalog) -> None:
    """192.168.1.1 and 192-168-1-1 must be distinct tokens, not the same
    digits with different separators tokenized away."""
    catalog.upsert_source(
        _record(
            source_id="src-hyphenated",
            provenance=ProvenanceMetadata(
                acquisition_note="legacy notation for the address 192-168-1-1"
            ),
        )
    )

    hits = catalog.search("192.168.1.1")

    assert hits == ()


def test_search_matches_exact_ipv6_address(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-ipv6-a",
            provenance=ProvenanceMetadata(acquisition_note="loopback address 2001:db8::1"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-ipv6-b",
            provenance=ProvenanceMetadata(acquisition_note="loopback address 2001:db8::2"),
        )
    )

    hits = catalog.search("2001:db8::1")

    assert [hit.source_id for hit in hits] == ["src-ipv6-a"]


def test_search_matches_cidr_notation(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-cidr-24",
            provenance=ProvenanceMetadata(acquisition_note="subnet is 10.0.0.0/24"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-cidr-16",
            provenance=ProvenanceMetadata(acquisition_note="subnet is 10.0.0.0/16"),
        )
    )

    hits = catalog.search("10.0.0.0/24")

    assert [hit.source_id for hit in hits] == ["src-cidr-24"]


def test_search_matches_interface_name_with_slashes(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-gig-1",
            provenance=ProvenanceMetadata(acquisition_note="GigabitEthernet0/0/1 is up"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-gig-2",
            provenance=ProvenanceMetadata(acquisition_note="GigabitEthernet0/0/2 is up"),
        )
    )

    hits = catalog.search("GigabitEthernet0/0/1")

    assert [hit.source_id for hit in hits] == ["src-gig-1"]


# --- filters --------------------------------------------------------------


def test_list_sources_filters_by_source_type(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(_record(source_id="src-image", source_type=SourceType.IMAGE))
    catalog.upsert_source(_record(source_id="src-pdf", source_type=SourceType.PDF))

    results = catalog.list_sources(filters=SourceSearchFilters(source_type=SourceType.PDF))

    assert [r.source_id for r in results] == ["src-pdf"]


def test_list_sources_filters_by_status_and_privacy(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-restricted",
            status=SourceProcessingStatus.APPROVED,
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-public",
            status=SourceProcessingStatus.APPROVED,
            privacy_classification=PrivacyClassification.PUBLIC,
        )
    )

    results = catalog.list_sources(
        filters=SourceSearchFilters(
            status=SourceProcessingStatus.APPROVED,
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        )
    )

    assert [r.source_id for r in results] == ["src-restricted"]


def test_list_sources_filters_by_knowledge_scope_fields(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-ccna",
            knowledge_scopes=[
                KnowledgeScope(
                    certification="CCNA",
                    course="D419",
                    topic="routing",
                    knowledge_domain="networking",
                )
            ],
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-aplus",
            knowledge_scopes=[KnowledgeScope(certification="A+", course="D220")],
        )
    )

    by_cert = catalog.list_sources(filters=SourceSearchFilters(certification="CCNA"))
    by_course = catalog.list_sources(filters=SourceSearchFilters(course="D220"))
    by_topic = catalog.list_sources(filters=SourceSearchFilters(topic="routing"))
    by_domain = catalog.list_sources(filters=SourceSearchFilters(knowledge_domain="networking"))

    assert [r.source_id for r in by_cert] == ["src-ccna"]
    assert [r.source_id for r in by_course] == ["src-aplus"]
    assert [r.source_id for r in by_topic] == ["src-ccna"]
    assert [r.source_id for r in by_domain] == ["src-ccna"]


def test_list_sources_filters_by_objective_framework(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-with-objective",
            knowledge_scopes=[KnowledgeScope(objective_framework="1.1.a")],
        )
    )
    catalog.upsert_source(_record(source_id="src-without-objective"))

    results = catalog.list_sources(filters=SourceSearchFilters(objective_framework="1.1.a"))

    assert [r.source_id for r in results] == ["src-with-objective"]


def test_search_combines_query_with_filters(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-match-type",
            source_type=SourceType.PDF,
            provenance=ProvenanceMetadata(acquisition_note="OSPF DR election overview"),
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-wrong-type",
            source_type=SourceType.IMAGE,
            provenance=ProvenanceMetadata(acquisition_note="OSPF DR election overview"),
        )
    )

    hits = catalog.search(
        "OSPF DR election", filters=SourceSearchFilters(source_type=SourceType.PDF)
    )

    assert [hit.source_id for hit in hits] == ["src-match-type"]


# --- no-result behavior -------------------------------------------------


def test_search_with_no_matches_returns_empty_tuple(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(_record())

    hits = catalog.search("nonexistent phrase that matches nothing")

    assert hits == ()


def test_list_sources_with_no_matching_filter_returns_empty_tuple(
    catalog: SQLiteSourceCatalog,
) -> None:
    catalog.upsert_source(_record(source_type=SourceType.IMAGE))

    results = catalog.list_sources(filters=SourceSearchFilters(source_type=SourceType.VIDEO))

    assert results == ()


def test_search_with_blank_query_returns_empty_tuple_in_both_modes(
    catalog: SQLiteSourceCatalog,
) -> None:
    catalog.upsert_source(_record())

    assert catalog.search("   ") == ()
    assert catalog.search("   ", mode=SourceSearchMode.EXACT_PHRASE) == ()


# --- SQL-injection-shaped input is treated as data --------------------------


def test_sql_injection_shaped_search_query_is_treated_as_literal_text(
    catalog: SQLiteSourceCatalog,
) -> None:
    catalog.upsert_source(_record())

    malicious_query = 'foo"; DROP TABLE source_records; --'
    hits = catalog.search(malicious_query)  # must not raise, must not drop anything

    assert hits == ()
    assert catalog.get_source("src-00000001") is not None


def test_sql_injection_shaped_search_query_is_harmless_in_exact_phrase_mode(
    catalog: SQLiteSourceCatalog,
) -> None:
    catalog.upsert_source(_record())

    malicious_query = 'foo"; DROP TABLE source_records; -- AND OR NOT *'
    hits = catalog.search(
        malicious_query, mode=SourceSearchMode.EXACT_PHRASE
    )  # must not raise, must not drop anything, must not be parsed as FTS5/SQL syntax

    assert hits == ()
    assert catalog.get_source("src-00000001") is not None


def test_sql_injection_shaped_field_values_are_stored_and_retrieved_as_data(
    catalog: SQLiteSourceCatalog,
) -> None:
    malicious_location = "archive/'; DROP TABLE source_records; --.png"
    record = _record(
        source_id="src-injection",
        original_location=malicious_location,
        filename="'; DROP TABLE source_records; --.png",
    )
    catalog.upsert_source(record)

    fetched = catalog.get_source("src-injection")

    assert fetched is not None
    assert fetched.original_location == malicious_location
    assert catalog.list_sources() != ()


def test_sql_injection_shaped_filter_value_is_treated_as_literal_data(
    catalog: SQLiteSourceCatalog,
) -> None:
    catalog.upsert_source(_record(knowledge_scopes=[KnowledgeScope(certification="CCNA")]))

    results = catalog.list_sources(filters=SourceSearchFilters(certification="CCNA' OR '1'='1"))

    assert results == ()
    assert catalog.get_source("src-00000001") is not None


# --- database close / lifecycle ------------------------------------------


def test_operations_after_close_raise(catalog: SQLiteSourceCatalog) -> None:
    catalog.upsert_source(_record())
    catalog.close()

    with pytest.raises(Exception):  # noqa: B017 - sqlite3.ProgrammingError on a closed connection
        catalog.get_source("src-00000001")


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    with SQLiteSourceCatalog.open(path) as store:
        store.initialize_schema()
        store.upsert_source(_record())
        assert store.get_source("src-00000001") is not None

    with pytest.raises(Exception):  # noqa: B017
        store.get_source("src-00000001")


def test_context_manager_closes_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    store: SQLiteSourceCatalog | None = None
    with pytest.raises(ValueError), SQLiteSourceCatalog.open(path) as opened:
        store = opened
        store.initialize_schema()
        raise ValueError("boom")

    assert store is not None
    with pytest.raises(Exception):  # noqa: B017
        store.get_source("anything")


def test_reopening_after_close_reads_persisted_data(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    store = SQLiteSourceCatalog.open(path)
    store.initialize_schema()
    store.upsert_source(_record())
    store.close()

    reopened = SQLiteSourceCatalog.open(path)
    reopened.initialize_schema()
    assert reopened.get_source("src-00000001") is not None
    reopened.close()
