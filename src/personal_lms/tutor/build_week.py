"""Build Week evidence-to-lesson, drill, verification, and mastery workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import Field

from personal_lms.domain.base import StrictModel
from personal_lms.domain.librarian import GroundingBundle, RetrievedEvidence
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.mastery import (
    MasteryRecord,
    ReviewStatus,
    SQLiteMasteryStore,
    new_session_id,
    utc_now,
)


class LessonDepth(StrEnum):
    BRIEF = "brief"
    STANDARD = "standard"


class DrillQuestion(StrictModel):
    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    answer_choices: tuple[str, ...] = ()
    correct_answer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    supporting_citation_ids: tuple[str, ...] = Field(min_length=1)
    objective: str = Field(min_length=1)
    difficulty: str = Field(min_length=1)


class TutorRequest(StrictModel):
    learning_objective: str = Field(min_length=1)
    knowledge_scope: str = Field(min_length=1)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    maximum_sources: int = Field(default=5, ge=1, le=20)
    lesson_depth: LessonDepth = LessonDepth.STANDARD
    generate_drill: bool = True


class TutorResponse(StrictModel):
    lesson: str
    citations: tuple[str, ...]
    retrieval_gaps: tuple[str, ...]
    source_summary: tuple[str, ...]
    verification_status: str
    drill_questions: tuple[DrillQuestion, ...]
    misconception_check: str | None
    mastery_record: MasteryRecord | None
    model_route: str
    session_id: UUID


class GroundingProvider(Protocol):
    def retrieve(self, objective: str, scope: str, maximum_sources: int) -> GroundingBundle: ...


class LessonProvider(Protocol):
    async def generate(self, prompt: str, *, privacy: PrivacyClassification) -> str: ...


def _citation_ids(evidence: list[RetrievedEvidence]) -> tuple[str, ...]:
    return tuple(f"E{i + 1}" for i in range(len(evidence)))


def _drill(objective: str, evidence: list[RetrievedEvidence]) -> tuple[DrillQuestion, ...]:
    ids = _citation_ids(evidence)
    if len(evidence) < 1:
        return ()
    first = evidence[0]
    support = first.text or first.citation.title
    return (
        DrillQuestion(
            question_id="q1",
            question_text=f"What is the central idea supported by {ids[0]}?",
            correct_answer=support,
            explanation=f"Supported by {ids[0]}.",
            supporting_citation_ids=(ids[0],),
            objective=objective,
            difficulty="recall",
        ),
        DrillQuestion(
            question_id="q2",
            question_text="Which action best applies the retrieved concept?",
            answer_choices=("Apply the documented behavior", "Ignore the evidence"),
            correct_answer="Apply the documented behavior",
            explanation=(
                f"The retrieved evidence supports using the documented behavior ({ids[0]})."
            ),
            supporting_citation_ids=(ids[0],),
            objective=objective,
            difficulty="applied",
        ),
        DrillQuestion(
            question_id="q3",
            question_text="Which statement is the misconception to avoid?",
            answer_choices=(
                "A claim outside the retrieved evidence is verified",
                "A claim needs evidence",
            ),
            correct_answer="A claim outside the retrieved evidence is verified",
            explanation="The evidence does not support that claim; review is required.",
            supporting_citation_ids=(ids[0],),
            objective=objective,
            difficulty="misconception",
        ),
    )


class GroundedTutorBuildWeekService:
    def __init__(
        self,
        grounding: GroundingProvider,
        mastery: SQLiteMasteryStore,
        lesson_provider: LessonProvider | None = None,
    ) -> None:
        self._grounding = grounding
        self._mastery = mastery
        self._lesson_provider = lesson_provider

    async def teach(self, request: TutorRequest) -> TutorResponse:
        bundle = self._grounding.retrieve(
            request.learning_objective, request.knowledge_scope, request.maximum_sources
        )
        evidence = bundle.evidence[: request.maximum_sources]
        session_id = new_session_id()
        citations = _citation_ids(evidence)
        gaps = tuple(bundle.gaps)
        if not bundle.is_sufficient or not evidence:
            return TutorResponse(
                lesson="Evidence is insufficient for a grounded lesson.",
                citations=citations,
                retrieval_gaps=gaps or ("No approved evidence was retrieved.",),
                source_summary=(),
                verification_status="REVIEW_NEEDED",
                drill_questions=(),
                misconception_check=None,
                mastery_record=None,
                model_route="offline_simulated",
                session_id=session_id,
            )
        excerpts = [e.text or e.citation.title for e in evidence]
        if self._lesson_provider is not None:
            lesson = await self._lesson_provider.generate(
                f"Objective: {request.learning_objective}\nEvidence: {excerpts}",
                privacy=request.privacy_classification,
            )
            route = (
                "gpt-5.6"
                if request.privacy_classification is not PrivacyClassification.RESTRICTED_LOCAL_ONLY
                else "local_only"
            )
        else:
            lesson = " ".join(
                f"[{cid}] {text}" for cid, text in zip(citations, excerpts, strict=True)
            )
            route = "offline_simulated"
        questions = _drill(request.learning_objective, evidence) if request.generate_drill else ()
        return TutorResponse(
            lesson=lesson,
            citations=citations,
            retrieval_gaps=gaps,
            source_summary=tuple(e.citation.title for e in evidence),
            verification_status="VERIFIED",
            drill_questions=questions,
            misconception_check=questions[2].question_text if questions else None,
            mastery_record=None,
            model_route=route,
            session_id=session_id,
        )

    def record_answer(
        self, response: TutorResponse, question: DrillQuestion, selected_answer: str
    ) -> MasteryRecord:
        correct = selected_answer == question.correct_answer
        record = MasteryRecord(
            learning_session_id=response.session_id,
            learning_objective=question.objective,
            question_id=question.question_id,
            selected_answer=selected_answer,
            correct=correct,
            review_status=ReviewStatus.MASTERED if correct else ReviewStatus.RETEACH_REQUIRED,
            weak_area_tags=() if correct else (question.difficulty,),
            created_at=utc_now(),
        )
        return self._mastery.save(record)
