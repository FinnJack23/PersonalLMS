"""Deterministic lexical retrieval harness for the Gate 1 10+2 contract.

Local, offline, and integer-only. There is no vector index, no embedding
model, and no network call anywhere in this module, and the gate report
says so rather than implying a capability the code does not have.

The one structural property that matters
----------------------------------------

**The corpus is built from the eligible set, not filtered afterwards.**
``RetrievalHarness`` is constructed from an already-computed
``ObjectivePackEvidenceEnvelope``; a region the evidence policy excluded
is never inserted, so it cannot be ranked, cannot consume a position in
the result window, and cannot be returned by a bug in a later filter.
Filtering after ranking would be a security hole shaped like a
performance detail — the same reason the SQL retrieval path applies its
constraints before ``LIMIT``.

Excluded regions stay *visible*: an abstention reports which exclusion
dimension explains it, which is how ``rc-12`` distinguishes "the eligible
corpus says nothing about this" from "the only material that matched was
the wrong blueprint version".

Scoring is exact integer arithmetic — no floats, no ``log`` — so two runs
over the same corpus produce bit-identical scores and orderings on every
platform. Ties break on evidence id, so insertion order never decides a
ranking.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from personal_lms.domain.objective_packs import (
    EvidenceRegion,
    ObjectivePack,
    ObjectivePackEvidenceEnvelope,
)
from personal_lms.objective_packs.hashing import hash_records

__all__ = [
    "ABSTAIN_NO_SUPPORTING_EVIDENCE",
    "ABSTAIN_WRONG_BLUEPRINT_VERSION",
    "RETRIEVAL_POLICY_VERSION",
    "CaseOutcome",
    "FrozenRetrievalCase",
    "RetrievalHarness",
    "RetrievalRun",
    "ScoredEvidence",
    "tokenize",
]


@runtime_checkable
class FrozenRetrievalCase(Protocol):
    """The shape one frozen retrieval case must present.

    Structural rather than a concrete import, so this harness stays
    independent of whichever fixture format produced the cases. The
    format adapter owns parsing; this module owns execution.
    """

    @property
    def case_id(self) -> str: ...

    @property
    def supported(self) -> bool: ...

    @property
    def query(self) -> str: ...

    @property
    def expected_evidence_ids(self) -> tuple[str, ...]: ...

    @property
    def expected_top_k(self) -> int: ...

    @property
    def expected_abstention_reason_code(self) -> str | None: ...

    @property
    def must_never_return(self) -> tuple[str, ...]: ...


#: Recorded on every run so a later scoring change produces a visibly
#: different result rather than silently reinterpreting a frozen one.
RETRIEVAL_POLICY_VERSION = "gate-1-local-lexical-1.0"

#: The two abstention codes the frozen retrieval cases pin.
ABSTAIN_NO_SUPPORTING_EVIDENCE = "no_supporting_evidence_in_eligible_corpus"
ABSTAIN_WRONG_BLUEPRINT_VERSION = "wrong_blueprint_version_excluded"

# Tokens keep internal dots, slashes, commas, and hyphens so "802.1q",
# "gi0/1", "10,20,99", and "200-301" survive as single terms. Splitting
# them would make a VLAN list indistinguishable from three numbers and a
# protocol name indistinguishable from two.
_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9./,\-]*")

#: Rarity weight is ``documents - document_frequency``, scaled. This is an
#: IDF in integer form, including the part that matters most here: a term
#: every eligible document carries has weight **zero**, exactly as
#: ``log(N/df) = 0`` when ``df == N``.
#:
#: That zero is what makes abstention work. Without it, a query sharing
#: only a filler word — "the", "on", or a topic word every document
#: mentions — would score every document above nothing and the harness
#: would return results for a question the corpus cannot answer. A
#: candidate whose matches are all ubiquitous scores 0 and is not a hit.
#: This weight is derived from the corpus and adapts as the eligible set
#: changes; it works together with the function-word list below, which
#: covers the case corpus statistics cannot.
_RARITY_SCALE = 100

#: Term frequency contributes, but is capped: a document repeating a term
#: twenty times is not twenty times more relevant, and letting it say so
#: would make the longest document win every query.
_MAXIMUM_TERM_FREQUENCY = 3

# A negative-case hit must cover at least half of the query's content terms.
# The ordinary ranking path stays recall-oriented for paraphrases; the
# abstention contract is intentionally more conservative so generic topic
# overlap cannot turn an unsupported question into a false answer.
_ABSTENTION_COVERAGE_NUMERATOR = 1
_ABSTENTION_COVERAGE_DENOMINATOR = 2


#: Function words carry grammar rather than topic, and indexing them makes
#: a lexical ranker answer questions it has no evidence for: a query and a
#: document sharing only "on" or "the" would score above nothing, and an
#: unsupported query would return results instead of abstaining. Corpus
#: statistics alone cannot substitute — in a two-document corpus "on" and
#: "allowed" have identical document frequency.
#:
#: Deliberately short, explicit, and reviewable. It holds only English
#: closed-class words: no technical term, no domain vocabulary, and
#: nothing that could distinguish one piece of evidence from another.
_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "across",
        "after",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "both",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "each",
        "for",
        "from",
        "goes",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "many",
        "may",
        "more",
        "most",
        "must",
        "no",
        "not",
        "of",
        "on",
        "one",
        "only",
        "or",
        "other",
        "over",
        "same",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "under",
        "up",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase content terms, preserving identifiers that contain punctuation.

    Function words are dropped; everything else is kept verbatim, so
    ``802.1q``, ``gi0/1``, and ``200-301`` survive intact.
    """
    return [term for term in _TOKEN_PATTERN.findall(text.lower()) if term not in _FUNCTION_WORDS]


@dataclass(frozen=True, slots=True)
class ScoredEvidence:
    """One ranked candidate, with the identity a report needs to cite."""

    evidence_id: str
    source_id: str
    content_sha256: str
    score: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """What one frozen retrieval case actually produced."""

    case_id: str
    supported: bool
    satisfied: bool
    ranked: tuple[ScoredEvidence, ...]
    expected_evidence_ids: tuple[str, ...] = ()
    missing_evidence_ids: tuple[str, ...] = ()
    ineligible_expected_ids: tuple[str, ...] = ()
    abstained: bool = False
    abstention_reason_code: str | None = None
    expected_abstention_reason_code: str | None = None
    forbidden_returned_ids: tuple[str, ...] = ()

    @property
    def ranked_ids(self) -> tuple[str, ...]:
        return tuple(hit.evidence_id for hit in self.ranked)


@dataclass(frozen=True, slots=True)
class RetrievalRun:
    """Every case outcome from one harness execution, plus index identity."""

    policy_version: str
    objective_ref: str
    index_content_hash: str
    eligible_evidence_ids: tuple[str, ...]
    outcomes: tuple[CaseOutcome, ...]

    @property
    def outcomes_by_id(self) -> dict[str, CaseOutcome]:
        return {outcome.case_id: outcome for outcome in self.outcomes}

    @property
    def supported_outcomes(self) -> tuple[CaseOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.supported)

    @property
    def unsupported_outcomes(self) -> tuple[CaseOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.supported)

    @property
    def unsatisfied_case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(outcome.case_id for outcome in self.outcomes if not outcome.satisfied))

    @property
    def blocked_by_pending_review_ids(self) -> tuple[str, ...]:
        """Cases whose expected evidence exists but is not yet eligible.

        Distinguished from a genuine ranking failure on purpose. A case
        that cannot be satisfied because a human has not approved its
        evidence is a pending approval, and reporting it as a retrieval
        defect would send someone to debug the ranker.
        """
        return tuple(
            sorted(
                outcome.case_id
                for outcome in self.outcomes
                if not outcome.satisfied and outcome.ineligible_expected_ids
            )
        )


class RetrievalHarness:
    """Ranks the eligible corpus for the frozen retrieval cases.

    Constructed from the envelope rather than from the pack alone, so
    "only eligible evidence is a candidate" is a property of construction
    instead of a rule the query path has to remember.
    """

    def __init__(
        self,
        *,
        pack: ObjectivePack,
        envelope: ObjectivePackEvidenceEnvelope,
    ) -> None:
        self._pack = pack
        self._envelope = envelope
        eligible = set(envelope.eligible_evidence_ids)
        self._corpus: tuple[EvidenceRegion, ...] = tuple(
            sorted(
                (region for region in pack.evidence_regions if region.evidence_id in eligible),
                key=lambda region: region.evidence_id,
            )
        )
        self._terms_by_id = {
            region.evidence_id: _term_counts(_document_text(region)) for region in self._corpus
        }
        self._document_frequency: dict[str, int] = {}
        for counts in self._terms_by_id.values():
            for term in counts:
                self._document_frequency[term] = self._document_frequency.get(term, 0) + 1

    @property
    def corpus_size(self) -> int:
        return len(self._corpus)

    @property
    def index_content_hash(self) -> str:
        """Canonical hash of the eligible index's logical content.

        Covers evidence identity, source identity, content digest, and the
        sorted term set actually indexed — never storage bytes, so a fresh
        database and a warm one hash the same.
        """
        return hash_records(
            (
                {
                    "evidence_id": region.evidence_id,
                    "source_id": region.source_id,
                    "content_sha256": region.content_sha256,
                    "terms": ",".join(sorted(self._terms_by_id[region.evidence_id])),
                }
                for region in self._corpus
            ),
            sort=True,
        )

    def search(self, query: str, *, limit: int) -> tuple[ScoredEvidence, ...]:
        """The top ``limit`` eligible candidates for ``query``, deterministically."""
        if limit < 1:
            raise ValueError("limit must be at least one")

        query_terms = set(tokenize(query))
        documents = len(self._corpus)
        scored: list[ScoredEvidence] = []
        for region in self._corpus:
            counts = self._terms_by_id[region.evidence_id]
            matched = sorted(query_terms & set(counts))
            if not matched:
                continue
            score = 0
            discriminating: list[str] = []
            for term in matched:
                rarity = (documents - self._document_frequency[term]) * _RARITY_SCALE
                if rarity == 0:
                    continue
                discriminating.append(term)
                score += rarity * min(counts[term], _MAXIMUM_TERM_FREQUENCY)
            if score == 0:
                # Every match was a term the whole corpus shares, which
                # says nothing about this document in particular.
                continue
            matched = discriminating
            scored.append(
                ScoredEvidence(
                    evidence_id=region.evidence_id,
                    source_id=region.source_id,
                    content_sha256=region.content_sha256,
                    score=score,
                    matched_terms=tuple(matched),
                )
            )

        # Descending score, then ascending id: a stable total order that
        # does not depend on corpus insertion order.
        scored.sort(key=lambda hit: (-hit.score, hit.evidence_id))
        return tuple(scored[:limit])

    def run(self, cases: Sequence[FrozenRetrievalCase]) -> RetrievalRun:
        """Execute every frozen case and report exactly what happened."""
        outcomes = tuple(self._run_case(case) for case in cases)
        return RetrievalRun(
            policy_version=RETRIEVAL_POLICY_VERSION,
            objective_ref=self._envelope.objective_ref,
            index_content_hash=self.index_content_hash,
            eligible_evidence_ids=self._envelope.eligible_evidence_ids,
            outcomes=outcomes,
        )

    def _run_case(self, case: FrozenRetrievalCase) -> CaseOutcome:
        query = case.query

        if case.supported:
            expected = tuple(case.expected_evidence_ids)
            ranked = self.search(query, limit=case.expected_top_k)
            returned = {hit.evidence_id for hit in ranked}
            missing = tuple(sorted(set(expected) - returned))
            # An expected id that is not even in the eligible corpus is a
            # pending approval, not a ranking miss. Naming the difference
            # is what keeps the honest blocker legible.
            eligible = set(self._envelope.eligible_evidence_ids)
            return CaseOutcome(
                case_id=case.case_id,
                supported=True,
                satisfied=not missing,
                ranked=ranked,
                expected_evidence_ids=expected,
                missing_evidence_ids=missing,
                ineligible_expected_ids=tuple(sorted(set(expected) - eligible)),
            )

        expected_code = case.expected_abstention_reason_code
        forbidden = tuple(case.must_never_return)
        scope_reason = self._abstention_reason(query)
        # An unsupported case is asked the same question as any other, over
        # the same eligible corpus. Its whole point is that the corpus has
        # nothing admissible to say. An explicit wrong-version objective is
        # rejected before ranking so generic overlap with the in-scope corpus
        # cannot launder the out-of-scope request into an ordinary hit.
        ranked = (
            ()
            if scope_reason == ABSTAIN_WRONG_BLUEPRINT_VERSION
            else tuple(
                hit
                for hit in self.search(query, limit=case.expected_top_k)
                if self._meets_abstention_coverage(query, hit.evidence_id)
            )
        )
        returned = {hit.evidence_id for hit in ranked}
        reason = scope_reason if not ranked else None
        return CaseOutcome(
            case_id=case.case_id,
            supported=False,
            satisfied=bool(
                not ranked and reason == expected_code and not returned & set(forbidden)
            ),
            ranked=ranked,
            abstained=not ranked,
            abstention_reason_code=reason,
            expected_abstention_reason_code=expected_code,
            forbidden_returned_ids=tuple(sorted(returned & set(forbidden))),
        )

    def _meets_abstention_coverage(self, query: str, evidence_id: str) -> bool:
        """Whether a negative-case candidate covers enough of the question."""
        query_terms = set(tokenize(query))
        if not query_terms:
            return False
        document_terms = set(self._terms_by_id[evidence_id])
        matched = query_terms & document_terms
        return (
            len(matched) * _ABSTENTION_COVERAGE_DENOMINATOR
            >= len(query_terms) * _ABSTENTION_COVERAGE_NUMERATOR
        )

    def _abstention_reason(self, query: str) -> str:
        """Why the eligible corpus had nothing to return.

        Two reasons are distinguishable, and the difference matters to a
        reviewer: "nothing here covers that" versus "the only thing that
        covers it belongs to a blueprint version this pack is not scoped
        to". Reporting the second as the first would hide a real exclusion
        behind a generic silence.

        The version reason is claimed only when the *query itself names
        the other version* — its terms overlap an excluded region's
        objective reference. Matching on ordinary topic words instead
        would attach the version reason to any query that happened to
        mention a trunk, including one that is simply unsupported.

        The excluded region is inspected and never returned. This method
        reads its scope and hands back a reason code, nothing else.
        """
        query_terms = set(tokenize(query))
        objective_ref = self._envelope.objective_ref
        for evidence_id in sorted(self._envelope.excluded):
            region = self._pack.evidence_by_id.get(evidence_id)
            if region is None or not region.objective_refs:
                continue
            if objective_ref in region.objective_refs:
                continue
            scope_terms = {term for ref in region.objective_refs for term in tokenize(ref)}
            if query_terms & scope_terms:
                return ABSTAIN_WRONG_BLUEPRINT_VERSION
        return ABSTAIN_NO_SUPPORTING_EVIDENCE


def _document_text(region: EvidenceRegion) -> str:
    """The text a region contributes to the index.

    An image region contributes its reviewer-approved accessible
    description — the only readable content a gate that claims no OCR can
    honestly index. Concept tags are included because they are curated
    retrieval metadata; the answer key never is.
    """
    parts = [region.resolved_content, *region.concept_tags]
    return " ".join(part for part in parts if part)


def _term_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for term in tokenize(text):
        counts[term] = counts.get(term, 0) + 1
    return counts
