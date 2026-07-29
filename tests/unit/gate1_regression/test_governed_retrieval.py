"""Governed retrieval binding and scope agreement (review findings #11–#14).

The first eligibility side table stored a bare ``objective_ref`` string as
a *substitute* for the existing ``KnowledgeScope.objective_framework``
relation, and bound its governance row to nothing but a chunk ID. That let
a governance row outlive the content it governed: replacing a chunk's
text, downgrading its parent document, or superseding its source left the
row intact and the chunk still retrievable.
"""

from __future__ import annotations

import pytest

from personal_lms.content.governed import (
    GovernedChunkEligibility,
    GovernedRetrievalPolicy,
    build_governed_filters,
)
from personal_lms.content.protocol import ChunkSearchFilters
from personal_lms.content.sqlite import SQLiteContentRepository
from personal_lms.domain.content import ContentChunk, CorpusDocument
from personal_lms.domain.enums import SourceProcessingStatus
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.objective_packs import (
    PermittedUse,
    QuarantineStatus,
    TrustStatus,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus

from ..objective_packs._helpers import OBJECTIVE_REF, text_hash

TERM = "synthetic"
CHUNK_TEXT = f"{TERM} placeholder passage"
DECISION_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def repository() -> SQLiteContentRepository:
    repo = SQLiteContentRepository.open(":memory:")
    repo.initialize_schema()
    return repo


def seed(
    repository: SQLiteContentRepository,
    *,
    chunk_id: str = "chunk-1",
    text: str = CHUNK_TEXT,
    privacy: PrivacyClassification = PrivacyClassification.INTERNAL,
    document_status: SourceProcessingStatus = SourceProcessingStatus.APPROVED,
    scope_objective: str | None = OBJECTIVE_REF,
    governed: bool = True,
) -> None:
    document = CorpusDocument(
        document_id="doc-1",
        source_id="src-1",
        title="Synthetic corpus document",
        status=document_status,
        content_hash=text_hash("doc-1"),
        knowledge_scopes=(
            [KnowledgeScope(objective_framework=scope_objective)]
            if scope_objective is not None
            else []
        ),
    )
    repository.upsert_document(document)
    chunk = ContentChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        source_id="src-1",
        ordinal=0,
        text=text,
        text_hash=text_hash(text),
        status=SourceProcessingStatus.APPROVED,
        trusted_for_rag=True,
        privacy_classification=privacy,
        knowledge_scopes=(
            [KnowledgeScope(objective_framework=scope_objective)]
            if scope_objective is not None
            else []
        ),
    )
    repository.upsert_chunk(chunk)
    if governed:
        repository.upsert_eligibility(
            GovernedChunkEligibility(
                chunk_id=chunk_id,
                chunk_text_hash=chunk.text_hash,
                document_content_hash=document.content_hash,
                source_id="src-1",
                source_sha256=text_hash("src-1-bytes"),
                review_decision_id=DECISION_ID,
                eligibility_policy_version="gate-1-evidence-1.0",
                rights_status=SourceRightsStatus.OWNED,
                permitted_uses=frozenset({PermittedUse.LOCAL_TEACH}),
                trust_status=TrustStatus.TRUSTED,
                quarantine_status=QuarantineStatus.CLEAR,
            )
        )


def strict(
    *, privacy_ceiling: PrivacyClassification = PrivacyClassification.INTERNAL
) -> ChunkSearchFilters:
    return build_governed_filters(
        GovernedRetrievalPolicy(
            policy_version="gate-1-evidence-1.0",
            objective_ref=OBJECTIVE_REF,
            requested_use=PermittedUse.LOCAL_TEACH,
            privacy_ceiling=privacy_ceiling,
        )
    )


class TestPrivacyCeilingBeforeLimit:
    def test_restricted_local_only_content_is_excluded_before_limit(
        self, repository: SQLiteContentRepository
    ) -> None:
        """A strict governed retrieval must never surface local-only content."""
        seed(repository, privacy=PrivacyClassification.RESTRICTED_LOCAL_ONLY)

        assert repository.search(TERM, filters=strict()) == ()

    def test_permitted_privacy_survives(self, repository: SQLiteContentRepository) -> None:
        seed(repository, privacy=PrivacyClassification.PUBLIC)

        assert len(repository.search(TERM, filters=strict())) == 1

    def test_restricted_rows_do_not_consume_the_result_window(
        self, repository: SQLiteContentRepository
    ) -> None:
        for index in range(9):
            seed(
                repository,
                chunk_id=f"blocked-{index}",
                privacy=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
            )
        seed(repository, chunk_id="zz-permitted")

        hits = repository.search(TERM, filters=strict(), limit=3)

        assert [hit.chunk.chunk_id for hit in hits] == ["zz-permitted"]


class TestScopeUsesTheExistingKnowledgeScopeRelation:
    def test_objective_scope_comes_from_knowledge_scope_not_a_side_table(
        self, repository: SQLiteContentRepository
    ) -> None:
        """A chunk with no objective_framework scope must not match."""
        seed(repository, scope_objective=None)

        assert repository.search(TERM, filters=strict()) == ()

    def test_a_mismatched_knowledge_scope_excludes_the_chunk(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository, scope_objective="synthetic-exam-v2.0:2.2")

        assert repository.search(TERM, filters=strict()) == ()

    def test_a_matching_knowledge_scope_is_required_and_sufficient(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository)

        assert len(repository.search(TERM, filters=strict())) == 1


class TestEligibilityIsBoundToCurrentContent:
    def test_replacing_chunk_text_invalidates_prior_eligibility(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository)
        replacement = f"{TERM} passage rewritten after the governance decision"
        repository.upsert_chunk(
            ContentChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                source_id="src-1",
                ordinal=0,
                text=replacement,
                text_hash=text_hash(replacement),
                status=SourceProcessingStatus.APPROVED,
                trusted_for_rag=True,
                knowledge_scopes=[KnowledgeScope(objective_framework=OBJECTIVE_REF)],
            )
        )

        assert repository.search(TERM, filters=strict()) == ()

    def test_downgrading_the_parent_document_invalidates_eligibility(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository)
        repository.upsert_document(
            CorpusDocument(
                document_id="doc-1",
                source_id="src-1",
                title="Synthetic corpus document",
                status=SourceProcessingStatus.CANDIDATE,
                content_hash=text_hash("doc-1-revised"),
                knowledge_scopes=[KnowledgeScope(objective_framework=OBJECTIVE_REF)],
            )
        )

        assert repository.search(TERM, filters=strict()) == ()

    def test_a_governance_row_naming_a_stale_hash_never_matches(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository, governed=False)
        repository.upsert_eligibility(
            GovernedChunkEligibility(
                chunk_id="chunk-1",
                chunk_text_hash=text_hash("some other text"),
                document_content_hash=text_hash("doc-1"),
                source_id="src-1",
                source_sha256=text_hash("src-1-bytes"),
                review_decision_id=DECISION_ID,
                eligibility_policy_version="gate-1-evidence-1.0",
                rights_status=SourceRightsStatus.OWNED,
                permitted_uses=frozenset({PermittedUse.LOCAL_TEACH}),
                trust_status=TrustStatus.TRUSTED,
                quarantine_status=QuarantineStatus.CLEAR,
            )
        )

        assert repository.search(TERM, filters=strict()) == ()

    def test_a_policy_version_mismatch_excludes_the_chunk(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository)
        mismatched = build_governed_filters(
            GovernedRetrievalPolicy(
                policy_version="some-other-policy-2.0",
                objective_ref=OBJECTIVE_REF,
                requested_use=PermittedUse.LOCAL_TEACH,
            )
        )

        assert repository.search(TERM, filters=mismatched) == ()

    def test_a_row_without_a_review_decision_never_matches(
        self, repository: SQLiteContentRepository
    ) -> None:
        """Governance without a persisted decision is not governance."""
        with pytest.raises(ValueError, match="review_decision_id"):
            GovernedChunkEligibility(
                chunk_id="chunk-1",
                chunk_text_hash=text_hash(CHUNK_TEXT),
                document_content_hash=text_hash("doc-1"),
                source_id="src-1",
                source_sha256=text_hash("src-1-bytes"),
                review_decision_id="",
                eligibility_policy_version="gate-1-evidence-1.0",
                rights_status=SourceRightsStatus.OWNED,
                permitted_uses=frozenset({PermittedUse.LOCAL_TEACH}),
                trust_status=TrustStatus.TRUSTED,
                quarantine_status=QuarantineStatus.CLEAR,
            )


class TestExistingProtocolStaysCompatible:
    def test_the_base_content_repository_protocol_is_unchanged(self) -> None:
        """Existing wrappers must remain structurally compatible."""
        from personal_lms.content.protocol import ContentRepository

        assert isinstance(SQLiteContentRepository.open(":memory:"), ContentRepository)

    def test_the_governed_subprotocol_is_separate(self) -> None:
        from personal_lms.content.governed import GovernedContentRepository

        assert isinstance(SQLiteContentRepository.open(":memory:"), GovernedContentRepository)

    def test_ungoverned_search_behaviour_is_unchanged(
        self, repository: SQLiteContentRepository
    ) -> None:
        seed(repository, governed=False)

        assert len(repository.search(TERM)) == 1
