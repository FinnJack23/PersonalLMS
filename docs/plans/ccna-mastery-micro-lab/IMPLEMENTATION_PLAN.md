# CCNA Mastery Micro-Lab Implementation Plan

Status: planning complete; implementation not started
Decision: **CONDITIONAL GO for Gate 1 preparation and implementation; NO-GO for
starting the 48-hour clock**
Design source:
`docs/design/ccna-mastery-micro-lab-design/00-CODEX-DESKTOP-AGENT-PROMPT.md`

## Outcome

Add one bounded CCNA mastery feature slice to PersonalLMS. The slice will load a
generic Objective Pack, prove its evidence, run a deterministic and replayable
mastery session, invoke the existing Tutor only for grounded feedback wording,
and use the existing provider/router boundary only when a route is both allowed
and qualified.

This is not a standalone CCNA application. It must not add another router,
retriever, Tutor, provider registry, privacy policy, budget policy, or generic
persistence framework.

## Strategic control-plane north star

The deeper goal is a reusable, transport-neutral multi-agent control plane. The
CCNA slice proves typed task/result boundaries, policy-before-dispatch, least
authority, qualification, append-only events, replay, and evidence-bearing
handoffs. Those contracts may later inform the CML MCP redesign and an
A2A-style edge adapter.

The 48-hour proof adds no A2A or MCP runtime. Any later A2A adapter is an
untrusted external-transmission boundary with separate authentication,
authorization, idempotency, replay protection, deadlines/cancellation, schema
validation, local commit authority, and rights/privacy/redaction/retention/cost
policy. Provider qualification does not qualify a remote agent.

## Non-negotiable implementation rules

1. Tier 0 code owns source eligibility, item selection, grading, follow-up
   mapping, achievement, review status, scheduling, and event replay.
2. An LLM may word a correction only after deterministic code fixes the target,
   observed state, next action, and approved evidence bundle.
3. Graded CCNA sessions may not use Tutor general-knowledge mode.
4. Existing privacy and routing rules run before every provider call. Restricted
   or local-only material never reaches a hosted provider.
5. Fake providers are test-only. A live profile fails closed when provider
   qualification is absent, stale, or incompatible with the task.
6. Objective-specific content remains data. A second objective must not require
   a Python or domain-schema change.
7. Acquisition and retained mastery are different states. Time passing alone
   cannot create retained mastery.
8. Session evidence is append-only and replayable. Corrections supersede prior
   events; they do not overwrite them.
9. Golden artifacts can be created or replaced only through an explicit
   reviewer-acceptance command. Normal gate runs are read-only with respect to
   goldens.
10. The existing Build Week demo in `src/personal_lms/mastery.py` and
    `src/personal_lms/tutor/build_week.py` remains intact and is not promoted to
    the new mastery authority.
11. Domain and mastery logic remains framework-neutral. If CrewAI is used, it
    stays a thin outer adapter following
    `src/personal_lms/adapters/crewai/personal_assistant.py`; it does not own
    deterministic control.

## Start conditions

### Work may begin

Implementation can begin in an isolated worktree once the five planning
artifacts and the design pack are available from the chosen base commit.

Recommended lead branch and worktree:

```text
branch:   codex/ccna-gate-1-objective-pack
worktree: /home/ajsch/projects/personal-lms-codex-ccna-gate1
```

Create it only after Alan decides how the currently untracked design and
planning files will be recorded on the implementation base. No implementation
agent should write in `/home/ajsch/projects/personal-lms`.

### The 48-hour clock may not begin until all are true

- Alan has approved the exact objective and exam-version scope.
- A local searchable-PDF extraction dependency or bounded adapter has been
  selected, declared, and exercised from source bytes. Ambient packages and
  sidecar text are not accepted as extraction.
- The complete `tests/linchpin/` fixture tree described below exists.
- Source bytes, hashes, MIME types, rights/use decisions, blueprint versions,
  selectors, the malicious paragraph, the low-confidence label, and the
  wrong-version distractor are frozen.
- One infographic claim has a recorded human visual review.
- Six required claims, 12 baseline items, six mapped follow-ups, one exit
  probe, the practical scenario, 10 supported retrieval cases, two unsupported
  cases, and four scripted learners have complete reviewed records.
- Expected outputs have been technically reviewed and accepted using the
  reviewer-only golden command.
- Baseline format, lint, type, unit, and integration checks pass in the
  implementation worktree.

Until then, report `clock_status: not_started`; do not spend Gate 1's 12 hours
on content completion or dependency discovery.

## Dependency order

```text
P0 pre-clock freeze
  -> WP1 generic contracts and loader
  -> WP2 local source adapter and evidence eligibility
  -> WP3 Gate 1 assembler, validator, retrieval cases, and CLI
  -> WP4 deterministic study-session contracts
  -> WP5 clean-room trunk simulator
  -> WP6 append-only events, replay, mastery, and scheduling
  -> WP7 grounded-feedback adapter
  -> WP8 Gate 2 closed-loop harness
     -> WP9 live/test route qualification and provider projection
     -> WP10 second-pack factory proof
  -> WP11 final decision report
```

WP5 and the storage half of WP6 may run in parallel after WP4, but agents must
have disjoint file ownership. After Gate 2, WP9 may run in parallel with WP10
once the WP7 `FeedbackWording` schema and the shadow evidence-ready supplement
are frozen. No later gate may compensate for an earlier failure.

## P0 — Pre-clock technical review and fixture freeze

Owner roles: Alan (authority), Claude Opus (technical review), ChatGPT SOL Ultra
(design adjudication)
Timebox: outside the 48-hour gate clock
Stop condition: any required source, item, answer, selector, or expected result
is incomplete or not reviewable

Required fixture tree:

```text
tests/linchpin/
├── fixture-manifest.yaml
├── sources/
│   ├── objective-2.2-synthetic.pdf
│   ├── objective-2.2-infographic.png
│   └── wrong-blueprint-source.pdf
├── packs/
│   ├── objective-2.2/
│   │   ├── claims.yaml
│   │   ├── baseline-items.yaml
│   │   ├── followup-items.yaml
│   │   └── exit-probe-items.yaml
│   └── objective-1.5-shadow/
├── queries/retrieval-cases.json
├── learners/
│   ├── clean-pass.json
│   ├── native-gap.json
│   ├── ambiguous.json
│   └── injection.json
├── expected/
│   ├── evidence-report.json
│   ├── loop-results.json
│   ├── event-and-state-hashes.json
│   └── route-and-factory-report.json
└── schemas/gate-report.schema.json
```

Freeze the following:

- six required claim IDs from the design;
- all source and region hashes plus accessible image descriptions;
- complete item records rather than ID-only placeholders;
- exposure class for every baseline, follow-up, and exit item;
- deterministic misconception-to-follow-up mappings;
- scenario grammar, target invariants, two faults, equivalence cases, reason
  codes, variations, and starting-state hash;
- expected evidence IDs and abstention codes for the 10+2 retrieval cases;
- learner response vectors and exact grade/mastery/schedule outcomes;
- canonical item, claim, scenario, policy, schema, and hash versions;
- allowed execution-profile/provider/task combinations; and
- the reviewer identity and golden-acceptance command contract.

Expected output: a reviewed fixture commit or immutable fixture tree whose
manifest hash is cited by every gate report.

### P0 hash and golden sequencing (authoritative)

P0 freezes only what can be computed without gate code:

- canonical **algorithms** (CLI state hash, `image-region-rgb-v1` region hash,
  normalized-bbox-to-pixel rounding, grounding formula);
- canonical **schemas** (gate-report schema, Objective Pack / evidence /
  learner / scenario record shapes);
- canonical **inputs** (frozen source bytes and their SHA-256, item banks,
  retrieval cases, scripted learner vectors); and
- canonical **target specifications** (per-claim scores, per-learner facet
  targets and outcomes, the executed CLI start/target state hashes).

P0 does **not** invent these, because the code that emits them does not exist
yet:

- the **index-content hash** is generated and reviewer-accepted at the **WP3**
  checkpoint (evidence gate runner + `EvidenceIndexSnapshot`);
- the **event-stream / interrupted-replay hashes** are generated and
  reviewer-accepted at the **WP6** checkpoint (append-only event repository +
  `SessionProjector`); and
- any **gate golden** is written only by the reviewer-only
  `personal-lms ccna-lab gate accept-goldens` command after the corresponding
  gate code runs, under `tests/goldens/ccna-mastery/`. A normal `validate` /
  `gate` / `report` run is read-only with respect to both that accepted-report
  root and the frozen P0 candidate specifications under
  `tests/linchpin/expected/`, and can never mint or rewrite either class of
  artifact.

Until those checkpoints, the affected fields in `fixture-manifest.yaml` and the
`tests/linchpin/expected/*.json` candidate specifications carry an explicit
"frozen at WPn" marker, not a fabricated value. Those candidate specifications
remain part of the exact fixture inventory; `accept-goldens` never writes them.

**Local commit authorization (2026-07-27):** Alan explicitly authorized the one
local P0 baseline commit of the reviewed fixture tree on branch
`claude/p0-fixture-freeze`. This authorization is for that single local commit
only; it does **not** grant push, pull-request, or merge authority.

### P0 scoring arithmetic contract (authoritative)

All facet component means and facet/overall mastery scores are computed with
exact rational (or full-precision Decimal) arithmetic and **no intermediate
rounding**. `ROUND_HALF_UP` to two decimals is applied **only** to stored or
displayed values. A displayed two-decimal facet is an output and is never fed
back as an input to a later computation.

Worked reference (native-gap scripted learner): the exact facets are
C=200/3, T=230/3, K=275/3, V=80, X=100, giving overall
M = 0.15C + 0.20T + 0.30K + 0.25V + 0.10X = 497/6 = 82.8333... → **82.83**.
Computing M from the displayed 2dp facets would give 82.84 and is disallowed.
WP4/WP6 graders and the gate reporter must implement this rule exactly so
reproduced scores match the frozen expected values.

### P0 fixture-extension envelope (authoritative)

The documented domain records (`SourceArtifact`, `EvidenceRegion`, and its
selectors) are strict (`extra="forbid"`). Test-only metadata that those
contracts do not define — source `file_size_bytes`, `magic_bytes_hex`, image
`pixel_dimensions`, and any derived `pixel_box` — must **not** be added as extra
fields on the strict records. It lives in an explicitly declared
fixture-extension envelope (`tests/linchpin/schemas/fixture-extensions.schema.json`),
which is candidate test scaffolding and never a domain record, never sent to a
provider. A derived image `pixel_box` is recomputed from the selector's
normalized `bbox` via the frozen round-half-up rule; it is recorded only in the
review artifact, not in the selector.

## WP1 — Generic Objective Pack contracts and deterministic loader

Gate: 1
Dependencies: P0 schema and fixture decisions
Estimated gate time: 2 hours

Expected production paths:

```text
src/personal_lms/domain/objective_packs.py
src/personal_lms/objective_packs/__init__.py
src/personal_lms/objective_packs/loader.py
src/personal_lms/objective_packs/validation.py
```

Planned symbols:

- `ObjectivePack`
- `ObjectivePackManifest`
- `ApprovedClaim`
- `EvidenceRegion`
- `AssessmentItem`
- `FollowUpRule`
- `MasteryPolicy`
- `ObjectivePackLoader`
- `ObjectivePackValidator`
- `ValidationFinding`

Implementation notes:

- Derive all public models from `domain.base.StrictModel`.
- Use versioned, canonical models; reject unknown fields and unknown IDs.
- Model required modalities/facets as pack data so a no-practical shadow
  objective can run without an objective-specific branch.
- Recompute reference uniqueness, exposure disjointness, coverage, and hashes;
  never trust a pack's declared totals.
- Preserve compatibility of `domain.librarian.GroundingBundle`; use an Objective
  Pack evidence envelope rather than replacing that model.

Expected tests:

```text
tests/unit/domain/test_objective_packs.py
tests/unit/objective_packs/test_loader.py
tests/unit/objective_packs/test_validation.py
```

Acceptance evidence:

- unknown and duplicate IDs fail with stable reason codes;
- 12 baseline records resolve exactly once;
- all follow-up and exit IDs resolve exactly once;
- exposure sets are pairwise disjoint;
- second load produces identical canonical IDs and content hashes; and
- the objective 1.5 shadow pack loads through the same schema.

## WP2 — Local extraction and evidence eligibility adapter

Gate: 1
Dependencies: WP1, approved parser decision
Estimated gate time: 2 hours

Expected existing paths to extend:

```text
src/personal_lms/domain/extraction.py
src/personal_lms/extraction/protocol.py
src/personal_lms/extraction/__init__.py
src/personal_lms/content/protocol.py
src/personal_lms/content/sqlite.py
src/personal_lms/librarian/content_grounding.py
```

Expected new bounded services/adapters:

```text
src/personal_lms/domain/evidence_review.py
src/personal_lms/evidence_review/protocol.py
src/personal_lms/evidence_review/service.py
src/personal_lms/evidence_review/sqlite.py
src/personal_lms/extraction/local_fixture.py
```

Planned symbols:

- `EvidenceReviewDecision`
- `EvidenceReviewRepository`
- `EvidenceReviewService`

- `SourceArtifactExtractor` protocol
- `LocalFixtureExtractor`
- `EvidenceEligibility`
- `EvidencePolicy`
- `EvidenceIndexSnapshot`

Implementation notes:

- The adapter reads frozen source bytes and verifies SHA-256 and MIME before
  extraction.
- PDF text must be extracted from the PDF bytes by the declared local adapter.
  A trusted sidecar cannot stand in for extraction.
- PNG bytes, MIME, dimensions, and region selectors are checked locally; Gate 1
  does not claim OCR. Human-authored labels/descriptions enter eligibility only
  through a persisted reviewer-only `EvidenceReviewDecision`; fixture YAML
  cannot approve itself.
- Reuse source inventory, promotion, catalog, content, and FTS5 contracts where
  their behavior fits. Do not compose their current SQLite implementations into
  one database until their unnamespaced `schema_migrations` tables are fixed.
- Apply rights, use, privacy, review, objective version, and quarantine filters
  before the retrieval limit.
- Use `KnowledgeScope.objective_framework` for exact 48-hour objective/version
  scope. The Objective Pack is not a second RAG “knowledge pack.”
- Keep Gate 1 lexical and local. Embeddings and a vector database are out.

Expected tests:

```text
tests/unit/extraction/test_local_fixture.py
tests/unit/content/test_objective_eligibility.py
tests/unit/librarian/test_objective_pack_grounding.py
tests/unit/objective_packs/test_source_and_region_resolution.py
```

Acceptance evidence:

- source/page/region resolution table;
- visual-review decision for one infographic claim;
- quarantine and rights/use exclusion report;
- wrong-blueprint and malicious-region exclusion;
- deterministic eligible-index content hash; and
- zero provider/network calls.

## WP3 — Gate 1 assembler, validator, retrieval harness, and CLI

Gate: 1
Dependencies: WP1–WP2
Estimated gate time: 8 hours

Expected new feature-adapter paths:

```text
src/personal_lms/labs/__init__.py
src/personal_lms/labs/ccna_mastery/__init__.py
src/personal_lms/labs/ccna_mastery/wiring.py
src/personal_lms/labs/ccna_mastery/cli.py
src/personal_lms/labs/ccna_mastery/gates.py
```

Expected existing path to extend:

```text
src/personal_lms/cli.py
```

Planned symbols:

- `build_ccna_mastery_use_case`
- `register_ccna_lab_commands`
- `EvidenceGateRunner`
- `GateCheck`
- `GateReport`
- `GoldenArtifactGuard`

CLI shape:

```text
personal-lms ccna-lab ingest ...
personal-lms ccna-lab evidence approve-region ...  # reviewer-only
personal-lms ccna-lab validate ...
personal-lms ccna-lab gate evidence ...
personal-lms ccna-lab gate report ...
personal-lms ccna-lab gate accept-goldens ...  # reviewer-only proposal
```

The exact golden command requires Alan's approval. Normal `validate`, `gate`,
and `report` commands must refuse any golden write. `wiring.py` accepts the
existing root-composed router, registry, budget policy, content/grounding
services, and `TutorTeachingCoordinator`; it may not construct duplicates.

Expected tests:

```text
tests/unit/labs/ccna_mastery/test_cli.py
tests/unit/labs/ccna_mastery/test_gates.py
tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py
tests/unit/test_cli.py
```

Acceptance evidence:

- all Gate 1 rows in `LINCHPIN_TRACEABILITY.md` pass;
- 10 supported queries return eligible evidence within top five;
- two unsupported queries return the exact abstention reason;
- a second fresh-database run produces identical IDs and index hashes;
- offline wall time is below five minutes; and
- existing no-argument, version, `ask`, and `build-week-demo` CLI tests remain
  green.

Gate 1 stop rule: stop immediately on any unresolved citation, unapproved
answer-bearing evidence, wrong-version/injected region leak, declared rather
than recomputed coverage, or a need for more than four focused hours of manual
cleanup.

## WP4 — Deterministic study-session contracts

Gate: 2
Dependencies: Gate 1 pass
Estimated gate time: 3 hours

Expected paths:

```text
src/personal_lms/domain/study_sessions.py
src/personal_lms/study_sessions/__init__.py
src/personal_lms/study_sessions/grading.py
src/personal_lms/study_sessions/selection.py
```

Planned symbols:

- `StudySession`
- `SessionPhase`
- `GradeResult`
- `GradeDisposition`
- `FacetScore`
- `DeterministicItemSelector`
- `DeterministicGrader`

Expected tests:

```text
tests/unit/domain/test_study_sessions.py
tests/unit/study_sessions/test_grading.py
tests/unit/study_sessions/test_selection.py
```

Acceptance evidence:

- exactly 12 baseline items;
- zero to six follow-ups from frozen mappings only;
- one bounded adaptive round, one practical scenario, and one exit probe;
- ambiguity becomes `review_required`, never an averaged numeric grade; and
- fake/model outputs cannot affect any authoritative field.

## WP5 — CCNA trunk-simulator adapter

Gate: 2
Dependencies: WP4 and approved scenario fixture
Estimated gate time: 4 hours

Expected paths:

```text
src/personal_lms/labs/protocol.py
src/personal_lms/labs/ccna_mastery/simulator.py
src/personal_lms/labs/ccna_mastery/scenario.py
```

Planned symbols:

- generic `PracticalAssessmentAdapter` protocol
- `TrunkScenario`
- `TrunkCommandParser`
- `TrunkState`
- `TrunkScenarioGrader`
- `CcnaTrunkPracticalAdapter`

The simulator is a deterministic clean-room state machine, not IOS emulation.
Only the reviewed grammar is accepted. Command aliases/orderings normalize to a
canonical transition. An invalid command returns a reason code and no state
mutation.

Expected tests:

```text
tests/unit/labs/test_protocol.py
tests/unit/labs/ccna_mastery/test_simulator.py
tests/unit/labs/ccna_mastery/test_trunk_scenario_equivalence.py
```

Acceptance evidence:

- both correct command orders reach the same state hash and grade;
- each invalid command leaves the prior hash unchanged;
- exactly two planted faults are observable and repairable; and
- scenario grades contain deterministic reason/grade IDs.

## WP6 — Append-only events, replay, mastery, and scheduling

Gate: 2
Dependencies: WP4; consumes WP5 practical result
Estimated gate time: 6 hours

Expected paths:

```text
src/personal_lms/study_sessions/protocol.py
src/personal_lms/study_sessions/sqlite.py
src/personal_lms/study_sessions/events.py
src/personal_lms/study_sessions/replay.py
src/personal_lms/study_sessions/mastery.py
src/personal_lms/study_sessions/scheduling.py
```

Planned symbols:

- `StudySessionRepository`
- `SQLiteStudySessionRepository`
- `LearningEvent`
- `EventEnvelope`
- `SessionProjector`
- `MasteryEvaluator`
- `ReviewScheduler`
- `FrozenClock`

Implementation notes:

- Use append-only inserts and monotonic per-session sequence numbers.
- Store canonical payload hash, prior-event hash, policy version, grade IDs,
  and fixture/pack version.
- A correction is a new superseding event.
- Projection is a pure fold; persisted snapshots are caches, not authority.
- `acquired` schedules later review. Only a successful delayed novel attempt
  may create `retained_mastery`.
- Leave `mastery.SQLiteMasteryStore` unchanged; its `INSERT OR REPLACE`
  behavior is incompatible with this gate.

Expected tests:

```text
tests/unit/study_sessions/test_sqlite.py
tests/unit/study_sessions/test_replay.py
tests/unit/study_sessions/test_mastery.py
tests/unit/study_sessions/test_scheduling.py
tests/unit/labs/ccna_mastery/test_interrupted_replay.py
```

Acceptance evidence:

- grade reproduction from grade IDs plus policy version;
- uninterrupted and interrupted runs have identical event sequences, final
  state hashes, facet scores, achievement, review status, and due windows;
- clean-pass is `acquired`, never retained;
- advancing `FrozenClock` alone never promotes mastery; and
- SQLite integrity and append-only invariants pass.

## WP7 — Grounded feedback through the existing Tutor

Gate: 2
Dependencies: WP4 and eligible Gate 1 evidence
Estimated gate time: 2 hours

Expected path:

```text
src/personal_lms/study_sessions/feedback.py
```

Existing paths reused without a second Tutor:

```text
src/personal_lms/tutor/coordinator.py
src/personal_lms/tutor/_generation.py
src/personal_lms/source_verification/protocol.py
src/personal_lms/policies/router.py
```

Planned symbols:

- `GroundedFeedback`
- `GroundedFeedbackService`
- `FeedbackWording`

Use `TutorTeachingCoordinator` in supplied-bundle mode. The adapter exposes no
general-knowledge flag. Existing Tutor citation/source checks must pass and
`refusal_reason` must be absent before the provider-controlled explanation is
accepted as `FeedbackWording.correction`. Deterministic code then constructs
`GroundedFeedback(target, observed, correction, next_action, evidence_ids)`.
Source verification remains an optional fail-closed correction check, never an
Evaluator or mastery authority.

Expected tests:

```text
tests/unit/study_sessions/test_feedback.py
tests/integration/test_ccna_tutor_reuse.py
```

Acceptance evidence:

- zero fresh retrieval calls for a supplied pack bundle;
- only approved trusted chunks enter the prompt;
- no general-knowledge mode in a graded session;
- fake replacement changes wording at most; and
- provider failure may append attempt/refusal and deterministic phase events,
  but cannot mutate item, grade, mastery, achievement, or schedule authority.

## WP8 — Gate 2 closed-loop and replay harness

Gate: 2
Dependencies: WP4–WP7
Estimated gate time: 5 hours

Expected paths:

```text
src/personal_lms/study_sessions/runner.py
src/personal_lms/labs/ccna_mastery/wiring.py
tests/unit/labs/ccna_mastery/test_gate_2_loop.py
tests/unit/labs/ccna_mastery/test_scripted_learners.py
```

Planned symbols:

- `BoundedStudySessionRunner`
- `CcnaMasteryUseCase` protocol/adapter
- `LoopGateRunner`

Read-only accepted goldens:

- `tests/linchpin/expected/loop-results.json`;
- `tests/linchpin/expected/event-and-state-hashes.json`.

Observed evidence is written under `var/ccna-mastery/gates/<run-id>/`, never
under `tests/linchpin/expected/`. It includes:
- one run record for each scripted learner;
- explicit maximum-count assertions; and
- all Gate 2 rows in `LINCHPIN_TRACEABILITY.md`.

Gate 2 stop rule: stop on any model authority, nondeterministic replay,
unbounded loop, silent ambiguity, invalid-command mutation, or retained mastery
without delayed novel evidence.

## WP9 — Execution-profile isolation and provider qualification

Gate: 3 route isolation
Dependencies: stable WP7 `FeedbackWording` schema and accepted Gate 2
Estimated gate time: 6 hours

Expected new generic paths:

```text
src/personal_lms/domain/provider_qualification.py
src/personal_lms/provider_qualification/__init__.py
src/personal_lms/provider_qualification/protocol.py
src/personal_lms/provider_qualification/sqlite.py
src/personal_lms/provider_qualification/policy.py
src/personal_lms/provider_qualification/ollama_inspector.py
src/personal_lms/providers/projection.py
```

Expected existing paths to extend narrowly:

```text
src/personal_lms/composition.py
src/personal_lms/policies/router.py
```

Planned symbols:

- `ExecutionProfile`
- `ProviderRegistrationDescriptor`
- `ProviderQualification`
- `ProviderQualificationRepository`
- `ProviderEligibilityPolicy`
- `OllamaQualificationInspector`
- `CanonicalResultProjector`
- `RoutePolicySnapshot`

Implementation notes:

- Root composition injects the execution profile, deployment-owned provider
  descriptors, and qualification snapshot. Providers cannot self-qualify.
- Keep `ProviderRegistry` a structural in-memory directory. Live composition
  omits fakes as defense in depth; `ProviderEligibilityPolicy` used before
  existing router ranking rejects test-only, incompatible, absent, or expired
  registrations.
- Honor `ModelRequest.capability_profile` exactly and require compatible task
  class, canonical `FeedbackWording` schema ID, structured-output behavior, and
  current measured qualification.
- Retain current Tier 0, local-first, privacy, static budget, deterministic
  ranking, and no-retry/no-fallback behavior. Spend-ledger work is one-week
  scope, not a Gate 3 requirement.
- Keep provider SDK objects and provider-specific schemas outside the domain.
  Revalidate provider projection into `FeedbackWording`, never authoritative
  `GroundedFeedback`.
- `OllamaQualificationInspector` composes public discovery/health/generation
  seams and an injected runtime descriptor; it does not expose private adapter
  state through the generic provider protocol.
- If Ollama is available, record resolved digest, quantization, runtime, and
  smoke status. Otherwise the Qwen smoke alone is `deferred`.

Expected tests:

```text
tests/unit/domain/test_provider_qualification.py
tests/unit/provider_qualification/test_policy.py
tests/unit/provider_qualification/test_sqlite.py
tests/unit/provider_qualification/test_ollama_inspector.py
tests/unit/providers/test_projection.py
tests/unit/policies/test_router.py
tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py
```

Acceptance evidence:

- fake selection is impossible in live;
- missing/expired qualification fails closed;
- provider output cannot change authoritative fields;
- no SDK object enters a domain model;
- projected output is revalidated against the canonical Pydantic model; and
- all pre-existing router/privacy/budget/provider isolation tests pass.

Any required route-isolation failure blocks the August 1 trial.

## WP10 — Second-pack factory proof

Gate: 3 factory scalability
Dependencies: Gate 1 generic loader/validator and Gate 2 generic runner
Estimated gate time: 6 hours

Expected changes are limited to fixture/content data and the factory test:

```text
tests/linchpin/packs/objective-1.5-shadow/factory-supplement.yaml
tests/linchpin/packs/objective-1.5-shadow/
tests/unit/labs/ccna_mastery/test_gate_3_factory.py
```

The immutable Gate 1/2/route manifest does not change. The supplement pins
factory criteria, evidence-ready inputs, start time, and final authored-pack
hash; it does not pre-author the shadow bank or a factory golden. Observed
factory output goes under `var/ccna-mastery/gates/<run-id>/`.

No production Python or domain-schema change is allowed during this measured
work package. The clock starts only after shadow-objective evidence is ready.

Acceptance evidence:

- objective v1.1 1.5 “Compare TCP to UDP” loads with the same loader;
- pack assembly takes no more than three focused hours after evidence readiness;
- the same validator and runner complete the diagnostic;
- absence of policy-required practical evidence yields an honest
  incomplete/developing result and refuses operational mastery; and
- all Gate 3 factory rows in `LINCHPIN_TRACEABILITY.md` pass.

A factory-only failure blocks the several-objectives-per-day scale claim but
does not by itself block the safe one-objective August 1 trial.

## WP11 — Final decision report

Gate: final
Dependencies: Gates 1–3 reports
Estimated gate time: 4 hours

Expected paths:

```text
src/personal_lms/labs/ccna_mastery/reporting.py
tests/unit/labs/ccna_mastery/test_gate_decision.py
```

The report must preserve separate statuses for:

- Gate 1 evidence;
- Gate 2 closed loop;
- Gate 3 route isolation;
- Gate 3 factory scalability; and
- optional Qwen smoke.

It must derive two decisions:

1. `august_1_one_objective_trial`: requires Gates 1, 2, and route isolation;
2. `several_objectives_per_day_scale`: additionally requires factory
   scalability.

Expected output:

```text
tests/linchpin/expected/route-and-factory-report.json
```

The final implementation report must include base/head commits, changed paths,
commands and exact results, gate rows and evidence hashes, deferrals, focused
time, unresolved risks, and explicit review requests.

## Two-day cut line

Inside the 48-hour proof:

- exact synthetic PDF/PNG fixture extraction and visual approval;
- Objective Pack loader, strict validator, claim evidence scoring, FTS5
  eligibility, and 10+2 retrieval cases;
- deterministic 12-item loop, zero-to-six mapped follow-ups, one practical,
  one exit, acquisition decision, schedule, append-only events, and replay;
- grounded correction wording through the existing Tutor;
- fake/live isolation and static qualification enforcement;
- optional Qwen smoke, which alone may be deferred;
- one reviewed 12-item shadow pack and factory measurement; and
- separate August 1 and scaling decisions.

Explicitly outside the two-day cut:

- real private WGU/course ingestion;
- production Docling/figure extraction;
- vector search or a vector database;
- browser UI, containers, MCP, A2A, LangGraph, or another agent framework;
- hosted-model fallback or a repair loop;
- unrestricted shells/filesystems;
- full CCNA catalog;
- retained mastery without a delayed novel attempt; and
- GitHub publication or deployment.

## One-week cut line

After the linchpin proof, and only if the relevant gate passes:

- local private-source Docling/figure-region ingestion through the same
  extraction protocol;
- source-rights and D419 review completion;
- expansion from 10+2 to at least 20 labeled retrieval cases;
- real installed-Qwen qualification with pinned digest/runtime/quantization;
- route health/cooldown and durable spend-ledger integration;
- delayed novel retention attempts and retest-bank comparison;
- additional reviewed Objective Packs through the generic factory;
- operational hardening of shared SQLite composition/migrations, WAL, foreign
  keys, backup, and recovery;
- optional thin CrewAI application adapter if the current orchestration
  requirement needs it; and
- authorized GitHub review/merge workflow.

The one-week work may harden or scale a passing proof; it may not reinterpret a
failed 48-hour core criterion as deferred.

## Agent workflow and checkpoints

Use one writer per path and one isolated worktree per active coding agent.

- ChatGPT 5.6 SOL Ultra: architecture decisions, phase prompts, evidence review,
  and next-loop planning; no implementation commits.
- Claude Opus: Gate 1 hard implementation and all safety/review/smoke gates.
- Claude Sonnet: routine, tightly specified coding or GitHub work after a gate
  contract is stable and Alan authorizes it.
- Codex SOL High: independent bounded implementation slices on a separate
  branch with disjoint file ownership.
- GPT-5.6 Luna: GitHub operations only after explicit authorization.
- Alan: source rights, technical content approval, golden acceptance, branch
  integration, and final go/no-go authority.

Every coding phase must stop at these checkpoints:

1. scope and file-ownership confirmation;
2. red tests or fixture-validation evidence;
3. smallest implementation;
4. focused tests;
5. full required checks;
6. diff/status/secrets review;
7. structured report back to ChatGPT SOL High.

No agent should self-merge, rewrite a golden, broaden content scope, or silently
continue into a later gate.

## Required validation commands

Run from a clean implementation worktree:

```bash
uv run --no-sync ruff check --no-cache .
uv run --no-sync ruff format --check --no-cache .
uv run --no-sync mypy --no-incremental src
uv run --no-sync pytest -p no:cacheprovider
git diff --check
git status --short
```

If an approved extraction dependency changes `pyproject.toml` or `uv.lock`, run
the equivalent synced environment checks and record the exact command. Never
claim a check passed if it was not run.
