"""Governed retrieval: one variable at a time.

Covers red items 10, 13–17.

Earlier probes changed several dimensions at once — a "source bytes" test
that also changed the document hash proves only that *something* was
bound. Each test here perturbs exactly one dimension and leaves every
other one current, so a passing test names the constraint that actually
holds.

Three reproduced defects: a fabricated ``review_decision_id`` authorized
retrieval (a non-empty string was treated as proof of approval); a
restricted parent document was invisible because privacy was read from the
chunk alone; and a chunk whose ``trusted_for_rag`` had been withdrawn was
still returned.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest

from personal_lms.content.governed import (
    GovernedChunkEligibility,
    GovernedRetrievalPolicy,
    build_governed_filters,
)
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
TEXT = f"{TERM} governed passage"
POLICY_VERSION = "gate-1-evidence-1.0"
SOURCE_SHA = text_hash("source-bytes")


class Fixture:
    """One fully-current, fully-eligible chunk that each test perturbs once."""

    def __init__(self, repository: SQLiteContentRepository) -> None:
        self.repository = repository
        self.decision_id = str(uuid4())
        self.document = CorpusDocument(
            document_id="doc-1",
            source_id="src-1",
            title="Synthetic corpus document",
            status=SourceProcessingStatus.APPROVED,
            content_hash=text_hash("doc-1"),
            privacy_classification=PrivacyClassification.INTERNAL,
            knowledge_scopes=[KnowledgeScope(objective_framework=OBJECTIVE_REF)],
        )
        self.chunk = ContentChunk(
            chunk_id="chunk-1",
            document_id="doc-1",
            source_id="src-1",
            ordinal=0,
            text=TEXT,
            text_hash=text_hash(TEXT),
            status=SourceProcessingStatus.APPROVED,
            trusted_for_rag=True,
            privacy_classification=PrivacyClassification.INTERNAL,
            knowledge_scopes=[KnowledgeScope(objective_framework=OBJECTIVE_REF)],
        )
        repository.upsert_document(self.document)
        repository.upsert_chunk(self.chunk)
        repository.upsert_eligibility(self.eligibility())

    def eligibility(self, **overrides: object) -> GovernedChunkEligibility:
        defaults: dict[str, object] = {
            "chunk_id": "chunk-1",
            "chunk_text_hash": text_hash(TEXT),
            "document_content_hash": text_hash("doc-1"),
            "source_id": "src-1",
            "source_sha256": SOURCE_SHA,
            "review_decision_id": self.decision_id,
            "eligibility_policy_version": POLICY_VERSION,
            "rights_status": SourceRightsStatus.OWNED,
            "permitted_uses": frozenset({PermittedUse.LOCAL_TEACH}),
            "trust_status": TrustStatus.TRUSTED,
            "quarantine_status": QuarantineStatus.CLEAR,
        }
        return GovernedChunkEligibility(**{**defaults, **overrides})  # type: ignore[arg-type]

    def hits(self) -> int:
        return len(self.repository.search(TERM, filters=self.filters()))

    @staticmethod
    def filters():  # type: ignore[no-untyped-def]
        return build_governed_filters(
            GovernedRetrievalPolicy(
                policy_version=POLICY_VERSION,
                objective_ref=OBJECTIVE_REF,
                requested_use=PermittedUse.LOCAL_TEACH,
            ),
            known_decision_ids=None,
        )


@pytest.fixture
def repository() -> SQLiteContentRepository:
    repo = SQLiteContentRepository.open(":memory:")
    repo.initialize_schema()
    return repo


@pytest.fixture
def fixture(repository: SQLiteContentRepository) -> Fixture:
    return Fixture(repository)


class TestBaseline:
    def test_a_fully_current_governed_chunk_is_retrievable(self, fixture: Fixture) -> None:
        """Every later test perturbs exactly one thing away from here."""
        assert fixture.hits() == 1


class TestPersistedDecisionIsRequired:
    def test_a_fabricated_decision_id_authorizes_nothing(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        """A non-empty string is not evidence that a human decided anything."""
        repository.upsert_eligibility(fixture.eligibility(review_decision_id=str(uuid4())))

        filters = build_governed_filters(
            GovernedRetrievalPolicy(
                policy_version=POLICY_VERSION,
                objective_ref=OBJECTIVE_REF,
                requested_use=PermittedUse.LOCAL_TEACH,
            ),
            known_decision_ids=frozenset({fixture.decision_id}),
        )

        assert repository.search(TERM, filters=filters) == ()

    def test_the_persisted_decision_id_is_accepted(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        filters = build_governed_filters(
            GovernedRetrievalPolicy(
                policy_version=POLICY_VERSION,
                objective_ref=OBJECTIVE_REF,
                requested_use=PermittedUse.LOCAL_TEACH,
            ),
            known_decision_ids=frozenset({fixture.decision_id}),
        )

        assert len(repository.search(TERM, filters=filters)) == 1

    def test_an_empty_known_decision_set_authorizes_nothing(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        filters = build_governed_filters(
            GovernedRetrievalPolicy(
                policy_version=POLICY_VERSION,
                objective_ref=OBJECTIVE_REF,
                requested_use=PermittedUse.LOCAL_TEACH,
            ),
            known_decision_ids=frozenset(),
        )

        assert repository.search(TERM, filters=filters) == ()


class TestOneVariableAtATime:
    def test_changing_only_the_chunk_text_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        replacement = f"{TERM} rewritten after the governance decision"
        repository.upsert_chunk(
            fixture.chunk.model_copy(
                update={"text": replacement, "text_hash": text_hash(replacement)}
            )
        )

        assert fixture.hits() == 0

    def test_an_authored_text_hash_that_lies_about_the_text_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        """Rewriting the text while keeping the old digest must not preserve access.

        ``ContentChunk.text_hash`` is a caller-supplied field under the
        existing shared contract, and ``model_copy`` skips validators, so
        a stale digest can genuinely be stored. The governed join
        therefore recomputes the digest from the chunk's *actual text* and
        never reads the stored field.
        """
        replacement = f"{TERM} rewritten but the stored digest left stale"
        repository.upsert_chunk(fixture.chunk.model_copy(update={"text": replacement}))

        assert fixture.hits() == 0

    def test_changing_only_the_document_content_hash_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_document(
            fixture.document.model_copy(update={"content_hash": text_hash("doc-1-revised")})
        )

        assert fixture.hits() == 0

    def test_changing_only_the_document_status_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_document(
            fixture.document.model_copy(update={"status": SourceProcessingStatus.CANDIDATE})
        )

        assert fixture.hits() == 0

    def test_changing_only_the_chunk_status_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_chunk(
            fixture.chunk.model_copy(
                update={"status": SourceProcessingStatus.CANDIDATE, "trusted_for_rag": False}
            )
        )

        assert fixture.hits() == 0

    def test_withdrawing_only_trusted_for_rag_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        """Status stays approved; only the separate trust decision changes."""
        repository.upsert_chunk(fixture.chunk.model_copy(update={"trusted_for_rag": False}))

        assert fixture.hits() == 0

    def test_changing_only_the_source_bytes_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        """Document hash, chunk text, and everything else stay current."""
        repository.upsert_eligibility(
            fixture.eligibility(source_sha256=text_hash("different-source-bytes"))
        )

        filters = build_governed_filters(
            GovernedRetrievalPolicy(
                policy_version=POLICY_VERSION,
                objective_ref=OBJECTIVE_REF,
                requested_use=PermittedUse.LOCAL_TEACH,
            ),
            known_decision_ids=frozenset({fixture.decision_id}),
            current_source_sha256={"src-1": SOURCE_SHA},
        )

        assert repository.search(TERM, filters=filters) == ()

    def test_changing_only_the_source_id_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_eligibility(fixture.eligibility(source_id="src-other"))

        assert fixture.hits() == 0

    def test_changing_only_the_policy_version_revokes_eligibility(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_eligibility(
            fixture.eligibility(eligibility_policy_version="some-other-policy-2.0")
        )

        assert fixture.hits() == 0


class TestStrictestPrivacyWins:
    def test_a_restricted_parent_document_is_not_overridden_by_a_public_chunk(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        """Privacy read from the chunk alone let a child downgrade its parent."""
        repository.upsert_document(
            fixture.document.model_copy(
                update={"privacy_classification": PrivacyClassification.RESTRICTED_LOCAL_ONLY}
            )
        )
        repository.upsert_chunk(
            fixture.chunk.model_copy(
                update={"privacy_classification": PrivacyClassification.PUBLIC}
            )
        )

        assert fixture.hits() == 0

    def test_a_restricted_chunk_under_a_public_document_is_also_excluded(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_document(
            fixture.document.model_copy(
                update={"privacy_classification": PrivacyClassification.PUBLIC}
            )
        )
        repository.upsert_chunk(
            fixture.chunk.model_copy(
                update={"privacy_classification": PrivacyClassification.RESTRICTED_LOCAL_ONLY}
            )
        )

        assert fixture.hits() == 0

    def test_both_public_is_retrievable(
        self, repository: SQLiteContentRepository, fixture: Fixture
    ) -> None:
        repository.upsert_document(
            fixture.document.model_copy(
                update={"privacy_classification": PrivacyClassification.PUBLIC}
            )
        )
        repository.upsert_chunk(
            fixture.chunk.model_copy(
                update={"privacy_classification": PrivacyClassification.PUBLIC}
            )
        )

        assert fixture.hits() == 1


class TestConstraintsPrecedeOrderByAndLimit:
    @pytest.mark.parametrize(
        ("label", "perturb"),
        [
            (
                "quarantined",
                lambda f: f.eligibility(quarantine_status=QuarantineStatus.QUARANTINED),
            ),
            ("untrusted", lambda f: f.eligibility(trust_status=TrustStatus.PROVISIONAL)),
            ("rights denied", lambda f: f.eligibility(rights_status=SourceRightsStatus.RESTRICTED)),
            (
                "use not permitted",
                lambda f: f.eligibility(permitted_uses=frozenset({PermittedUse.LOCAL_INDEX})),
            ),
        ],
    )
    def test_ineligible_rows_never_consume_the_result_window(
        self,
        repository: SQLiteContentRepository,
        fixture: Fixture,
        label: str,
        perturb: Callable[[Fixture], GovernedChunkEligibility],
    ) -> None:
        """Nine ineligible rows ahead of one eligible row, limit three.

        A post-``LIMIT`` filter returns nothing here; a WHERE-clause filter
        returns the eligible row.
        """
        for index in range(9):
            blocked = ContentChunk(
                chunk_id=f"blocked-{index}",
                document_id="doc-1",
                source_id="src-1",
                ordinal=index + 1,
                text=TEXT,
                text_hash=text_hash(TEXT),
                status=SourceProcessingStatus.APPROVED,
                trusted_for_rag=True,
                privacy_classification=PrivacyClassification.INTERNAL,
                knowledge_scopes=[KnowledgeScope(objective_framework=OBJECTIVE_REF)],
            )
            repository.upsert_chunk(blocked)
            import dataclasses

            record = perturb(fixture)
            repository.upsert_eligibility(dataclasses.replace(record, chunk_id=f"blocked-{index}"))

        hits = repository.search(TERM, filters=fixture.filters(), limit=3)

        assert [hit.chunk.chunk_id for hit in hits] == ["chunk-1"], (
            f"a {label} row consumed the window"
        )
