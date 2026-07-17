from __future__ import annotations

import pytest

from personal_lms.catalog import SourceSearchMode
from personal_lms.content import ChunkSearchFilters, ChunkSearchHit, SQLiteContentRepository
from personal_lms.domain import (
    ContentChunk,
    CorpusDocument,
    KnowledgeScope,
    LibrarianRetrievalRequest,
    PrivacyClassification,
    SourceProcessingStatus,
)
from personal_lms.librarian import LibrarianContentGroundingService
from personal_lms.librarian.content_grounding import _allowed_privacy_classifications

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


def _request(**overrides: object) -> LibrarianRetrievalRequest:
    defaults: dict[str, object] = {"interpreted_query": "OSPF DR election"}
    defaults.update(overrides)
    return LibrarianRetrievalRequest.model_validate(defaults)


class _CountingRepository:
    """Wraps a real repository, counting calls to search() only."""

    def __init__(self, inner: SQLiteContentRepository) -> None:
        self._inner = inner
        self.search_calls: list[str] = []

    def initialize_schema(self) -> None:
        self._inner.initialize_schema()

    def upsert_document(self, document: CorpusDocument) -> None:
        self._inner.upsert_document(document)

    def get_document(self, document_id: str) -> CorpusDocument | None:
        return self._inner.get_document(document_id)

    def list_documents(self, *, source_id: str | None = None) -> tuple[CorpusDocument, ...]:
        return self._inner.list_documents(source_id=source_id)

    def upsert_chunk(self, chunk: ContentChunk) -> None:
        self._inner.upsert_chunk(chunk)

    def get_chunk(self, chunk_id: str) -> ContentChunk | None:
        return self._inner.get_chunk(chunk_id)

    def list_chunks(self, *, filters: ChunkSearchFilters | None = None) -> tuple[ContentChunk, ...]:
        return self._inner.list_chunks(filters=filters)

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: ChunkSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[ChunkSearchHit, ...]:
        self.search_calls.append(query)
        return self._inner.search(query, mode=mode, filters=filters, limit=limit)

    def close(self) -> None:
        self._inner.close()


@pytest.fixture
def repo() -> SQLiteContentRepository:
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    return store


@pytest.fixture
def service(repo: SQLiteContentRepository) -> LibrarianContentGroundingService:
    return LibrarianContentGroundingService(repo)


# --- actual chunk text and provenance in evidence ---------------------------


def test_evidence_carries_the_actual_chunk_text(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="The OSPF DR election is decided by priority, then router ID."))

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 1
    assert bundle.evidence[0].text == "The OSPF DR election is decided by priority, then router ID."


def test_evidence_preserves_source_document_and_chunk_provenance(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_chunk(_chunk(chunk_id="chunk-7", document_id="doc-1", source_id="src-1"))

    bundle = service.retrieve(_request())

    evidence = bundle.evidence[0]
    assert evidence.citation.source_id == "src-1"
    assert evidence.document_id == "doc-1"
    assert evidence.chunk_id == "chunk-7"


def test_retrieve_searches_the_repository_exactly_once() -> None:
    inner = SQLiteContentRepository.open(":memory:")
    inner.initialize_schema()
    inner.upsert_document(_document())
    inner.upsert_chunk(_chunk())
    counting = _CountingRepository(inner)
    service = LibrarianContentGroundingService(counting)  # type: ignore[arg-type]

    service.retrieve(_request())

    assert len(counting.search_calls) == 1


def test_bundle_request_id_correlates_to_the_request(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    request = _request()
    bundle = service.retrieve(request)
    assert bundle.request_id == request.request_id


# --- citation page/section/timestamp preservation ----------------------------


def test_citation_title_equals_parent_document_title(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(title="Routing Concepts Module 14"))
    repo.upsert_chunk(_chunk(section_title="Some Section"))

    bundle = service.retrieve(_request())

    citation = bundle.evidence[0].citation
    assert citation.title == "Routing Concepts Module 14"
    assert citation.title != "Some Section"


def test_citation_location_preserves_page_section_and_timestamp(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            page_number=42,
            section_title="OSPF DR Election",
            timestamp_start_seconds=10.0,
            timestamp_end_seconds=45.5,
        )
    )

    bundle = service.retrieve(_request())

    location = bundle.evidence[0].citation.location
    assert location is not None
    assert "p.42" in location
    assert "OSPF DR Election" in location
    assert "10" in location
    assert "45.5" in location


def test_citation_location_is_none_with_no_positional_metadata(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    bundle = service.retrieve(_request())

    assert bundle.evidence[0].citation.location is None


# --- trusted vs untrusted sufficiency ----------------------------------------


def test_sufficient_when_at_least_one_trusted_chunk_matches(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(status=SourceProcessingStatus.APPROVED))
    repo.upsert_chunk(_chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=True))

    bundle = service.retrieve(_request())

    assert bundle.is_sufficient is True
    assert bundle.gaps == []


def test_insufficient_when_hits_exist_but_none_are_trusted(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(status=SourceProcessingStatus.APPROVED))
    repo.upsert_chunk(_chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=False))

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 1  # untrusted evidence is still represented
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1
    assert "trusted_for_rag" in bundle.gaps[0]


def test_insufficient_and_reports_gap_when_no_chunks_match(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    bundle = service.retrieve(_request(interpreted_query="nonexistent topic"))

    assert bundle.evidence == []
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1
    assert "no permitted content chunks matched" in bundle.gaps[0]


def test_mixed_trusted_and_untrusted_chunks_are_sufficient_and_both_surfaced(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(status=SourceProcessingStatus.APPROVED))
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-trusted",
            status=SourceProcessingStatus.APPROVED,
            trusted_for_rag=True,
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-untrusted",
            ordinal=1,
            status=SourceProcessingStatus.APPROVED,
            trusted_for_rag=False,
        )
    )

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 2
    assert bundle.is_sufficient is True
    assert bundle.gaps == []


def test_evidence_never_fabricates_conflicts_duplicate_or_superseded_state(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(status=SourceProcessingStatus.APPROVED))
    repo.upsert_chunk(
        _chunk(chunk_id="chunk-1", status=SourceProcessingStatus.APPROVED, trusted_for_rag=True)
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-2",
            ordinal=1,
            status=SourceProcessingStatus.APPROVED,
            trusted_for_rag=True,
        )
    )

    bundle = service.retrieve(_request())

    assert bundle.conflicts == []
    for item in bundle.evidence:
        assert item.is_duplicate is None
        assert item.is_superseded is None


# --- scope filters -----------------------------------------------------------


def test_knowledge_scope_certification_filters_the_search(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-ccna",
            document_id="doc-1",
            source_id="src-1",
            knowledge_scopes=[KnowledgeScope(certification="CCNA")],
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-aplus",
            ordinal=1,
            document_id="doc-2",
            source_id="src-2",
            knowledge_scopes=[KnowledgeScope(certification="A+")],
        )
    )

    bundle = service.retrieve(_request(knowledge_scope=KnowledgeScope(certification="CCNA")))

    assert [item.chunk_id for item in bundle.evidence] == ["chunk-ccna"]


def test_bundle_carries_through_the_requests_knowledge_scope(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    bundle = service.retrieve(_request(knowledge_scope=KnowledgeScope(certification="CCNA")))
    assert bundle.knowledge_scope is not None
    assert bundle.knowledge_scope.certification == "CCNA"


# --- privacy filtering ---------------------------------------------------


def test_more_restrictive_chunk_is_excluded_from_evidence(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY))

    bundle = service.retrieve(_request(privacy_classification=PrivacyClassification.INTERNAL))

    assert bundle.evidence == []


def test_equally_classified_chunk_is_included(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(privacy_classification=PrivacyClassification.INTERNAL))

    bundle = service.retrieve(_request(privacy_classification=PrivacyClassification.INTERNAL))

    assert len(bundle.evidence) == 1


def test_less_restrictive_chunk_is_included_for_a_more_permissive_request(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    """A request classified internal must still see public chunks — the
    filter is a ceiling, not an exact-match requirement."""
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(privacy_classification=PrivacyClassification.PUBLIC))

    bundle = service.retrieve(_request(privacy_classification=PrivacyClassification.INTERNAL))

    assert len(bundle.evidence) == 1


def test_privacy_filtering_excludes_only_the_more_restrictive_chunk(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-public",
            privacy_classification=PrivacyClassification.PUBLIC,
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-restricted",
            ordinal=1,
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        )
    )

    bundle = service.retrieve(_request(privacy_classification=PrivacyClassification.INTERNAL))

    assert [item.chunk_id for item in bundle.evidence] == ["chunk-public"]


def test_allowed_privacy_classifications_for_each_ceiling() -> None:
    """Each request classification produces exactly the explicit allowed
    set implied by the PUBLIC < INTERNAL < SENSITIVE < RESTRICTED_LOCAL_ONLY
    hierarchy — never more, never fewer."""
    assert _allowed_privacy_classifications(PrivacyClassification.PUBLIC) == {
        PrivacyClassification.PUBLIC
    }
    assert _allowed_privacy_classifications(PrivacyClassification.INTERNAL) == {
        PrivacyClassification.PUBLIC,
        PrivacyClassification.INTERNAL,
    }
    assert _allowed_privacy_classifications(PrivacyClassification.SENSITIVE) == {
        PrivacyClassification.PUBLIC,
        PrivacyClassification.INTERNAL,
        PrivacyClassification.SENSITIVE,
    }
    assert _allowed_privacy_classifications(PrivacyClassification.RESTRICTED_LOCAL_ONLY) == {
        PrivacyClassification.PUBLIC,
        PrivacyClassification.INTERNAL,
        PrivacyClassification.SENSITIVE,
        PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    }


def test_approved_but_untrusted_evidence_reports_both_signals_distinctly(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    """A chunk can be approved (citation.approved=True) while still not
    trusted_for_rag — these are distinct signals and the bundle must
    remain insufficient despite the approval."""
    repo.upsert_document(_document(status=SourceProcessingStatus.APPROVED))
    repo.upsert_chunk(_chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=False))

    bundle = service.retrieve(_request())

    assert bundle.is_sufficient is False
    assert len(bundle.evidence) == 1
    assert bundle.evidence[0].citation.approved is True
    assert bundle.evidence[0].trusted_for_rag is False


# --- explicit search modes ----------------------------------------------


def test_default_mode_matches_non_adjacent_terms(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            text=("ip addressing notes, neighbor discovery, ospf basics, show commands overview")
        )
    )

    bundle = service.retrieve(_request(interpreted_query="show ip ospf neighbor"))

    assert len(bundle.evidence) == 1


def test_explicit_exact_phrase_mode_rejects_non_adjacent_terms(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            text=("ip addressing notes, neighbor discovery, ospf basics, show commands overview")
        )
    )

    bundle = service.retrieve(
        _request(interpreted_query="show ip ospf neighbor"), mode=SourceSearchMode.EXACT_PHRASE
    )

    assert bundle.evidence == []


def test_explicit_exact_phrase_mode_matches_the_literal_phrase(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="output of show ip ospf neighbor"))

    bundle = service.retrieve(
        _request(interpreted_query="show ip ospf neighbor"), mode=SourceSearchMode.EXACT_PHRASE
    )

    assert len(bundle.evidence) == 1


def test_quoted_query_without_explicit_mode_is_not_reinterpreted(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    """A caller-supplied quoted query string is plain ALL_TERMS text, not
    sniffed into EXACT_PHRASE — proven against scattered (non-adjacent)
    content."""
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            text=("ip addressing notes, neighbor discovery, ospf basics, show commands overview")
        )
    )

    bundle = service.retrieve(_request(interpreted_query='"show ip ospf neighbor"'))

    assert len(bundle.evidence) == 1


# --- knowledge-pack short-circuit --------------------------------------------


def test_non_empty_knowledge_packs_returns_insufficient_with_a_clear_gap(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())  # would otherwise match

    bundle = service.retrieve(_request(knowledge_packs=["ccna", "network-plus"]))

    assert bundle.evidence == []
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1
    assert "knowledge_packs" in bundle.gaps[0]
    assert "not supported" in bundle.gaps[0]


def test_non_empty_knowledge_packs_performs_zero_repository_searches() -> None:
    inner = SQLiteContentRepository.open(":memory:")
    inner.initialize_schema()
    inner.upsert_document(_document())
    inner.upsert_chunk(_chunk())
    counting = _CountingRepository(inner)
    service = LibrarianContentGroundingService(counting)  # type: ignore[arg-type]

    service.retrieve(_request(knowledge_packs=["ccna"]))

    assert counting.search_calls == []


def test_empty_knowledge_packs_searches_normally(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    bundle = service.retrieve(_request(knowledge_packs=[]))

    assert len(bundle.evidence) == 1


# --- no-results behavior ---------------------------------------------------


def test_empty_repository_returns_insufficient_with_gap(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    bundle = service.retrieve(_request())

    assert bundle.evidence == []
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1


def test_request_max_results_limits_the_search(
    repo: SQLiteContentRepository, service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    for i in range(5):
        repo.upsert_chunk(
            _chunk(chunk_id=f"chunk-{i}", ordinal=i, text="OSPF DR election overview")
        )

    bundle = service.retrieve(_request(max_results=2))

    assert len(bundle.evidence) == 2
