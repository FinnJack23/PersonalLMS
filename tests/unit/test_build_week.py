import asyncio
from datetime import UTC, datetime
from uuid import uuid4

# Fixture construction is intentionally compact; line length is not behavior.
# ruff: noqa: E501
import pytest

from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.librarian import GroundingBundle, RetrievedEvidence
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.mastery import SQLiteMasteryStore
from personal_lms.source_readiness import (
    SourceReadinessEntry,
    SourceReadinessImporter,
    SourceReadinessManifest,
)
from personal_lms.tutor.build_week import GroundedTutorBuildWeekService, TutorRequest


class Inventory:
    def __init__(self) -> None:
        self.sources = {}
        self.versions = []

    def add_source(self, source):
        self.sources[source.canonical_locator] = source
        return source

    def find_by_locator(self, kind, locator):
        return self.sources.get(locator)

    def add_version(self, version):
        self.versions.append(version)
        return version


def test_manifest_import_is_idempotent_and_rejects_private_paths() -> None:
    with pytest.raises(ValueError):
        SourceReadinessEntry(
            entry_id="x",
            source_label="x",
            display_name="x",
            media_type="text",
            content_identity="synthetic:x",
            availability_status="approved",
            candidate_locator="/home/private/source.txt",
        )
    inventory = Inventory()
    manifest = SourceReadinessManifest(
        manifest_version="1",
        manifest_id="m",
        generated_by="test",
        entries=(
            SourceReadinessEntry(
                entry_id="x",
                source_label="x",
                display_name="x",
                media_type="text",
                content_identity="synthetic:x",
                availability_status="approved",
                approval_status="approved",
                candidate_locator="demo://x",
            ),
        ),
    )
    importer = SourceReadinessImporter(inventory)
    now = datetime.now(UTC)
    first = importer.import_manifest(manifest, now=now)
    second = importer.import_manifest(manifest, now=now)
    assert first.imported_entry_ids == ("x",)
    assert second.skipped_entry_ids == ("x",)


class Grounding:
    def retrieve(self, objective, scope, maximum_sources):
        return GroundingBundle(
            request_id=uuid4(),
            is_sufficient=True,
            evidence=[
                RetrievedEvidence(
                    citation=SourceCitation(source_id="s", title="Approved source", approved=True),
                    text="Evidence claim",
                    trusted_for_rag=True,
                )
            ],
            gaps=["Unsupported follow-up"],
        )


def test_tutor_returns_exactly_three_cited_questions_and_gap() -> None:
    service = GroundedTutorBuildWeekService(Grounding(), SQLiteMasteryStore.open(":memory:"))
    response = asyncio.run(
        service.teach(
            TutorRequest(
                learning_objective="Explain routes",
                knowledge_scope="networking",
                privacy_classification=PrivacyClassification.INTERNAL,
            )
        )
    )
    assert response.verification_status == "VERIFIED"
    assert response.retrieval_gaps == ("Unsupported follow-up",)
    assert len(response.drill_questions) == 3
    assert all(question.supporting_citation_ids for question in response.drill_questions)


def test_tutor_fails_closed_without_evidence() -> None:
    class Empty:
        def retrieve(self, objective, scope, maximum_sources):
            return GroundingBundle(request_id=uuid4(), is_sufficient=False, gaps=["No evidence"])

    service = GroundedTutorBuildWeekService(Empty(), SQLiteMasteryStore.open(":memory:"))
    response = asyncio.run(
        service.teach(
            TutorRequest(learning_objective="Explain routes", knowledge_scope="networking")
        )
    )
    assert response.drill_questions == ()
    assert response.verification_status == "REVIEW_NEEDED"
