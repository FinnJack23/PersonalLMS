"""Librarian domain contracts: structured retrieval requests and interpreted evidence.

These are pure data shapes only — see
``docs/product-specs/AGENT_ROSTER_AND_CONTRACTS.md`` and
``docs/product-specs/RAG_KNOWLEDGE_PLANE.md`` for the Librarian's role. The
Librarian:

- builds ``LibrarianRetrievalRequest`` and sends it toward a future
  domain-neutral RAG service (not implemented here — no retrieval, index,
  embedding, or vector-database code exists in this module);
- interprets that service's results into a ``GroundingBundle``, recording
  sufficiency, gaps, and conflicts;
- never ingests files, never chunks, embeds, indexes, or manages vector
  infrastructure;
- never approves a source — ``SourceCitation.approved`` is read-only status
  reported here, set elsewhere by the Curator;
- never generates the final teaching response — that is the Tutor's job,
  consuming a ``GroundingBundle`` as input (see ``domain/tutor.py``);
- never writes to Obsidian.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.privacy import PrivacyClassification


class LibrarianRetrievalRequest(StrictModel):
    """A structured retrieval request the Librarian addresses to a future
    domain-neutral RAG service.

    ``knowledge_packs`` may name zero, one, or several packs — retrieval is
    not assumed to be scoped to a single active domain (see
    RAG_KNOWLEDGE_PLANE.md, "Single- and cross-domain retrieval").
    """

    request_id: UUID = Field(default_factory=uuid4)
    interpreted_query: str = Field(
        min_length=1,
        description="The Librarian's structured interpretation of what to search for.",
    )
    raw_query: str | None = Field(
        default=None,
        min_length=1,
        description="The original, unmodified user phrasing, if it differs from interpreted_query.",
    )
    knowledge_scope: KnowledgeScope | None = None
    knowledge_packs: list[str] = Field(default_factory=list)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    max_results: int | None = Field(default=None, gt=0)
    created_at: AwareDatetime = Field(default_factory=utcnow)


class RetrievedEvidence(StrictModel):
    """One piece of evidence returned by retrieval, with the Librarian's
    interpretation of its standing (duplicate, superseded) layered on top
    of the plain citation.

    ``is_duplicate`` and ``is_superseded`` are three-valued
    (``True``/``False``/``None``), not booleans defaulting to ``False``:
    ``None`` means this specific piece of evidence's duplicate/supersession
    status was never evaluated (e.g. a retrieval path that only searched
    metadata and never consulted ``SourceAssetRelationship`` records) —
    never fabricate ``False`` (a truly-checked-and-clear result) merely
    because a check did not happen. Only a caller that has actually
    inspected relationship data may assert ``True`` or ``False``.

    ``text``, ``document_id``, and ``chunk_id`` are all optional and
    default to ``None`` — source-metadata-only retrieval paths (which have
    no actual chunk content, just a citation) never set them, so existing
    callers and JSON payloads remain unaffected. A content-chunk retrieval
    path sets all three: ``text`` is the chunk's actual retrieved content,
    and ``document_id``/``chunk_id`` preserve exactly which
    ``CorpusDocument``/``ContentChunk`` it came from, alongside
    ``citation.source_id`` for the ``SourceRecord``.

    ``trusted_for_rag`` mirrors ``ContentChunk.trusted_for_rag`` and is
    kept deliberately distinct from ``citation.approved``: the citation's
    ``approved`` reflects the underlying chunk's/source's processing
    *status*, while ``trusted_for_rag`` is the separate, stricter RAG-trust
    gate (which additionally requires the parent document to be reviewed —
    see ``content.sqlite.SQLiteContentRepository.upsert_chunk``). A piece
    of evidence can be ``approved=True`` while ``trusted_for_rag=False``.
    Defaults to ``None`` — a source-metadata-only retrieval path (no
    ``ContentChunk`` involved at all) has no such concept to report and
    leaves it unset, exactly like ``is_duplicate``/``is_superseded``.
    """

    citation: SourceCitation
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    knowledge_pack: str | None = Field(default=None, min_length=1)
    knowledge_scope: KnowledgeScope | None = None
    is_duplicate: bool | None = None
    is_superseded: bool | None = None
    superseded_by_source_id: str | None = Field(default=None, min_length=1)
    text: str | None = Field(default=None, min_length=1)
    document_id: str | None = Field(default=None, min_length=1)
    chunk_id: str | None = Field(default=None, min_length=1)
    trusted_for_rag: bool | None = None


class EvidenceConflict(StrictModel):
    """A Librarian-identified disagreement between two or more evidence items."""

    description: str = Field(min_length=1)
    conflicting_source_ids: list[str] = Field(min_length=2)


class GroundingBundle(StrictModel):
    """The Librarian's interpreted retrieval result: evidence plus its own
    sufficiency and conflict judgment.

    This is the one artifact the Tutor (and, per the agent roster, the
    Drill Master) consumes as grounding — see ``domain/tutor.py``. An empty
    ``evidence`` list is a valid, meaningful state (nothing usable was
    retrieved); ``is_sufficient`` is the Librarian's explicit judgment
    call, never inferred from the presence or count of ``evidence`` by a
    downstream consumer.
    """

    bundle_id: UUID = Field(default_factory=uuid4)
    request_id: UUID = Field(
        description="Correlates to the originating LibrarianRetrievalRequest.request_id."
    )
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    is_sufficient: bool
    gaps: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    knowledge_scope: KnowledgeScope | None = None
    created_at: AwareDatetime = Field(default_factory=utcnow)
