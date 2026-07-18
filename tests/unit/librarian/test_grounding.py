from __future__ import annotations

import pytest

from personal_lms.catalog import SourceSearchFilters, SourceSearchHit, SourceSearchMode
from personal_lms.catalog.sqlite import SQLiteSourceCatalog
from personal_lms.domain import (
    KnowledgeScope,
    LibrarianRetrievalRequest,
    ProvenanceMetadata,
    SourceProcessingStatus,
    SourceRecord,
    SourceType,
)
from personal_lms.librarian import LibrarianGroundingService

_VALID_SHA256 = "a" * 64


def _record(**overrides: object) -> SourceRecord:
    defaults: dict[str, object] = {
        "source_id": "src-00000001",
        "source_type": SourceType.PDF,
        "original_location": "archive/networking/module14.pdf",
        "filename": "routing-concepts-module14.pdf",
        "mime_type": "application/pdf",
        "sha256_hash": _VALID_SHA256,
        "byte_size": 204_800,
        "status": SourceProcessingStatus.APPROVED,
        # Matches _request()'s default interpreted_query so a plain
        # _record() is findable by a plain _request() without every test
        # having to restate matching search text.
        "provenance": ProvenanceMetadata(acquisition_note="OSPF DR election overview"),
    }
    defaults.update(overrides)
    return SourceRecord.model_validate(defaults)


def _request(**overrides: object) -> LibrarianRetrievalRequest:
    defaults: dict[str, object] = {"interpreted_query": "OSPF DR election"}
    defaults.update(overrides)
    return LibrarianRetrievalRequest.model_validate(defaults)


class _CountingCatalog:
    """Wraps a real catalog, counting calls to search() only."""

    def __init__(self, inner: SQLiteSourceCatalog) -> None:
        self._inner = inner
        self.search_calls: list[str] = []

    def initialize_schema(self) -> None:
        self._inner.initialize_schema()

    def upsert_source(self, record: SourceRecord) -> None:
        self._inner.upsert_source(record)

    def get_source(self, source_id: str) -> SourceRecord | None:
        return self._inner.get_source(source_id)

    def list_sources(
        self, *, filters: SourceSearchFilters | None = None
    ) -> tuple[SourceRecord, ...]:
        return self._inner.list_sources(filters=filters)

    def add_relationship(self, relationship: object) -> None:
        raise NotImplementedError

    def list_relationships(self, source_id: str) -> tuple[object, ...]:
        return ()

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: SourceSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[SourceSearchHit, ...]:
        self.search_calls.append(query)
        return self._inner.search(query, mode=mode, filters=filters, limit=limit)

    def close(self) -> None:
        self._inner.close()


@pytest.fixture
def catalog() -> SQLiteSourceCatalog:
    store = SQLiteSourceCatalog.open(":memory:")
    store.initialize_schema()
    return store


@pytest.fixture
def service(catalog: SQLiteSourceCatalog) -> LibrarianGroundingService:
    return LibrarianGroundingService(catalog)


# --- basic retrieval and provenance preservation ----------------------------


def test_retrieve_converts_a_hit_into_evidence_with_preserved_provenance(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    record = _record(
        source_id="src-ospf",
        filename="ospf-dr-election.pdf",
        provenance=ProvenanceMetadata(acquisition_note="OSPF DR election overview"),
    )
    catalog.upsert_source(record)

    bundle = service.retrieve(_request(interpreted_query="OSPF DR election"))

    assert len(bundle.evidence) == 1
    evidence = bundle.evidence[0]
    assert evidence.citation.source_id == record.source_id
    assert evidence.citation.title == record.filename
    assert evidence.citation.approved is True
    assert evidence.citation.location is None


def test_retrieve_searches_the_catalog_exactly_once() -> None:
    inner = SQLiteSourceCatalog.open(":memory:")
    inner.initialize_schema()
    inner.upsert_source(_record())
    counting = _CountingCatalog(inner)
    service = LibrarianGroundingService(counting)  # type: ignore[arg-type]

    service.retrieve(_request())

    assert len(counting.search_calls) == 1


def test_bundle_request_id_correlates_to_the_request(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    request = _request()

    bundle = service.retrieve(request)

    assert bundle.request_id == request.request_id


def test_bundle_carries_through_the_requests_knowledge_scope(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    request = _request(knowledge_scope=KnowledgeScope(certification="CCNA"))

    bundle = service.retrieve(request)

    assert bundle.knowledge_scope is not None
    assert bundle.knowledge_scope.certification == "CCNA"


# --- sufficiency rules -------------------------------------------------------


def test_sufficient_when_at_least_one_approved_source_matches(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record(status=SourceProcessingStatus.APPROVED))

    bundle = service.retrieve(_request())

    assert bundle.is_sufficient is True
    assert bundle.gaps == []


def test_sufficient_with_reviewed_or_trusted_for_rag_status(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record(source_id="src-1", status=SourceProcessingStatus.REVIEWED))

    bundle = service.retrieve(_request())

    assert bundle.is_sufficient is True


def test_insufficient_and_reports_gap_when_no_sources_match(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    bundle = service.retrieve(_request(interpreted_query="nonexistent topic"))

    assert bundle.evidence == []
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1
    assert "no cataloged sources matched" in bundle.gaps[0]


def test_insufficient_and_reports_gap_when_only_candidate_sources_match(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record(status=SourceProcessingStatus.CANDIDATE))

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 1  # candidate evidence is still surfaced, not dropped
    assert bundle.evidence[0].citation.approved is False
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1
    assert "none are approved/trusted" in bundle.gaps[0]


def test_raw_and_rejected_sources_are_not_treated_as_approved(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record(source_id="src-raw", status=SourceProcessingStatus.RAW))
    catalog.upsert_source(_record(source_id="src-rejected", status=SourceProcessingStatus.REJECTED))

    bundle = service.retrieve(_request())

    assert all(not item.citation.approved for item in bundle.evidence)
    assert bundle.is_sufficient is False


def test_mixed_approved_and_candidate_sources_are_sufficient_and_both_surfaced(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record(source_id="src-approved", status=SourceProcessingStatus.APPROVED))
    catalog.upsert_source(
        _record(source_id="src-candidate", status=SourceProcessingStatus.CANDIDATE)
    )

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 2
    assert bundle.is_sufficient is True
    assert bundle.gaps == []


# --- conflicts are never fabricated ------------------------------------------


def test_conflicts_are_always_empty_even_with_multiple_hits(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record(source_id="src-1"))
    catalog.upsert_source(_record(source_id="src-2"))

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 2
    assert bundle.conflicts == []


# --- search mode: all_terms (default) and exact_phrase ----------------------


def test_explicit_exact_phrase_mode_rejects_non_adjacent_terms(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(
        _record(
            provenance=ProvenanceMetadata(
                acquisition_note=(
                    "ip addressing notes, neighbor discovery, ospf basics, show commands"
                )
            )
        )
    )

    bundle = service.retrieve(
        _request(interpreted_query="show ip ospf neighbor"), mode=SourceSearchMode.EXACT_PHRASE
    )

    assert bundle.evidence == []


def test_explicit_exact_phrase_mode_matches_the_literal_phrase(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(
        _record(provenance=ProvenanceMetadata(acquisition_note="output of show ip ospf neighbor"))
    )

    bundle = service.retrieve(
        _request(interpreted_query="show ip ospf neighbor"), mode=SourceSearchMode.EXACT_PHRASE
    )

    assert len(bundle.evidence) == 1


def test_default_mode_is_all_terms_without_a_mode_argument(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    """No mode argument at all — proves ALL_TERMS is the true default,
    not merely what happens to be passed in other tests."""
    catalog.upsert_source(
        _record(
            provenance=ProvenanceMetadata(
                acquisition_note=(
                    "ip addressing notes, neighbor discovery, ospf basics, show commands"
                )
            )
        )
    )

    bundle = service.retrieve(_request(interpreted_query="show ip ospf neighbor"))

    assert len(bundle.evidence) == 1


def test_quoted_query_without_explicit_mode_is_treated_as_ordinary_all_terms_text(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    """A caller-supplied quoted query string is not sniffed into
    EXACT_PHRASE — it is plain ALL_TERMS text, quote characters and all.
    Proven against the scattered (non-adjacent) fixture: under the old,
    now-removed quote-detection convention this would have resolved to
    EXACT_PHRASE and matched nothing."""
    catalog.upsert_source(
        _record(
            provenance=ProvenanceMetadata(
                acquisition_note=(
                    "ip addressing notes, neighbor discovery, ospf basics, show commands"
                )
            )
        )
    )

    bundle = service.retrieve(_request(interpreted_query='"show ip ospf neighbor"'))

    assert len(bundle.evidence) == 1


def test_quoted_query_with_explicit_exact_phrase_mode_still_requires_adjacency(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    """The explicit mode parameter (not query text) governs EXACT_PHRASE
    behavior: a quoted query passed with an explicit EXACT_PHRASE mode
    still fails to match non-adjacent content, exactly like the unquoted
    equivalent."""
    catalog.upsert_source(
        _record(
            provenance=ProvenanceMetadata(
                acquisition_note=(
                    "ip addressing notes, neighbor discovery, ospf basics, show commands"
                )
            )
        )
    )

    bundle = service.retrieve(
        _request(interpreted_query='"show ip ospf neighbor"'), mode=SourceSearchMode.EXACT_PHRASE
    )

    assert bundle.evidence == []


# --- knowledge-scope filter translation --------------------------------------


def test_knowledge_scope_certification_filters_the_search(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(
        _record(
            source_id="src-ccna",
            knowledge_scopes=[KnowledgeScope(certification="CCNA")],
        )
    )
    catalog.upsert_source(
        _record(
            source_id="src-aplus",
            knowledge_scopes=[KnowledgeScope(certification="A+")],
        )
    )

    bundle = service.retrieve(_request(knowledge_scope=KnowledgeScope(certification="CCNA")))

    assert [item.citation.source_id for item in bundle.evidence] == ["src-ccna"]


def test_evidence_carries_the_records_single_knowledge_scope(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(
        _record(knowledge_scopes=[KnowledgeScope(certification="CCNA", topic="routing")])
    )

    bundle = service.retrieve(_request())

    assert bundle.evidence[0].knowledge_scope is not None
    assert bundle.evidence[0].knowledge_scope.certification == "CCNA"


def test_evidence_knowledge_scope_is_none_when_record_has_multiple_scopes(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(
        _record(
            knowledge_scopes=[
                KnowledgeScope(certification="CCNA"),
                KnowledgeScope(certification="Network+"),
            ]
        )
    )

    bundle = service.retrieve(_request())

    assert bundle.evidence[0].knowledge_scope is None


# --- unsupported knowledge-pack filtering -----------------------------------


def test_non_empty_knowledge_packs_returns_insufficient_with_a_clear_gap(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record())  # would otherwise match

    bundle = service.retrieve(_request(knowledge_packs=["ccna", "network-plus"]))

    assert bundle.evidence == []
    assert bundle.is_sufficient is False
    assert len(bundle.gaps) == 1
    assert "knowledge_packs" in bundle.gaps[0]
    assert "not supported" in bundle.gaps[0]


def test_non_empty_knowledge_packs_performs_zero_catalog_searches() -> None:
    inner = SQLiteSourceCatalog.open(":memory:")
    inner.initialize_schema()
    inner.upsert_source(_record())
    counting = _CountingCatalog(inner)
    service = LibrarianGroundingService(counting)  # type: ignore[arg-type]

    service.retrieve(_request(knowledge_packs=["ccna"]))

    assert counting.search_calls == []


def test_non_empty_knowledge_packs_preserves_the_requests_knowledge_scope(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    bundle = service.retrieve(
        _request(
            knowledge_packs=["ccna"],
            knowledge_scope=KnowledgeScope(certification="CCNA"),
        )
    )

    assert bundle.knowledge_scope is not None
    assert bundle.knowledge_scope.certification == "CCNA"


def test_empty_knowledge_packs_searches_normally(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record())

    bundle = service.retrieve(_request(knowledge_packs=[]))

    assert len(bundle.evidence) == 1


# --- relationship state: unknown, never fabricated False --------------------


def test_duplicate_and_superseded_state_is_none_when_not_evaluated(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record())

    bundle = service.retrieve(_request())

    assert len(bundle.evidence) == 1
    assert bundle.evidence[0].is_duplicate is None
    assert bundle.evidence[0].is_superseded is None
    assert bundle.evidence[0].superseded_by_source_id is None


# --- domain neutrality --------------------------------------------------------


def test_service_works_with_no_knowledge_scope_at_all(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(_record())

    bundle = service.retrieve(_request())

    assert bundle.knowledge_scope is None
    assert len(bundle.evidence) == 1


# --- relevance score bounds ---------------------------------------------------


def test_relevance_score_is_bounded_between_zero_and_one(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    catalog.upsert_source(
        _record(provenance=ProvenanceMetadata(acquisition_note="OSPF DR election overview"))
    )

    bundle = service.retrieve(_request())

    assert 0.0 <= bundle.evidence[0].relevance_score <= 1.0  # type: ignore[operator]


# --- max_results respected ----------------------------------------------------


def test_request_max_results_limits_the_search(
    catalog: SQLiteSourceCatalog, service: LibrarianGroundingService
) -> None:
    for i in range(5):
        catalog.upsert_source(
            _record(
                source_id=f"src-{i}",
                provenance=ProvenanceMetadata(acquisition_note="OSPF DR election overview"),
            )
        )

    bundle = service.retrieve(_request(max_results=2))

    assert len(bundle.evidence) == 2
