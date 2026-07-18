"""Domain-neutral grounding service: SourceCatalog search interpreted as a GroundingBundle.

This is the Librarian's retrieval-interpretation role made concrete over
the existing ``SourceCatalog`` protocol — see
``docs/product-specs/AGENT_ROSTER_AND_CONTRACTS.md`` and
``docs/product-specs/RAG_KNOWLEDGE_PLANE.md``. It performs no model or
provider call, no Obsidian access, no filesystem access, no embeddings, no
vector search, and no SQLite schema change: it only translates one
``LibrarianRetrievalRequest`` into ``SourceSearchFilters``, searches the
catalog at most once, and interprets the results into a
``GroundingBundle``.

Domain-neutral by construction: nothing here names CCNA, a certification,
or any other specific knowledge pack. A knowledge pack is only ever
optional caller-supplied ``KnowledgeScope`` data flowing through
``LibrarianRetrievalRequest.knowledge_scope`` — CCNA is a future consumer
of this service, never a structural requirement of it.

Two things this service deliberately does *not* do, both to avoid
claiming more than it actually knows:

- **Search mode is never inferred from query text.** ``retrieve()``
  defaults to ``SourceSearchMode.ALL_TERMS`` and uses
  ``SourceSearchMode.EXACT_PHRASE`` only when the caller passes it
  explicitly via the ``mode`` parameter. There is no quote-detection or
  query-rewriting convention — a caller who writes
  ``'"show ip ospf neighbor"'`` gets exactly that literal string,
  including the quote characters, searched as ordinary ``ALL_TERMS`` text.
- **``LibrarianRetrievalRequest.knowledge_packs`` is never silently
  ignored.** ``SourceCatalog``/``SourceSearchFilters`` (retrieval v1) has
  no knowledge-pack filter dimension at all. Rather than search anyway and
  return results the caller never asked to be scoped that broadly, a
  non-empty ``knowledge_packs`` list short-circuits to an explicit
  insufficient ``GroundingBundle`` with a gap explaining why, and performs
  zero catalog searches.

Conflict detection is likewise out of scope: identifying that two sources
*disagree* requires content-level analysis this service does not perform
(no model call, no deterministic domain calculator). ``GroundingBundle.conflicts``
is therefore always empty — never fabricated to look more thorough than
the search actually was. The same discipline applies to
``RetrievedEvidence.is_duplicate``/``is_superseded``: this service never
consults ``SourceAssetRelationship`` data, so both are always left
``None`` (unknown), never defaulted to ``False``.
"""

from __future__ import annotations

from personal_lms.catalog.protocol import SourceCatalog, SourceSearchFilters, SourceSearchMode
from personal_lms.domain.catalog import SourceRecord
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.enums import SourceProcessingStatus
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.librarian import (
    GroundingBundle,
    LibrarianRetrievalRequest,
    RetrievedEvidence,
)

_DEFAULT_LIMIT = 20

# Statuses that represent a source having passed human curation — see
# SourceProcessingStatus's own docstring ("from raw archive entry through
# trusted-RAG eligibility") and RAG_KNOWLEDGE_PLANE.md's promotion rule:
# only curator-approved, promoted sources are ever presented as grounded
# evidence. RAW/CATALOGED/CANDIDATE/REJECTED/RECONSTRUCTED are deliberately
# excluded — none of them represents a completed review.
_APPROVED_STATUSES = frozenset(
    {
        SourceProcessingStatus.APPROVED,
        SourceProcessingStatus.REVIEWED,
        SourceProcessingStatus.TRUSTED_FOR_RAG,
    }
)

_KNOWLEDGE_PACKS_UNSUPPORTED_GAP = (
    "knowledge_packs={packs!r} was requested, but knowledge-pack filtering is "
    "not supported by retrieval v1's SourceCatalog; refusing to search unscoped "
    "rather than silently ignoring the requested constraint"
)


def _citation_from_record(record: SourceRecord) -> SourceCitation:
    """A citation with no invented fields.

    ``location`` (page/section/timestamp) is left unset — this service has
    no positional information from a keyword-search hit, and fabricating
    one would violate the "never fabricate citations" requirement.
    """
    return SourceCitation(
        source_id=record.source_id,
        title=record.filename,
        location=None,
        approved=record.status in _APPROVED_STATUSES,
    )


def _relevance_score(raw_score: float) -> float:
    """Deterministic, monotonic squashing of an unbounded catalog search
    score into ``RetrievedEvidence.relevance_score``'s required [0, 1]
    range.

    ``SourceSearchHit.score`` (derived from FTS5's ``bm25()``) is
    higher-is-better but has no fixed upper bound. This is a bounded
    transform of that real value — never a fabricated confidence — chosen
    because it is simple, deterministic, and strictly order-preserving:
    for any two non-negative scores ``a > b``, ``a/(1+a) > b/(1+b)``.
    """
    bounded = max(raw_score, 0.0)
    return bounded / (1.0 + bounded)


def _single_knowledge_scope(record: SourceRecord) -> KnowledgeScope | None:
    """The record's one scope tag, or ``None``.

    Never guesses among multiple candidates: a record tagged with more
    than one ``KnowledgeScope`` has no unambiguous "the" scope to
    attribute to this evidence item without knowing which one the query
    actually matched on, which a keyword-search hit does not report.
    """
    return record.knowledge_scopes[0] if len(record.knowledge_scopes) == 1 else None


def _build_filters(request: LibrarianRetrievalRequest) -> SourceSearchFilters:
    """Translate the request's scope into ``SourceSearchFilters``.

    Only ``knowledge_scope`` fields are translated. ``privacy_classification``
    is deliberately not translated into an exact-match filter: the catalog's
    filter is equality-based, and a request's privacy classification is not
    an equality constraint on what may be retrieved (a request classified
    ``internal`` should not silently exclude ``public`` sources). Multi-value
    ``knowledge_packs`` scoping is handled separately by ``retrieve()`` —
    see the module docstring — since it cannot be expressed as a
    ``SourceSearchFilters`` field at all.
    """
    scope = request.knowledge_scope
    if scope is None:
        return SourceSearchFilters()
    return SourceSearchFilters(
        knowledge_domain=scope.knowledge_domain,
        certification=scope.certification,
        course=scope.course,
        topic=scope.topic,
        objective_framework=scope.objective_framework,
    )


class LibrarianGroundingService:
    """Turns one ``LibrarianRetrievalRequest`` into one ``GroundingBundle``,
    searching a ``SourceCatalog`` at most once."""

    def __init__(self, catalog: SourceCatalog) -> None:
        self._catalog = catalog

    def retrieve(
        self,
        request: LibrarianRetrievalRequest,
        *,
        mode: SourceSearchMode | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> GroundingBundle:
        if request.knowledge_packs:
            return GroundingBundle(
                request_id=request.request_id,
                evidence=[],
                is_sufficient=False,
                gaps=[_KNOWLEDGE_PACKS_UNSUPPORTED_GAP.format(packs=request.knowledge_packs)],
                conflicts=[],
                knowledge_scope=request.knowledge_scope,
            )

        resolved_mode = mode if mode is not None else SourceSearchMode.ALL_TERMS
        filters = _build_filters(request)
        search_limit = request.max_results if request.max_results is not None else limit

        hits = self._catalog.search(
            request.interpreted_query, mode=resolved_mode, filters=filters, limit=search_limit
        )

        evidence = [
            RetrievedEvidence(
                citation=_citation_from_record(hit.record),
                relevance_score=_relevance_score(hit.score),
                knowledge_scope=_single_knowledge_scope(hit.record),
                is_duplicate=None,
                is_superseded=None,
            )
            for hit in hits
        ]

        has_approved_evidence = any(item.citation.approved for item in evidence)

        gaps: list[str] = []
        if not evidence:
            gaps.append(f"no cataloged sources matched the query: {request.interpreted_query!r}")
        elif not has_approved_evidence:
            gaps.append(
                f"retrieved {len(evidence)} candidate source(s) for "
                f"{request.interpreted_query!r}, but none are approved/trusted"
            )

        return GroundingBundle(
            request_id=request.request_id,
            evidence=evidence,
            is_sufficient=bool(evidence) and has_approved_evidence,
            gaps=gaps,
            conflicts=[],
            knowledge_scope=request.knowledge_scope,
        )
