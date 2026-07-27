# CCNA Mastery Micro-Lab Architecture Delta

Status: repository-reconciled planning; no implementation exists
Decision: extend PersonalLMS as one bounded feature slice
Primary constraint: no standalone CCNA application and no duplicate control
services

## Architectural judgment

PersonalLMS already has the correct outer safety seams: deterministic routing,
provider-neutral inference, local Ollama and fake adapters, privacy
classification, budget policy, staged source promotion, SQLite/FTS5 content,
grounded Tutor coordination, citation verification, and optional CrewAI
wrapping. The CCNA design should reuse those seams.

The repository does not yet have a generic Objective Pack, claim-specific
evidence policy, deterministic assessment/session loop, practical-adapter
protocol, append-only learning events, replay projection, acquisition/retention
policy, schedule history, execution profiles, or expiring provider
qualification. Those are genuine bounded additions, not services to simulate by
renaming the Build Week demo.

## Strategic control-plane boundary

The CCNA lab is a bounded PersonalLMS learning-control slice. Its reusable core
is not “CCNA code”; it is:

- typed task, result, authority, evidence, and policy envelopes;
- deterministic policy checks before dispatch;
- least-authority execution;
- provider qualification and expiry;
- canonical structured-result revalidation;
- append-only events and deterministic replay;
- explicit human approval boundaries; and
- evidence-bearing handoffs and decision reports.

Those contracts may later inform the CML MCP redesign. A future A2A adapter is
not justified until the local gates pass and a second deployed consumer proves
the need. The 48-hour implementation remains transport-neutral and exposes
only a stable use-case port, not repositories, event stores, or internal
handlers.

Any later A2A adapter is an untrusted external boundary. It needs independent
remote-agent trust, authentication, authorization, idempotency and duplicate
suppression, replay protection, deadlines/cancellation, schema/version
negotiation, and local commit authority. Transmission must also pass rights,
privacy/redaction, retention, approval, and cost policy. Provider
qualification does not qualify a remote agent. No current dependency or test
establishes these controls.

## Current request path and proposed extension

```text
existing personal-lms CLI
  -> bounded CCNA use-case port
    -> feature wiring supplied by root composition
    -> generic ObjectivePack loader + validator
    -> existing source/promotion/content/FTS5 services
    -> deterministic study-session runner
       -> new practical adapter protocol
       -> CCNA trunk clean-room adapter
       -> new append-only event repository + projector
       -> new mastery evaluator + scheduler
       -> existing Tutor coordinator, supplied-bundle mode
          -> existing privacy/budget/router/registry/provider path
    -> gate report with immutable evidence hashes
```

An optional CrewAI adapter may call the bounded use-case port after the
orchestration decision is resolved. A future A2A adapter may call only that
same port after its external-boundary controls are proven. Neither may insert
itself between the runner and its deterministic authorities.

## Component reconciliation

| Proposed concern | Actual PersonalLMS seam | Decision | Narrow delta |
|---|---|---|---|
| Strict domain boundaries | `src/personal_lms/domain/base.py::StrictModel` | Reuse directly | All new boundary records derive from it. |
| Objective/version scope | `src/personal_lms/domain/knowledge_scope.py::KnowledgeScope` | Reuse and compose | Use `objective_framework` for exact objective/version scope; keep Objective Pack identity separate from the existing provisional RAG `knowledge_packs` filter. |
| Objective Pack | No current equivalent | New generic bounded module | Add versioned contracts, deterministic loader, and validator under `domain/objective_packs.py` and `objective_packs/`. No CCNA fields in the generic schema. |
| Source inventory | `source_inventory/protocol.py`, `source_inventory/sqlite.py` | Reuse | Add safe byte registration at an adapter/service boundary; do not turn `SourceReadinessImporter` into production ingestion. |
| Extraction queue and artifacts | `extraction/protocol.py`, `extraction/sqlite.py`, `extraction/fake.py` | Extend | Add an extractor protocol and one bounded local searchable-PDF/PNG fixture adapter. `FakeExtractor` stays test-only. |
| Rights and approval | `promotion/eligibility.py`, `promotion/service.py`, domain promotion/source records | Reuse plus versioned use decision | Preserve human approval, rights, privacy non-downgrade, idempotency, and recovery. Add allowed-use decisions such as local extract/index/teach rather than another promotion service. |
| Catalog | `catalog/protocol.py`, `catalog/sqlite.py` | Reuse | No CCNA catalog. Preserve provenance and exact technical-token retrieval behavior. |
| Content and lexical retrieval | `content/protocol.py::ContentRepository`, `content/sqlite.py::SQLiteContentRepository` | Reuse and narrow extension | Keep FTS5. Apply trust, rights/use, privacy, objective/version, and quarantine eligibility before result limiting; produce deterministic index hashes. |
| Grounding request and bundle | `domain/librarian.py::{LibrarianRetrievalRequest, RetrievedEvidence, GroundingBundle}` | Reuse compatibly | Do not replace models protected by old-shaped/round-trip tests. Add an Objective Pack evidence envelope and only backward-compatible generic fields if unavoidable. |
| Grounding service | `librarian/content_grounding.py::LibrarianContentGroundingService` | Reuse and extend | Add exact pack/objective eligibility, region provenance, retrieval trace, and claim-specific support calculation. Do not create CCNA RAG. |
| Evidence sufficiency | Existing `GroundingBundle.is_sufficient` is only “one trusted hit” | New policy above existing bundle | Recompute per-claim support factors and score; never trust declared YAML coverage or score. |
| Evidence review | No generic decision repository/service | New generic bounded module | Persist reviewer-only text and image-region decisions; PNG validation covers bytes, MIME, dimensions, regions, and human-reviewed labels, not OCR. |
| Tutor | `tutor/coordinator.py::TutorTeachingCoordinator` and `tutor/evidence_checked.py::EvidenceCheckedTutorService` | Reuse directly | Use supplied-bundle mode for graded feedback; prohibit general-knowledge mode; add only a typed feedback adapter. |
| Source verification | `source_verification/protocol.py::SourceVerifier` and `model_backed.py` | Reuse optionally | Fail-closed semantic check of correction wording only. It is not the grader, evaluator, evidence scorer, or mastery authority. |
| Model request/result | `domain/models.py::{ModelRequest, ModelResult}` | Reuse provider boundary; wrap at application edge | Add a canonical structured task/result projection layer without placing provider schemas or SDK objects in domain models. |
| Provider protocol | `providers/protocol.py::ModelProvider` | Reuse | No CCNA provider. |
| Provider registry | `providers/registry.py::ProviderRegistry` | Reuse unchanged as a structural registry | Do not put execution eligibility or self-authorization in registration. Preserve deterministic registration-order independence. |
| Provider registration metadata | No current deployment-owned descriptor | New composition input | A `ProviderRegistrationDescriptor` declares deployment-owned profile/task/schema constraints; providers and fakes cannot self-qualify. |
| Fake providers | `providers/fake.py::{FakeLocalProvider, FakeHostedProvider}` | Reuse unchanged in tests | Test composition supplies test-only descriptors; live composition never supplies a fake-eligible descriptor. |
| Ollama/Qwen | `providers/ollama/` | Reuse through a narrow qualification inspector | Build on loopback controls, injected client, discovery, digest/quantization metadata, and one-request/no-retry behavior. The inspector/descriptor supplies identity without leaking provider SDK types into the domain. |
| Deterministic routing | `policies/router.py::DeterministicRouter` | Reuse with exact profile matching and injected eligibility | Match `ModelRequest.capability_profile` exactly, then apply an injected `ProviderEligibilityPolicy` before existing ranking; retain Tier 0, local-first, privacy, static budget, deterministic ranking, and no retry/fallback. |
| Privacy | `domain/privacy.py::PrivacyClassification` and current SQL/router propagation | Reuse directly | No CCNA privacy enum or bypass. Only minimized approved evidence may cross any provider boundary. |
| Budget | `domain/budgets.py::BudgetPolicy` and router zero-limit handling | Reuse unchanged for the proof | Keep static zero-hosted-spend enforcement. A spend ledger is one-week work and is not introduced during route isolation. |
| Personal assistant flow | `flows/personal_assistant.py::PersonalAssistantFlow` | Preserve | It routes one request and is not a study-session engine. Do not overload or break `run()`. |
| CrewAI boundary | `adapters/crewai/personal_assistant.py` and ADR-0001 | Reuse pattern only | Framework-neutral application service owns logic; an optional thin CrewAI adapter may delegate later. |
| Assessment item selection | No suitable current service | New generic module | Deterministic, bounded, data-driven selection with exposure-set enforcement. |
| Grading | No suitable current service | New generic module | Exact grade/reason IDs and ambiguity disposition. LLM output has no grade authority. |
| Practical lab | No current protocol or simulator | New generic protocol plus CCNA adapter | Add `PracticalAssessmentAdapter`; implement one clean-room trunk state machine. Do not emulate IOS or create a general network simulator. |
| Mastery | `src/personal_lms/mastery.py` is a Build Week `INSERT OR REPLACE` store | New bounded semantics; leave legacy intact | Add acquisition/retention policy and facet decisions under `study_sessions/`; reuse Pydantic/SQLite patterns only. |
| Session/event persistence | No append-only learning-event repository | New bounded repository | Append-only events, supersession, chained/canonical hashes, replay projection, and schedule history. This is feature persistence within PersonalLMS, not a new persistence framework. |
| CLI | `src/personal_lms/cli.py::build_parser/main` | Extend safely | Register nested `ccna-lab` commands from a handler module; preserve all current public behavior and console entry point. |
| Gate reporting | No equivalent | New bounded harness | Canonical Pydantic report, pass/fail/deferred semantics, immutable expected artifacts, and separate trial/scaling decisions. |
| A2A/MCP transport | No current dependency, trust model, or deployed consumer | Post-gate spike only | After a second deployed consumer exists, map only the stable use-case port and prove external trust, policy, delivery, replay, deadline, and local-commit controls. No protocol dependency in the 48-hour slice. |

## Exact production paths expected to change

### New generic domain and services

```text
src/personal_lms/domain/objective_packs.py
src/personal_lms/domain/study_sessions.py
src/personal_lms/domain/provider_qualification.py
src/personal_lms/domain/evidence_review.py
src/personal_lms/objective_packs/__init__.py
src/personal_lms/objective_packs/loader.py
src/personal_lms/objective_packs/validation.py
src/personal_lms/evidence_review/__init__.py
src/personal_lms/evidence_review/protocol.py
src/personal_lms/evidence_review/service.py
src/personal_lms/evidence_review/sqlite.py
src/personal_lms/extraction/local_fixture.py
src/personal_lms/study_sessions/__init__.py
src/personal_lms/study_sessions/protocol.py
src/personal_lms/study_sessions/grading.py
src/personal_lms/study_sessions/selection.py
src/personal_lms/study_sessions/events.py
src/personal_lms/study_sessions/sqlite.py
src/personal_lms/study_sessions/replay.py
src/personal_lms/study_sessions/mastery.py
src/personal_lms/study_sessions/scheduling.py
src/personal_lms/study_sessions/feedback.py
src/personal_lms/study_sessions/runner.py
src/personal_lms/provider_qualification/__init__.py
src/personal_lms/provider_qualification/protocol.py
src/personal_lms/provider_qualification/sqlite.py
src/personal_lms/provider_qualification/policy.py
src/personal_lms/provider_qualification/ollama_inspector.py
src/personal_lms/providers/projection.py
```

### New bounded lab adapter

```text
src/personal_lms/labs/__init__.py
src/personal_lms/labs/protocol.py
src/personal_lms/labs/ccna_mastery/__init__.py
src/personal_lms/labs/ccna_mastery/wiring.py
src/personal_lms/labs/ccna_mastery/cli.py
src/personal_lms/labs/ccna_mastery/gates.py
src/personal_lms/labs/ccna_mastery/scenario.py
src/personal_lms/labs/ccna_mastery/simulator.py
src/personal_lms/labs/ccna_mastery/reporting.py
```

This package owns only CCNA wiring, fixture/gate entry points, and the trunk
adapter. `wiring.py` accepts shared PersonalLMS dependencies and exposes a
stable use-case port; root composition owns repository and provider lifecycles.
The package does not own generic source, retrieval, Tutor, routing, provider,
privacy, budget, event, mastery, or scheduling implementations.

### Existing paths with narrow edits

```text
src/personal_lms/cli.py
src/personal_lms/extraction/protocol.py
src/personal_lms/extraction/__init__.py
src/personal_lms/content/protocol.py
src/personal_lms/content/sqlite.py
src/personal_lms/librarian/content_grounding.py
src/personal_lms/policies/router.py
```

`pyproject.toml` and `uv.lock` change only for Alan's approved parser.
Content/FTS changes are the smallest filter and evidence-envelope delta;
region/claim records stay in that envelope. The router changes only for exact
`capability_profile` matching and injected eligibility when current seams
cannot supply them. Budget schemas, routing-decision schemas, registry, fakes,
and Ollama adapters remain unchanged unless a test proves an unavoidable
minimal edit; any broad extractor or schema migration triggers a stop.

These are expected paths, not permission for one agent to edit all of them.
Implementation prompts must assign disjoint path ownership and keep diffs
smaller than this maximum inventory.

## Authority model

| Decision | Sole authority | Provider/agent allowance |
|---|---|---|
| Source eligible for extraction/index/teaching | rights, approval, privacy, and evidence policies | May summarize an already eligible region; may not authorize it. |
| Text/image-region review | `EvidenceReviewService` plus authenticated human reviewer | Providers may propose wording; only a reviewer decision authorizes the evidence label. |
| Objective/item references valid | `ObjectivePackValidator` | None. |
| Retrieval result eligible | content query plus evidence policy | May not reintroduce filtered material. |
| Item selected | `DeterministicItemSelector` | None. |
| Answer grade/reason | `DeterministicGrader` or practical adapter | None. |
| Follow-up selected | frozen `FollowUpRule` mapping | None. |
| Practical state/grade | deterministic practical adapter | None. |
| Mastery/review status | `MasteryEvaluator` | None. |
| Due window | `ReviewScheduler` | None. |
| State projection | `SessionProjector` over append-only events | None. |
| Correction wording | deterministic feedback adapter after bundle/citation/source checks | Provider output maps only to `FeedbackWording.correction`; deterministic code constructs `GroundedFeedback`. |
| Provider selection | router plus injected `ProviderEligibilityPolicy` and deployment descriptor | Provider cannot select or qualify itself, or bypass exact profile/policy checks. |
| Provider failure | append-only attempt/refusal/phase events | Cannot mutate grade, item selection, mastery, or schedule authority projections. |
| Golden replacement | human reviewer command | No normal agent/gate run. |
| Gate decision | deterministic gate report, reviewed by Alan | Agents provide evidence; they do not waive failed checks. |

This authority matrix—not byte-for-byte equality of whole event streams—is the
comparison contract between fake and no-provider runs. It is also the future
control-plane contract: an A2A agent may request or report work, but its message
cannot grant itself authority that the local application contract does not
contain.

## Objective Pack versus current grounding contracts

`GroundingBundle` is a runtime retrieval result and has backward-compatible
serialized forms protected by:

- `tests/unit/domain/test_librarian.py::test_retrieved_evidence_old_shaped_payload_without_new_fields_still_validates`
- `tests/unit/domain/test_librarian.py::test_grounding_bundle_json_round_trip`

The Objective Pack is a versioned assessment definition. It should contain
approved claim references, item records, deterministic follow-up mappings,
modality/facet requirements, and mastery policy. It should not embed a second
retrieval engine.

The planned `ObjectivePackValidator` resolves evidence through the existing
content/retrieval seams and emits a separate evidence report/envelope. Tutor
then consumes a compatible supplied `GroundingBundle` containing only eligible
chunks. This preserves current callers while enabling claim-specific proof.

## Retrieval and evidence delta

Current strengths to preserve:

- `SQLiteContentRepository` uses parameterized FTS5;
- privacy is applied in SQL before `LIMIT`;
- results are ordered deterministically;
- chunks preserve source/document/chunk/page/section provenance;
- a trusted chunk requires a reviewed parent; and
- Tutor prompt construction strips every untrusted or empty chunk.

Gate 1 additions:

- exact source-byte/MIME/hash verification;
- PNG byte, MIME, dimension, region, image-hash, accessible-description, and
  human-reviewed-label validation; this gate does not claim OCR;
- rights/use, quarantine, objective/version, and trust eligibility before
  ranking;
- claim-specific support factors and recomputed grounding score;
- material-conflict and unsupported-query abstention reason codes;
- retrieval trace and deterministic index-content hash; and
- exact 10 supported plus 2 unsupported cases.

FTS5 is sufficient for the 48-hour proof. The design document's broader
20-query requirement is the one-week hardening set. No vector database is
justified.

## Routing and provider delta

`DeterministicRouter` currently protects Tier 0 short-circuiting, local
preference, privacy-forced local behavior, local-only requests, zero-budget
denial/approval behavior, deterministic ranking, and prompt-free errors. Those
behaviors are protected by `tests/unit/policies/test_router.py`.

Gate 3 adds pre-ranking eligibility:

- explicit `test` or `live` execution profile;
- exact `ModelRequest.capability_profile` matching;
- fake isolation through test-only composition descriptors, never provider
  self-authorization;
- injected qualification/profile snapshots from `ProviderEligibilityPolicy`;
- exact task class and canonical output-schema ID from a deployment-owned
  `ProviderRegistrationDescriptor`;
- measured, non-expired qualification status;
- actual structured-output behavior, not the current default capability claim;
- provider projection followed by canonical Pydantic revalidation; and
- canonical route-policy/qualification identity in the attempt record.

The existing no-retry/no-fallback contract remains. The design's proposed
repair/hosted escalation loop is outside the 48-hour proof because it conflicts
with existing tests for the flow, Tutor generation, source verifier, Ollama,
and OpenAI adapter.

Qwen is optional for Gate 3 only. If Ollama is unavailable, the smoke is
`deferred`; static live-route isolation remains required. If it runs, the report
records actual installed tag, digest, quantization, runtime, and smoke status.

## Persistence delta

The repository has several sound SQLite repository patterns but not a single
coordinated database:

- source inventory, extraction, and promotion each use an unnamespaced
  `schema_migrations(version)` table;
- initializing those three implementations against one file can cause the
  first version-1 row to suppress another repository's schema;
- catalog and content use separate connection/schema patterns;
- promotion tests intentionally compose separate databases;
- catalog/content do not currently establish cross-store foreign keys; and
- WAL is not a repository-wide invariant.

For the 48-hour proof:

- keep existing source, extraction, promotion, catalog, and content stores in
  explicitly separate temporary files;
- add one bounded `SQLiteStudySessionRepository` for CCNA/generic study events;
- never claim one-file/WAL compliance; and
- include store identities and canonical logical-record hashes in the gate
  report, never hashes of raw SQLite database or WAL bytes.

For one-week hardening, Alan must choose namespaced migrations or a coordinated
database migrator before composing these repositories into one SQLite file.

`src/personal_lms/mastery.py::SQLiteMasteryStore` uses `INSERT OR REPLACE` and
cannot serve as the append-only evidence trail. It remains a legacy Build Week
demo dependency. The new study-session repository reuses implementation
patterns, not overwrite semantics.

## CLI compatibility

The repository has one console entry point:

```text
personal-lms = personal_lms.cli:main
```

`src/personal_lms/cli.py` currently supports no arguments, `--version`, `ask`,
and `build-week-demo`. It can safely register a nested `ccna-lab` parser whose
handlers live in `labs/ccna_mastery/cli.py`. No new console script or
standalone application is needed.

Compatibility checks must retain:

- `tests/unit/test_cli.py`;
- `tests/unit/test_cli_ask.py`;
- current composition behavior;
- `python -m personal_lms`; and
- optional-import isolation for CrewAI, Ollama, and hosted providers.

## Orchestration and A2A boundary

There is a real wording conflict:

- `AGENTS.md` and ADR-0001 require CrewAI Flows for deterministic outer
  orchestration;
- the CCNA design says a custom typed workflow is enough and adding CrewAI
  would duplicate control; and
- current code already separates a framework-neutral `PersonalAssistantFlow`
  from a thin optional CrewAI adapter.

Reconciliation:

1. Implement Objective Pack, study-session, policy, event, mastery, and
   practical logic as plain injected Python services.
2. Expose one bounded use-case port through feature wiring supplied with shared
   dependencies by root composition. Preserve `PersonalAssistantFlow.run()`;
   later registration may add a handler but may not change its behavior.
3. Resolve whether a thin CrewAI outer adapter is required before Gate 2 begins.
4. Run an A2A contract-mapping spike only after the local gates pass and a
   second deployed consumer exists. The adapter calls the use-case port, not
   repositories, events, or internal handlers, and enforces the external
   trust/delivery/transmission controls stated above.
5. Neither adapter may own grading, state, policy, routing approval, replay, or
   local commit authority.

Alan must confirm whether this compatibility pattern satisfies ADR-0001 or
whether a thin CrewAI wrapper is required. The answer is a prerequisite to
Gate 2, not a reason to put CrewAI into Gate 1.

## Decisions requiring Alan

| ID | Decision | Recommended default | Effect if unresolved |
|---|---|---|---|
| AD-01 | May Gate 1 add one declared local searchable-PDF parser dependency? | Approve one narrow, pinned parser; parse PNG structure/regions locally and require human visual approval. | Gate clock cannot start because actual-byte extraction is absent. |
| AD-02 | How is official blueprint scope represented in the synthetic gate fixture? | Freeze authoritative objective/version metadata plus the synthetic wrong-version source; use real authorized blueprint bytes in one-week hardening. | Version exclusion expectation remains ambiguous. |
| AD-03 | Exact golden-acceptance command and reviewer identity | Approve a reviewer-only `ccna-lab gate accept-goldens` contract; normal runs refuse writes. | Goldens cannot be frozen honestly. |
| AD-04 | Does the framework-neutral use-case port plus a thin outer CrewAI adapter satisfy ADR-0001? | Resolve before Gate 2; keep deterministic authority in injected services. | Gate 2 may not begin. |
| AD-05 | Qwen identity for later live-local qualification | Resolve the actually installed model at runtime; never hard-code `qwen3.5:9b` or current test examples. | Qwen smoke may defer; core static gate still runs. |
| AD-06 | Hosted-model budget scope | Keep the 48-hour proof at zero hosted spend; design a spend ledger before any hosted live route. | Hosted route remains ineligible, which is safe. |
| AD-07 | Gate 3 report when route passes and factory fails | Preserve subgate statuses; one-objective trial may pass while scaling fails. | A single “overall” status would misstate the decision. |
| AD-08 | Definition and recording of “focused hours” | Human start/stop ledger tied to fixture-ready and gate-start hashes. | Manual-cleanup and factory-time criteria cannot be audited. |
| AD-09 | Shared SQLite direction after proof | Namespaced migrations or one coordinated migrator; do not co-locate current stores unchanged. | One-week persistence hardening is blocked, not Gate 1. |
| AD-10 | A2A/CML portability checkpoint | After local gates and a second deployed consumer, run a contract-mapping spike with no core-domain changes and full external-boundary controls. | Portability remains untested, but the August 1 proof is unaffected. |
| AD-11 | May implementation agents create local feature-branch commits, or must handoff use only a canonical diff hash? | Alan chooses one integration authority before coding; push and PR remain prohibited. | Handoff/integration provenance is ambiguous. |

## Known design conflicts resolved by this plan

1. Gate 1 uses 10 supported plus 2 unsupported retrieval cases. The 20-case
   requirement is a one-week expansion.
2. Qwen is optional/deferred in the 48-hour route gate and required for later
   live-local hardening.
3. Gate 1 uses one bounded synthetic-source adapter. Real private
   Docling/figure extraction is one-week work.
4. Objective-specific required modalities are pack policy data. The shadow
   objective can complete a diagnostic while refusing operational mastery when
   required practical evidence is absent.
5. Route isolation and factory scalability remain independent Gate 3 subgates
   with separate decisions.
6. Canonical IDs and hashes are computed from explicitly versioned,
   canonicalized records; normal gate reports cannot rewrite expected values.
7. Fake and Qwen outputs project into one named canonical Pydantic result type;
   providers do not define domain schemas.
8. A2A is a future transport adapter to the proven control plane, not a reason
   to add a second framework during the linchpin proof.

## Trial blockers versus scaling blockers

Blocks the August 1 one-objective trial:

- any Gate 1 evidence/source/version/abstention/injection failure;
- any Gate 2 grading/state/replay/bounds/acquisition-retention failure; or
- any required Gate 3 route-isolation failure.

Does not by itself block that narrow trial:

- Qwen smoke deferred because Ollama is unavailable;
- factory scalability failure;
- absence of real private Docling ingestion;
- fewer than 20 week-scale retrieval cases;
- no hosted route;
- no unified SQLite/WAL implementation; or
- no A2A/CML adapter yet.

Those items block broader production or several-objectives-per-day claims and
must remain visible, not be silently called complete.
