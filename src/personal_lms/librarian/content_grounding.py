"""Domain-neutral grounding service v2: ContentRepository search interpreted as a GroundingBundle.

This is the Librarian's retrieval-interpretation role made concrete over
the ``ContentRepository`` protocol, retrieving actual chunk *text* rather
than only source-level metadata — see
``personal_lms.librarian.grounding`` for the original source-metadata
service this one sits alongside (not replaces: both remain available,
neither modifies the other). It performs no model or provider call, no
Tutor generation, no Obsidian access, no filesystem access, no
embeddings, no vector search, and no SQLite schema change: it only
translates one ``LibrarianRetrievalRequest`` into ``ChunkSearchFilters``,
searches a ``ContentRepository`` exactly once, and interprets the results
into a ``GroundingBundle``.

Domain-neutral by construction: nothing here names CCNA, a certification,
or any other specific knowledge pack — see
``personal_lms.librarian.grounding`` for the same principle applied there.

Mirrors the source-metadata service's two deliberate omissions:

- **Search mode is never inferred from query text.** ``retrieve()``
  defaults to ``SourceSearchMode.ALL_TERMS`` and uses
  ``SourceSearchMode.EXACT_PHRASE`` only when the caller passes it
  explicitly via the ``mode`` parameter — no quote-detection or
  query-rewriting convention.
- **``LibrarianRetrievalRequest.knowledge_packs`` is never silently
  ignored.** ``ContentRepository``/``ChunkSearchFilters`` (retrieval v1)
  has no knowledge-pack filter dimension at all. A non-empty
  ``knowledge_packs`` list short-circuits to an explicit insufficient
  ``GroundingBundle`` with a gap explaining why, and performs zero
  repository searches.

Two additional rules specific to chunk-level retrieval:

- **Sufficiency is gated on ``ContentChunk.trusted_for_rag``, not
  citation approval.** A chunk's ``SourceCitation.approved`` (reused
  as-is from ``ChunkSearchHit.citation``) reflects the chunk's processing
  ``status``; ``trusted_for_rag`` is the separate, stricter gate
  established in ``domain.content`` (itself requiring both the chunk's
  own status *and* its parent document's status to be reviewed — see
  ``content.sqlite.SQLiteContentRepository.upsert_chunk``). Untrusted
  hits are still returned as evidence — never silently dropped — but
  cannot make a bundle ``is_sufficient``. ``RetrievedEvidence.trusted_for_rag``
  carries this value through so a consumer never has to guess it from
  ``citation.approved``.
- **Privacy filtering is a repository-level ``IN`` filter, applied before
  ``LIMIT``, not a Python post-filter.** ``request.privacy_classification``
  is translated into the *explicit* set of every classification it
  permits (see ``_ALLOWED_BY_CEILING`` below) and passed to
  ``ChunkSearchFilters.allowed_privacy_classifications`` — the repository
  narrows candidates in SQL before truncating to ``limit``, so a
  permitted chunk ranked past several more-restrictive matches is never
  pushed out of the result window by content it was never allowed to see
  in the first place. When the privacy-constrained search returns no
  hits, the gap says exactly that — "no *permitted* content chunks
  matched" — never implying anything about whether inaccessible matches
  exist.

Conflict detection is out of scope, exactly as in the source-metadata
service: ``GroundingBundle.conflicts`` is always empty, and
``RetrievedEvidence.is_duplicate``/``is_superseded`` are always ``None``
(unknown) — this service never consults ``SourceAssetRelationship`` data.
"""

from __future__ import annotations

from types import MappingProxyType

from personal_lms.content.protocol import (
    ChunkSearchFilters,
    ChunkSearchHit,
    ContentRepository,
    SourceSearchMode,
)
from personal_lms.domain.content import ContentChunk
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.librarian import (
    GroundingBundle,
    LibrarianRetrievalRequest,
    RetrievedEvidence,
)
from personal_lms.domain.privacy import PrivacyClassification

_DEFAULT_LIMIT = 20

_KNOWLEDGE_PACKS_UNSUPPORTED_GAP = (
    "knowledge_packs={packs!r} was requested, but knowledge-pack filtering is "
    "not supported by retrieval v1's ContentRepository; refusing to search "
    "unscoped rather than silently ignoring the requested constraint"
)

# Explicit, immutable privacy hierarchy — deliberately not derived from
# PrivacyClassification's enum declaration order, so a future reordering
# of that enum (e.g. for display purposes) can never silently change
# retrieval-security behavior here. A request permits its own
# classification and every strictly-lower-ranked one.
_PRIVACY_RANK: MappingProxyType[PrivacyClassification, int] = MappingProxyType(
    {
        PrivacyClassification.PUBLIC: 0,
        PrivacyClassification.INTERNAL: 1,
        PrivacyClassification.SENSITIVE: 2,
        PrivacyClassification.RESTRICTED_LOCAL_ONLY: 3,
    }
)

# Precomputed once: for each possible request ceiling, the exact frozenset
# of classifications it permits. Avoids rebuilding the same handful of
# frozensets on every retrieve() call.
_ALLOWED_BY_CEILING: MappingProxyType[PrivacyClassification, frozenset[PrivacyClassification]] = (
    MappingProxyType(
        {
            ceiling: frozenset(
                classification
                for classification, rank in _PRIVACY_RANK.items()
                if rank <= ceiling_rank
            )
            for ceiling, ceiling_rank in _PRIVACY_RANK.items()
        }
    )
)


def _allowed_privacy_classifications(
    ceiling: PrivacyClassification,
) -> frozenset[PrivacyClassification]:
    """Every classification a request classified ``ceiling`` may retrieve.

    Exact rule: a classification is permitted iff its explicit
    ``_PRIVACY_RANK`` is less than or equal to ``ceiling``'s — e.g.
    ``ceiling=INTERNAL`` permits ``{PUBLIC, INTERNAL}``, never
    ``SENSITIVE`` or ``RESTRICTED_LOCAL_ONLY``.
    """
    return _ALLOWED_BY_CEILING[ceiling]


def _relevance_score(raw_score: float) -> float:
    """Deterministic, monotonic squashing of an unbounded repository search
    score into ``RetrievedEvidence.relevance_score``'s required [0, 1] range.

    See ``personal_lms.librarian.grounding._relevance_score`` for the
    identical rationale — duplicated here rather than imported, keeping
    this module independent.
    """
    bounded = max(raw_score, 0.0)
    return bounded / (1.0 + bounded)


def _single_knowledge_scope(chunk: ContentChunk) -> KnowledgeScope | None:
    """The chunk's one scope tag, or ``None``.

    Never guesses among multiple candidates — see
    ``personal_lms.librarian.grounding._single_knowledge_scope`` for the
    identical rationale.
    """
    return chunk.knowledge_scopes[0] if len(chunk.knowledge_scopes) == 1 else None


def _build_filters(request: LibrarianRetrievalRequest) -> ChunkSearchFilters:
    """Translate the request's scope and privacy ceiling into ``ChunkSearchFilters``.

    ``knowledge_scope`` fields are translated as-is. ``privacy_classification``
    is translated into the *explicit allowed set* via
    ``_allowed_privacy_classifications`` — never as an exact-match
    ``ChunkSearchFilters.privacy_classification`` filter, which would
    incorrectly exclude less-restrictive chunks too (a request classified
    ``internal`` must still see ``public`` chunks). ``knowledge_packs`` is
    handled separately by ``retrieve()`` since it has no
    ``ChunkSearchFilters`` field at all.
    """
    scope = request.knowledge_scope
    return ChunkSearchFilters(
        knowledge_domain=scope.knowledge_domain if scope else None,
        certification=scope.certification if scope else None,
        course=scope.course if scope else None,
        topic=scope.topic if scope else None,
        objective_framework=scope.objective_framework if scope else None,
        allowed_privacy_classifications=_allowed_privacy_classifications(
            request.privacy_classification
        ),
    )


def _evidence_from_hit(hit: ChunkSearchHit) -> RetrievedEvidence:
    """Evidence preserving the chunk's actual text and full provenance chain.

    ``citation`` is reused as-is from ``hit.citation`` — the repository
    already builds it correctly (title from the parent ``CorpusDocument``,
    location from whatever page/section/timestamp the chunk actually
    carries; see ``content.sqlite._citation_from_chunk``), so rebuilding
    it here would only risk drifting out of sync with that logic.
    """
    chunk = hit.chunk
    return RetrievedEvidence(
        citation=hit.citation,
        relevance_score=_relevance_score(hit.score),
        knowledge_scope=_single_knowledge_scope(chunk),
        is_duplicate=None,
        is_superseded=None,
        text=chunk.text,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        trusted_for_rag=chunk.trusted_for_rag,
    )


class LibrarianContentGroundingService:
    """Turns one ``LibrarianRetrievalRequest`` into one ``GroundingBundle``,
    searching a ``ContentRepository`` exactly once."""

    def __init__(self, repository: ContentRepository) -> None:
        self._repository = repository

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

        hits = self._repository.search(
            request.interpreted_query, mode=resolved_mode, filters=filters, limit=search_limit
        )

        evidence = [_evidence_from_hit(hit) for hit in hits]
        has_trusted_evidence = any(hit.chunk.trusted_for_rag for hit in hits)

        gaps: list[str] = []
        if not evidence:
            gaps.append(
                f"no permitted content chunks matched the query: {request.interpreted_query!r}"
            )
        elif not has_trusted_evidence:
            gaps.append(
                f"retrieved {len(evidence)} chunk(s) for {request.interpreted_query!r}, "
                "but none are trusted_for_rag"
            )

        return GroundingBundle(
            request_id=request.request_id,
            evidence=evidence,
            is_sufficient=bool(evidence) and has_trusted_evidence,
            gaps=gaps,
            conflicts=[],
            knowledge_scope=request.knowledge_scope,
        )
