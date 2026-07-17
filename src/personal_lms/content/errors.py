"""Typed errors for content-repository persistence."""

from __future__ import annotations


class ContentRepositoryError(Exception):
    """Base class for all content-repository persistence errors."""


class ParentDocumentNotFoundError(ContentRepositoryError):
    """Raised when a chunk names a ``document_id`` with no persisted ``CorpusDocument``.

    Raised both at write time (``upsert_chunk()``, before persisting) and
    at read time (``search()``, if a chunk somehow has no resolvable
    parent) — a repository-integrity violation either way, never silently
    tolerated.
    """

    def __init__(self, document_id: str) -> None:
        super().__init__(
            f"No CorpusDocument cataloged with document_id {document_id!r}; "
            "upsert_document() must run before upsert_chunk() for chunks under it"
        )
        self.document_id = document_id


class ParentSourceMismatchError(ContentRepositoryError):
    """Raised when a chunk's ``source_id`` does not match its parent document's ``source_id``."""

    def __init__(self, chunk_id: str, document_id: str, expected: str, actual: str) -> None:
        super().__init__(
            f"Chunk {chunk_id!r} declares source_id {actual!r}, but its parent "
            f"document {document_id!r} has source_id {expected!r}"
        )
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.expected_source_id = expected
        self.actual_source_id = actual


class ParentDocumentNotApprovedError(ContentRepositoryError):
    """Raised when a ``trusted_for_rag=True`` chunk's parent document has
    not itself passed review.

    Independent of, and in addition to, ``ContentChunk``'s own
    ``status``-based validator — a chunk can individually claim an
    approved status while its parent document is still a raw or candidate
    entry; this check closes that gap at persistence time.
    """

    def __init__(self, chunk_id: str, document_id: str, parent_status: str) -> None:
        super().__init__(
            f"Chunk {chunk_id!r} sets trusted_for_rag=True, but its parent document "
            f"{document_id!r} has status {parent_status!r} — the parent must be "
            "approved, reviewed, or trusted_for_rag first"
        )
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.parent_status = parent_status
