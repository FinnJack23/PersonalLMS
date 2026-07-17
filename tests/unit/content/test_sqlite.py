from __future__ import annotations

from pathlib import Path

import pytest

from personal_lms.catalog import SourceSearchMode
from personal_lms.content import (
    ChunkSearchFilters,
    ParentDocumentNotApprovedError,
    ParentDocumentNotFoundError,
    ParentSourceMismatchError,
    SQLiteContentRepository,
)
from personal_lms.domain import (
    ContentChunk,
    CorpusDocument,
    KnowledgeScope,
    PrivacyClassification,
    ProvenanceMetadata,
    SourceProcessingStatus,
)

_VALID_SHA256 = "a" * 64


def _document(**overrides: object) -> CorpusDocument:
    defaults: dict[str, object] = {
        "document_id": "doc-1",
        "source_id": "src-1",
        "title": "Routing Concepts Module 14",
        "content_hash": _VALID_SHA256,
        "status": SourceProcessingStatus.APPROVED,
    }
    defaults.update(overrides)
    return CorpusDocument.model_validate(defaults)


def _chunk(**overrides: object) -> ContentChunk:
    defaults: dict[str, object] = {
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "source_id": "src-1",
        "ordinal": 0,
        "text": "OSPF DR election overview",
        "text_hash": _VALID_SHA256,
        "status": SourceProcessingStatus.APPROVED,
    }
    defaults.update(overrides)
    return ContentChunk.model_validate(defaults)


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteContentRepository:
    store = SQLiteContentRepository.open(tmp_path / "content.sqlite3")
    store.initialize_schema()
    return store


# --- schema initialization --------------------------------------------------


def test_schema_initialization_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteContentRepository.open(tmp_path / "content.sqlite3")
    store.initialize_schema()
    store.initialize_schema()  # must not raise

    store.upsert_document(_document())
    assert store.get_document("doc-1") is not None
    store.close()


def test_schema_initialization_does_not_wipe_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "content.sqlite3"
    store = SQLiteContentRepository.open(path)
    store.initialize_schema()
    store.upsert_document(_document())
    store.close()

    reopened = SQLiteContentRepository.open(path)
    reopened.initialize_schema()
    assert reopened.get_document("doc-1") is not None
    reopened.close()


# --- document JSON round trip / insert / get / list -------------------------


def test_document_upsert_then_get_returns_equal_record(repo: SQLiteContentRepository) -> None:
    document = _document(
        language="en",
        knowledge_scopes=[KnowledgeScope(certification="CCNA")],
        provenance=ProvenanceMetadata(imported_by="alan"),
    )
    repo.upsert_document(document)

    fetched = repo.get_document("doc-1")

    assert fetched == document


def test_get_document_returns_none_when_absent(repo: SQLiteContentRepository) -> None:
    assert repo.get_document("does-not-exist") is None


def test_list_documents_returns_all(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))

    documents = repo.list_documents()

    assert {d.document_id for d in documents} == {"doc-1", "doc-2"}


def test_list_documents_filters_by_source_id(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))

    documents = repo.list_documents(source_id="src-1")

    assert [d.document_id for d in documents] == ["doc-1"]


def test_document_upsert_with_same_id_replaces_the_row(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(title="first title"))
    repo.upsert_document(_document(title="second title"))

    fetched = repo.get_document("doc-1")

    assert fetched is not None
    assert fetched.title == "second title"
    assert len(repo.list_documents()) == 1


# --- chunk JSON round trip / insert / get / list -----------------------------


def test_chunk_upsert_then_get_returns_equal_record(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    chunk = _chunk(
        page_number=12,
        section_title="OSPF DR Election",
        timestamp_start_seconds=10.0,
        timestamp_end_seconds=45.5,
        knowledge_scopes=[KnowledgeScope(certification="CCNA")],
    )
    repo.upsert_chunk(chunk)

    fetched = repo.get_chunk("chunk-1")

    assert fetched == chunk


def test_get_chunk_returns_none_when_absent(repo: SQLiteContentRepository) -> None:
    assert repo.get_chunk("does-not-exist") is None


def test_list_chunks_returns_all_in_document_ordinal_order(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-2", ordinal=1))
    repo.upsert_chunk(_chunk(chunk_id="chunk-1", ordinal=0))

    chunks = repo.list_chunks()

    assert [c.chunk_id for c in chunks] == ["chunk-1", "chunk-2"]


def test_chunk_upsert_with_same_id_replaces_the_row(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="first version"))
    repo.upsert_chunk(_chunk(text="second version"))

    fetched = repo.get_chunk("chunk-1")

    assert fetched is not None
    assert fetched.text == "second version"
    assert len(repo.list_chunks()) == 1


def test_different_chunk_ids_with_identical_text_coexist(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-1", text="duplicate passage"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-2", ordinal=1, text="duplicate passage"))

    chunks = repo.list_chunks()

    assert len(chunks) == 2
    assert {c.chunk_id for c in chunks} == {"chunk-1", "chunk-2"}


# --- parent/source mismatch rejection ----------------------------------------


def test_upsert_chunk_rejects_missing_parent_document(repo: SQLiteContentRepository) -> None:
    with pytest.raises(ParentDocumentNotFoundError):
        repo.upsert_chunk(_chunk())


def test_upsert_chunk_rejects_source_id_mismatch(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-parent"))

    with pytest.raises(ParentSourceMismatchError):
        repo.upsert_chunk(_chunk(document_id="doc-1", source_id="src-other"))


def test_upsert_chunk_succeeds_when_source_id_matches(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))

    repo.upsert_chunk(_chunk(document_id="doc-1", source_id="src-1"))  # must not raise

    assert repo.get_chunk("chunk-1") is not None


def test_failed_chunk_upsert_does_not_persist_partial_state(repo: SQLiteContentRepository) -> None:
    with pytest.raises(ParentDocumentNotFoundError):
        repo.upsert_chunk(_chunk())

    assert repo.get_chunk("chunk-1") is None
    assert repo.list_chunks() == ()


# --- parent approval gate ---------------------------------------------------


@pytest.mark.parametrize(
    "parent_status",
    [
        SourceProcessingStatus.RAW,
        SourceProcessingStatus.CANDIDATE,
        SourceProcessingStatus.REJECTED,
    ],
)
def test_trusted_chunk_rejected_when_parent_not_reviewed(
    repo: SQLiteContentRepository, parent_status: SourceProcessingStatus
) -> None:
    repo.upsert_document(_document(status=parent_status))

    with pytest.raises(ParentDocumentNotApprovedError):
        repo.upsert_chunk(_chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=True))


@pytest.mark.parametrize(
    "parent_status",
    [
        SourceProcessingStatus.APPROVED,
        SourceProcessingStatus.REVIEWED,
        SourceProcessingStatus.TRUSTED_FOR_RAG,
    ],
)
def test_trusted_chunk_accepted_when_parent_reviewed(
    repo: SQLiteContentRepository, parent_status: SourceProcessingStatus
) -> None:
    repo.upsert_document(_document(status=parent_status))

    repo.upsert_chunk(  # must not raise
        _chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=True)
    )

    fetched = repo.get_chunk("chunk-1")
    assert fetched is not None
    assert fetched.trusted_for_rag is True


def test_non_trusted_chunk_accepted_regardless_of_parent_status(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document(status=SourceProcessingStatus.RAW))

    repo.upsert_chunk(_chunk(trusted_for_rag=False))  # must not raise

    assert repo.get_chunk("chunk-1") is not None


def test_failed_trusted_chunk_upsert_does_not_persist_partial_state(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document(status=SourceProcessingStatus.RAW))

    with pytest.raises(ParentDocumentNotApprovedError):
        repo.upsert_chunk(_chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=True))

    assert repo.get_chunk("chunk-1") is None


# --- FTS search: exact terms and technical identifiers -----------------------


def test_search_matches_exact_cli_command(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="output of show ip ospf neighbor"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-2", ordinal=1, text="unrelated ip routing overview"))

    hits = repo.search("show ip ospf neighbor", mode=SourceSearchMode.EXACT_PHRASE)

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-1"]


def test_search_all_terms_matches_scattered_natural_language(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            text=("ip addressing notes, neighbor discovery, ospf basics, show commands overview")
        )
    )

    hits = repo.search("show ip ospf neighbor")  # default ALL_TERMS

    assert len(hits) == 1


def test_search_exact_phrase_rejects_scattered_natural_language(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            text=("ip addressing notes, neighbor discovery, ospf basics, show commands overview")
        )
    )

    hits = repo.search("show ip ospf neighbor", mode=SourceSearchMode.EXACT_PHRASE)

    assert hits == ()


def test_search_matches_ipv4_address(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", text="interface configured with 192.168.1.1"))
    repo.upsert_chunk(
        _chunk(chunk_id="chunk-b", ordinal=1, text="interface configured with 192.168.1.2")
    )

    hits = repo.search("192.168.1.1")

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-a"]


def test_search_ipv4_dotted_does_not_match_hyphenated_variant(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="legacy notation for the address 192-168-1-1"))

    hits = repo.search("192.168.1.1")

    assert hits == ()


def test_search_matches_ipv6_address(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", text="loopback address 2001:db8::1"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", ordinal=1, text="loopback address 2001:db8::2"))

    hits = repo.search("2001:db8::1")

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-a"]


def test_search_matches_cidr_notation(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", text="subnet is 10.0.0.0/24"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", ordinal=1, text="subnet is 10.0.0.0/16"))

    hits = repo.search("10.0.0.0/24")

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-a"]


def test_search_matches_interface_name_with_slashes(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", text="GigabitEthernet0/0/1 is up"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", ordinal=1, text="GigabitEthernet0/0/2 is up"))

    hits = repo.search("GigabitEthernet0/0/1")

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-a"]


def test_search_matches_section_title_metadata(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="generic content", section_title="OSPF DR Election"))

    hits = repo.search("OSPF DR Election")

    assert len(hits) == 1


# --- filters -------------------------------------------------------------


def test_search_filters_by_document_id(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", document_id="doc-1", source_id="src-1"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", document_id="doc-2", source_id="src-2", ordinal=1))

    hits = repo.search("OSPF", filters=ChunkSearchFilters(document_id="doc-1"))

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-a"]


def test_search_filters_by_source_id(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", document_id="doc-1", source_id="src-1"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", document_id="doc-2", source_id="src-2", ordinal=1))

    hits = repo.search("OSPF", filters=ChunkSearchFilters(source_id="src-2"))

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-b"]


def test_search_filters_by_status_and_privacy(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-restricted",
            status=SourceProcessingStatus.APPROVED,
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-public",
            ordinal=1,
            status=SourceProcessingStatus.APPROVED,
            privacy_classification=PrivacyClassification.PUBLIC,
        )
    )

    hits = repo.search(
        "OSPF",
        filters=ChunkSearchFilters(
            status=SourceProcessingStatus.APPROVED,
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        ),
    )

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-restricted"]


def test_search_filters_by_knowledge_scope_fields(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-ccna",
            knowledge_scopes=[KnowledgeScope(certification="CCNA", course="D419", topic="routing")],
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-aplus",
            ordinal=1,
            knowledge_scopes=[KnowledgeScope(certification="A+")],
        )
    )

    hits = repo.search("OSPF", filters=ChunkSearchFilters(certification="CCNA"))

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-ccna"]


def test_list_chunks_filters_by_document_id(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", document_id="doc-1", source_id="src-1"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", document_id="doc-2", source_id="src-2", ordinal=1))

    chunks = repo.list_chunks(filters=ChunkSearchFilters(document_id="doc-1"))

    assert [c.chunk_id for c in chunks] == ["chunk-a"]


# --- page/section/timestamp citation mapping ---------------------------------


def test_search_hit_citation_title_equals_parent_document_title(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document(title="Routing Concepts Module 14"))
    repo.upsert_chunk(
        _chunk(
            page_number=42,
            section_title="OSPF DR Election",
            status=SourceProcessingStatus.APPROVED,
        )
    )

    hits = repo.search("OSPF")

    assert len(hits) == 1
    citation = hits[0].citation
    assert citation.source_id == "src-1"
    assert citation.title == "Routing Concepts Module 14"
    assert citation.approved is True


def test_search_hit_citation_title_ignores_section_title_and_ordinal(
    repo: SQLiteContentRepository,
) -> None:
    """title never falls back to section_title or a synthetic
    "chunk N of doc-X" label — it is always the parent document's title,
    even when a section_title is present or absent."""
    repo.upsert_document(_document(title="Routing Concepts Module 14"))
    repo.upsert_chunk(_chunk(ordinal=3, section_title="Some Section"))

    hits = repo.search("OSPF")

    citation = hits[0].citation
    assert citation.title == "Routing Concepts Module 14"
    assert citation.title != "Some Section"
    assert "3" not in citation.title
    assert "doc-1" not in citation.title


def test_search_hit_citation_page_section_and_timestamps_appear_only_in_location(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document(title="Routing Concepts Module 14"))
    repo.upsert_chunk(
        _chunk(
            page_number=42,
            section_title="OSPF DR Election",
            timestamp_start_seconds=10.0,
            timestamp_end_seconds=45.5,
            status=SourceProcessingStatus.APPROVED,
        )
    )

    hits = repo.search("OSPF")

    citation = hits[0].citation
    assert citation.title == "Routing Concepts Module 14"  # never these values
    assert citation.location is not None
    assert "p.42" in citation.location
    assert "OSPF DR Election" in citation.location
    assert "10" in citation.location
    assert "45.5" in citation.location


def test_search_hit_citation_preserves_timestamp_range(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(timestamp_start_seconds=10.0, timestamp_end_seconds=45.5))

    hits = repo.search("OSPF")

    citation = hits[0].citation
    assert citation.location is not None
    assert "10" in citation.location
    assert "45.5" in citation.location


def test_search_hit_citation_location_is_none_with_no_positional_metadata(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())  # no page, section, or timestamp

    hits = repo.search("OSPF")

    assert hits[0].citation.location is None


def test_search_hit_citation_approved_reflects_chunk_status(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(status=SourceProcessingStatus.CANDIDATE))

    hits = repo.search("OSPF")

    assert hits[0].citation.approved is False


def test_search_raises_typed_error_when_parent_document_is_missing(
    repo: SQLiteContentRepository,
) -> None:
    """Simulates an otherwise-unreachable data-integrity violation: the
    public API (upsert_chunk requiring the parent to exist first, no
    delete_document()) cannot normally produce an orphaned chunk, so the
    only way to exercise this defensive path is to remove the parent row
    directly."""
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    repo._connection.execute(  # noqa: SLF001 - deliberately bypassing the public API, see docstring
        "DELETE FROM corpus_documents WHERE document_id = ?", ("doc-1",)
    )

    with pytest.raises(ParentDocumentNotFoundError):
        repo.search("OSPF")


# --- SQL-injection-shaped input ----------------------------------------------


def test_sql_injection_shaped_search_query_is_treated_as_literal_text(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    malicious_query = 'foo"; DROP TABLE content_chunks; --'
    hits = repo.search(malicious_query)  # must not raise, must not drop anything

    assert hits == ()
    assert repo.get_chunk("chunk-1") is not None


def test_sql_injection_shaped_chunk_text_is_stored_and_retrieved_as_data(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    malicious_text = "'; DROP TABLE content_chunks; --"
    repo.upsert_chunk(_chunk(text=malicious_text))

    fetched = repo.get_chunk("chunk-1")

    assert fetched is not None
    assert fetched.text == malicious_text
    assert repo.list_chunks() != ()


def test_sql_injection_shaped_filter_value_is_treated_as_literal_data(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(knowledge_scopes=[KnowledgeScope(certification="CCNA")]))

    results = repo.list_chunks(filters=ChunkSearchFilters(certification="CCNA' OR '1'='1"))

    assert results == ()
    assert repo.get_chunk("chunk-1") is not None


# --- close / context-manager lifecycle ---------------------------------------


def test_operations_after_close_raise(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.close()

    with pytest.raises(Exception):  # noqa: B017 - sqlite3.ProgrammingError on a closed connection
        repo.get_document("doc-1")


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    path = tmp_path / "content.sqlite3"
    with SQLiteContentRepository.open(path) as store:
        store.initialize_schema()
        store.upsert_document(_document())
        assert store.get_document("doc-1") is not None

    with pytest.raises(Exception):  # noqa: B017
        store.get_document("doc-1")


def test_context_manager_closes_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "content.sqlite3"
    store: SQLiteContentRepository | None = None
    with pytest.raises(ValueError), SQLiteContentRepository.open(path) as opened:
        store = opened
        store.initialize_schema()
        raise ValueError("boom")

    assert store is not None
    with pytest.raises(Exception):  # noqa: B017
        store.get_document("anything")


# --- empty results and deterministic ordering --------------------------------


def test_search_with_no_matches_returns_empty_tuple(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    hits = repo.search("nonexistent phrase that matches nothing")

    assert hits == ()


def test_list_chunks_empty_repository_returns_empty_tuple(repo: SQLiteContentRepository) -> None:
    assert repo.list_chunks() == ()


def test_list_documents_empty_repository_returns_empty_tuple(
    repo: SQLiteContentRepository,
) -> None:
    assert repo.list_documents() == ()


def test_search_results_are_deterministically_ordered(repo: SQLiteContentRepository) -> None:
    repo.upsert_document(_document())
    for i in range(5):
        repo.upsert_chunk(
            _chunk(chunk_id=f"chunk-{i}", ordinal=i, text="OSPF DR election overview")
        )

    first_run = [hit.chunk.chunk_id for hit in repo.search("OSPF")]
    second_run = [hit.chunk.chunk_id for hit in repo.search("OSPF")]

    assert first_run == second_run
    assert first_run == sorted(first_run)


def test_list_chunks_are_deterministically_ordered_by_document_and_ordinal(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-c", ordinal=2))
    repo.upsert_chunk(_chunk(chunk_id="chunk-a", ordinal=0))
    repo.upsert_chunk(_chunk(chunk_id="chunk-b", ordinal=1))

    chunks = repo.list_chunks()

    assert [c.chunk_id for c in chunks] == ["chunk-a", "chunk-b", "chunk-c"]
