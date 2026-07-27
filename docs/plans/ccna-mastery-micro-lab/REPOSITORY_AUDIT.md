# PersonalLMS Repository Audit for the CCNA Mastery Micro-Lab

Audit date: 2026-07-27
Mode: read-first, local-only, planning-only
Baseline commit: `565cf42743b31aae96f67e22d1891569003f0009`
Baseline branch: `codex/build-week-grounded-tutor`
Judgment: **CONDITIONAL GO for Gate 1 implementation/preparation; NO-GO for
starting the 48-hour clock**

## Executive findings

The proposed CCNA micro-lab fits PersonalLMS as a bounded feature slice. It
should not be a separate application. The repository already provides the
router, provider protocol/registry/fakes/Ollama adapter, privacy classifications,
budget policy, source inventory/promotion/catalog/content stages, FTS5,
grounded Tutor, source verification, SQLite repository patterns, CLI entry
point, and optional thin CrewAI boundary.

Three groups of work are genuinely absent:

1. executable reviewed Objective Pack/source/query/learner fixtures and a local
   real-byte PDF/PNG extraction adapter;
2. generic deterministic assessment, practical-adapter, append-only event,
   replay, mastery, and scheduling services; and
3. live/test execution profiles, fake-live exclusion, expiring provider
   qualification, and canonical structured-result projection.

The first group prevents the clock from starting. The second and third groups
are the intended Gate 2 and Gate 3 implementation, not reasons to build
duplicate CCNA infrastructure.

## Read-first audit scope

The audit followed the repository and task reading order:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `pyproject.toml`
- `docs/exec-plans/active/2026-07-16_PERSONAL_LMS_MULTI_AGENT_MASTER_PLAN.md`
- `docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md`
- relevant closeout, product specifications, and ADRs 0001–0004
- every file in
  `docs/design/ccna-mastery-micro-lab-design/`, including all micro-designs and
  YAML examples
- current source packages and relevant unit/integration tests

No product code, dependency, source content, provider call, Git commit, push,
PR, or GitHub state was changed.

## Git and worktree baseline

At the audit baseline:

```text
HEAD:       565cf42743b31aae96f67e22d1891569003f0009
branch:     codex/build-week-grounded-tutor
upstream:   origin/codex/build-week-grounded-tutor
upstream:   ahead 0, behind 0
local `main...HEAD`: HEAD 36 ahead, 0 behind
origin `origin/main...HEAD`: HEAD 0 ahead, 1 behind
```

Existing worktrees:

```text
/home/ajsch/projects/personal-lms
  codex/build-week-grounded-tutor

/mnt/c/Users/admin/.codex/visualizations/2026/07/25/.../personal-lms-learning-artifact-plan
  codex/learning-artifact-factory-plan
```

Git baseline reconciliation: local `main` is stale, while `origin/main` is the
release merge `5d43efc`; its tree is identical to audited HEAD `565cf42`.
Alan reports that `demo`, `main`, and tag
`build-week-grounded-tutor-v0.1.0` all reference `5d43efc`, `demo` is protected
from deletion/force-push including admins, `main` is the default PR base, and
no GitHub Pages/deployment is configured. This supplied GitHub state was not
independently changed during the audit. Implementation branches must start from
the latest approved `main` after the required design/planning inputs are
available; every PR must visibly target `main`, never `demo`.

The baseline working tree was not technically clean:

```text
?? docs/design/
?? docs/deasign/
```

There were no tracked modifications before this planning task. The two
untracked directories contained substantively duplicate CCNA design packs; the
misspelled copy also contained Windows `Zone.Identifier` files. During the
authorized cleanup, every substantive file was verified byte-identical to its
`docs/design/` counterpart, then `docs/deasign/` was moved to the recoverable
backup `/tmp/personal-lms-deasign-backup-20260727`. The correct design tree
remains intact and untracked.

The only authorized additions from this turn are the five files under:

```text
docs/plans/ccna-mastery-micro-lab/
```

The repository normally stores execution plans in
`docs/exec-plans/active/`. The design-owner prompt explicitly requires these
five artifacts under `docs/plans/ccna-mastery-micro-lab/`, so the exact
requested path was retained. The implementation plan explains status and does
not attempt to replace the active master plan.

## Baseline validation

Commands were run from the canonical worktree before creating the planning
documents. They used existing locked dependencies, disabled synchronization and
caches where supported, and made no provider calls.

| Command | Outcome |
|---|---|
| `uv run --no-sync ruff check --no-cache .` | Passed: all checks passed. |
| `uv run --no-sync ruff format --check --no-cache .` | Passed: 179 files already formatted. |
| `uv run --no-sync mypy --no-incremental src` | Passed: 91 source files checked with no issues. |
| `uv run --no-sync pytest -p no:cacheprovider` | Passed: 1,177 passed, 3 skipped, 1,180 collected in 13.16 seconds. |
| `git diff --check` | Passed with no output. |

The three skips are intentional optional-dependency negative-path tests. No
live Ollama/model call, hosted API call, CrewAI LLM orchestration, source
ingestion, or network smoke was run; installed adapter test suites did execute.

These results establish the baseline only. Implementation completion must rerun
the required checks in its isolated worktree.

## Relevant current architecture

### Deterministic routing and model boundary

Definitions:

- `src/personal_lms/domain/models.py::ModelCapabilityProfile`
- `src/personal_lms/domain/models.py::ModelRequest`
- `src/personal_lms/domain/models.py::ModelResult`
- `src/personal_lms/providers/protocol.py::ModelProvider`
- `src/personal_lms/providers/registry.py::ProviderRegistry`
- `src/personal_lms/policies/router.py::DeterministicRouter`

Current callers:

- `src/personal_lms/flows/personal_assistant.py::PersonalAssistantFlow.run`
- `src/personal_lms/tutor/_generation.py::route_and_generate`
- `src/personal_lms/source_verification/model_backed.py::ModelBackedSourceVerifier.verify`
- `src/personal_lms/composition.py::compose`

Existing behavior worth preserving:

- Tier 0 deterministic short circuit;
- local preference over hosted providers;
- privacy-forced local behavior;
- local-only enforcement;
- deterministic cost/latency/provider ranking;
- prompt-free route errors;
- zero-budget denial or approval-required results; and
- one route, one provider call, no automatic retry/fallback in current callers.

Current gaps:

- request `capability_profile` is not an exact candidate filter;
- `supports_structured_output` is declared but not checked by the router;
- no `test` versus `live` execution profile;
- no fake-provider live prohibition;
- no task-class qualification, expiry, cooldown, or durable qualification;
- no current-spend ledger or request-cost comparison;
- no provider-specific schema projection followed by canonical revalidation;
- no route-policy hash; and
- no provider attempt record with actual model digest/runtime identity.

### Providers

Reusable:

- `src/personal_lms/providers/fake.py::FakeLocalProvider`
- `src/personal_lms/providers/fake.py::FakeHostedProvider`
- `src/personal_lms/providers/ollama/config.py::OllamaProviderConfig`
- `src/personal_lms/providers/ollama/provider.py::OllamaProvider`
- `src/personal_lms/providers/ollama/smoke_test.py::run_smoke_test`

The fakes are deterministic and injectable with no filesystem/network/
environment access, but they are ordinary registrable providers today. They
are not impossible to select in a live profile because no live profile exists.

The Ollama adapter already has loopback-default controls, URL validation,
one-request/no-retry behavior, injected transport, health, model discovery,
digest, and quantization metadata. Its current smoke is an echo-like request
and does not persist a task-class qualification, expiry, resolved runtime, or
canonical structured-result proof. The design's Qwen model names are
provisional; the implementation must resolve the actually installed tag.

`pyproject.toml` declares only Pydantic as a core dependency. There is no
declared PDF/image extraction library.

### Privacy and budget

Reusable:

- `src/personal_lms/domain/privacy.py::PrivacyClassification`
- `src/personal_lms/domain/budgets.py::BudgetPolicy`
- `src/personal_lms/content/protocol.py::ChunkSearchFilters`
- `src/personal_lms/librarian/content_grounding.py`

Privacy filtering is applied in SQL before `LIMIT`. Tutor prompt construction
admits only trusted, non-empty evidence. These controls must remain on the
graded-feedback path.

`BudgetPolicy` currently validates configured thresholds. Router enforcement is
limited mainly to zero daily/automatic limits and does not track consumed
daily/monthly spend or estimate a particular call. The safe 48-hour
composition is local-only with zero hosted spend. Hosted routing requires a
later ledger/attempt extension.

### Source inventory, extraction, promotion, and content

Reusable staged services:

1. `src/personal_lms/source_inventory/`
2. `src/personal_lms/extraction/`
3. `src/personal_lms/promotion/`
4. `src/personal_lms/catalog/`
5. `src/personal_lms/content/`

`SourcePromotionService` already coordinates explicit human approval, rights
clearance, privacy non-downgrade, extraction success, idempotent promotion, and
recovery. `SQLiteContentRepository` provides deterministic FTS5 and exact
technical-token behavior. These are the correct seams; a `CCNASourceRepository`
or CCNA RAG store would be duplication.

What is absent:

- a production extractor protocol/adapter;
- safe actual-byte hashing, MIME, size, and configured-root checks;
- searchable-PDF and image extraction;
- image region/visual review records;
- fine-grained allowed-use records;
- approved artifact-to-document/chunk assembly;
- objective/version/rights/quarantine pre-limit eligibility;
- claim-specific support factors and recomputed grounding scores;
- retrieval traces and deterministic index-content hashes; and
- a verified link from content parent back to an approved promoted source.

`src/personal_lms/source_readiness.py::SourceReadinessImporter` is not a
production ingestion shortcut. It is a redacted Build Week metadata importer:
it does not read source bytes, uses a placeholder hash/locator kind, and rejects
normal private `/home/...` or `/Users/...` locators. It should remain unchanged.

### Grounding and Tutor

Reusable grounding:

- `src/personal_lms/domain/librarian.py::LibrarianRetrievalRequest`
- `src/personal_lms/domain/librarian.py::RetrievedEvidence`
- `src/personal_lms/domain/librarian.py::GroundingBundle`
- `src/personal_lms/librarian/content_grounding.py::LibrarianContentGroundingService`

Current content grounding preserves source/document/chunk/page/section
provenance, privacy-before-limit behavior, trusted-parent checks, and
deterministic FTS5 ranking. A non-empty `knowledge_packs` request currently
returns an insufficient bundle without searching, so the CCNA design cannot
pretend current knowledge-pack filtering exists. For the 48-hour proof, exact
objective/version scoping can compose
`KnowledgeScope.objective_framework`; generic pack eligibility still needs a
narrow extension.

`GroundingBundle.is_sufficient` means at least one trusted match, not
claim-specific grounding at score 85. Replacing it wholesale would break
compatibility tests. The correct delta is a versioned Objective Pack evidence
envelope plus the existing supplied bundle.

Reusable Tutor:

- `src/personal_lms/tutor/evidence_checked.py::EvidenceCheckedTutorService`
- `src/personal_lms/tutor/coordinator.py::TutorTeachingCoordinator`
- `src/personal_lms/source_verification/protocol.py::SourceVerifier`
- `src/personal_lms/source_verification/model_backed.py::ModelBackedSourceVerifier`

The coordinator already enforces exactly one evidence mode, zero/one retrieval,
zero/one model call, privacy propagation, trusted-evidence prompting, structural
citation checks, optional fail-closed semantic verification, and safe provider
failure. The CCNA feature should use supplied-bundle mode so it cannot broaden
a reviewed pack with fresh retrieval.

A new `GroundedFeedback` adapter may let the provider word a correction.
Deterministic code must supply target, observed condition, and next action.
Source verification may check the wording; it may not become the Evaluator,
grader, evidence scorer, or mastery authority.

### Flow and orchestration

`src/personal_lms/flows/personal_assistant.py::PersonalAssistantFlow` handles
one model request. Its `RunState` is mutable/in-memory and it is not a
specialist dispatcher, study-session runner, or durable event store. Preserve
its public `run()` semantics.

`src/personal_lms/adapters/crewai/personal_assistant.py` is a thin optional
adapter over the framework-neutral flow. This supplies the reconciliation
pattern for the apparent conflict between:

- `AGENTS.md`/ADR-0001 requiring CrewAI Flows at the outer orchestration
  boundary; and
- the CCNA design rejecting a second control framework in the 48-hour scope.

The mastery use case should be a bounded, injected, framework-neutral PersonalLMS
application handler. It can receive an optional thin CrewAI adapter later if
Alan requires one. Deterministic domain logic does not belong in CrewAI.

The same application seam is the future portability point for the CML MCP
redesign and an A2A-style transport. This audit does not authorize or recommend
adding A2A/MCP to the linchpin scope.

### Mastery and persistence

The repository has no generic attempt/grade/follow-up/session/replay/retention/
schedule service.

`src/personal_lms/mastery.py::SQLiteMasteryStore` is a small Build Week store
using `INSERT OR REPLACE`. `src/personal_lms/tutor/build_week.py` can mark a
single correct response as `MASTERED` and generates three runtime questions.
Those semantics conflict with the CCNA acquisition/retention and append-only
gates. Keep the demo intact and reuse only Pydantic/SQLite coding patterns.

Existing SQLite services are not safely one-database composable:

- source inventory, extraction, and promotion each use an unnamespaced
  `schema_migrations(version PRIMARY KEY)` table;
- if they share one database, the first version-1 row can cause the next
  repository to skip its schema;
- catalog/content use separate connection/schema patterns;
- cross-store foreign keys and repository-wide WAL are absent; and
- current promotion integration tests use separate stores.

The 48-hour proof should use explicit separate temporary store files plus one
new bounded append-only study-session repository. One-week hardening can
introduce namespaced migrations or a coordinated migrator.

### CLI

Current public interface:

- `personal-lms`
- `personal-lms --version`
- `personal-lms ask`
- `personal-lms build-week-demo`
- `python -m personal_lms`
- separate existing `personal-lms-ollama-smoke-test`

`src/personal_lms/cli.py::build_parser` uses `argparse`. A nested `ccna-lab`
subparser registered by a handler module is a safe additive change if current
CLI tests remain green. A standalone CCNA console script is unnecessary.

## Audit questions

### 1. Which proposed components can directly reuse existing code?

Direct reuse:

- `StrictModel` for all structured boundaries;
- `KnowledgeScope` for objective/version scope;
- source inventory, promotion eligibility/service, catalog, and content
  repository protocols;
- SQLite FTS5 content retrieval and provenance;
- `GroundingBundle` and supplied evidence;
- `TutorTeachingCoordinator` and evidence/citation verification;
- `SourceVerifier` as an optional correction check;
- `DeterministicRouter`, `ProviderRegistry`, `ModelProvider`, fake providers,
  and Ollama adapter;
- `PrivacyClassification` and `BudgetPolicy`;
- current composition/dependency-injection patterns;
- root `personal-lms` CLI; and
- SQLite repository testing patterns.

### 2. Which need an adapter, extension, or new bounded module?

Adapters:

- one local actual-byte searchable-PDF/PNG fixture extractor;
- one `GroundedFeedback` adapter around the existing Tutor;
- one CCNA trunk practical adapter behind a generic protocol; and
- one nested CLI/composition/gate adapter.

Narrow generic extensions:

- source use/rights and content eligibility;
- FTS5 objective/version/quarantine filtering and trace/hash output;
- router candidate eligibility for execution profile and qualification;
- fake-provider metadata;
- Ollama qualification reporting; and
- budget attempt/ledger behavior before any future hosted route.

New bounded generic modules:

- Objective Pack contracts/loader/validator;
- deterministic selection and grading;
- study-session state machine;
- append-only learning events and replay projector;
- acquisition/retention mastery and scheduling;
- practical-adapter protocol;
- provider qualification/projection; and
- machine-readable gate reporting.

### 3. Which design assumptions conflict with present architecture?

- “Existing qualification controls” do not exist.
- `PersonalAssistantFlow` does not orchestrate a mastery session.
- current `GroundingBundle` cannot be replaced with the proposed rich shape
  without breaking serialized compatibility.
- current knowledge-pack filtering is deliberately unimplemented.
- no real PDF/image/Docling extraction adapter or dependency exists.
- current BudgetPolicy is not a spend ledger.
- fake providers are not live-isolated.
- Ollama declares structured-output capability by default but does not send a
  canonical schema projection.
- current mastery overwrites records and permits one-answer mastery.
- the design's one-database/WAL assumption conflicts with current unnamespaced
  migrations and independent stores.
- the proposed retry/repair/hosted fallback conflicts with tested one-call,
  no-retry, no-fallback behavior and is outside the two-day cut.
- “no CrewAI” wording must be reconciled with ADR-0001 through a
  framework-neutral handler and optional thin outer adapter.
- A2A is a strategic transport candidate, not existing PersonalLMS
  infrastructure or a Gate 1 dependency.

### 4. What is the smallest honest Gate 1 implementation?

The smallest cohesive Gate 1:

1. strict generic Objective Pack/evidence/item contracts;
2. deterministic YAML/JSON loader, canonicalizer, and validator;
3. one declared local actual-byte searchable-PDF/PNG fixture adapter;
4. rights/use/privacy/trust/quarantine/objective-version eligibility composed
   with existing source/content services;
5. claim-specific evidence scoring and reference/exposure recomputation;
6. existing SQLite FTS5 for the 10+2 cases;
7. an evidence gate runner and machine report;
8. nested `ccna-lab gate evidence/report` commands; and
9. golden-write protection.

It contains no study UI, model call, vector database, full source platform,
production Docling pipeline, or CCNA-specific RAG. The generic loader must also
load the shadow objective schema even though Gate 3 content is assembled later.

### 5. What fixtures require technical review before the clock starts?

The exact absent tree is specified in
`IMPLEMENTATION_PLAN.md` and the design's gate document. Review must freeze:

- all source bytes, paths, SHA-256, MIME, size, rights/use, privacy, version,
  pages, image regions, and accessible descriptions;
- malicious source paragraph, expected inert behavior, low-confidence label,
  and wrong-version distractor;
- one visually approved infographic claim;
- the six required claims and evidence factors;
- 12 complete baseline items;
- six complete mapped follow-ups;
- one complete non-overlapping exit probe;
- scenario invariants, grammar, two faults, rubric, equivalence cases,
  variations, reason IDs, and starting hash;
- 10 supported and 2 unsupported query expectations;
- four learner response vectors and exact outcomes;
- canonical state/event/index hashes;
- all policy/schema/content versions;
- allowed provider/profile combinations; and
- golden reviewer command and focused-time method.

Current example files are explicitly non-executable: the objective says
`draft_missing_question_bank_records`, referenced item files are absent, the
scenario says `draft_until_human_review`, its starting hash is null, reviewer
fields are placeholders, and D419 mapping remains provisional/private.

### 6. Can the CLI live under the existing CLI without breaking interfaces?

Yes. Register `ccna-lab` as a nested `argparse` subparser and keep its handler
in `src/personal_lms/labs/ccna_mastery/cli.py`. Do not add a console script.
Run existing CLI tests plus new nested-command tests to protect no-argument,
version, `ask`, demo, and `python -m` behavior.

### 7. Which current tests protect reused behavior?

Routing:

- `tests/unit/policies/test_router.py::test_deterministic_capable_short_circuits_to_tier_0`
- `::test_local_preferred_over_hosted_even_when_hosted_is_cheaper`
- `::test_local_only_requests_never_select_hosted_providers`
- `::test_restricted_local_only_privacy_forces_local_routing_regardless_of_flag`
- `::test_privacy_policy_denied_when_only_a_hosted_candidate_would_otherwise_qualify`
- `::test_zero_daily_limit_blocks_hosted_routing`
- `::test_zero_automatic_limit_requires_approval_instead_of_denial`
- `::test_selection_is_independent_of_registration_order`
- `::test_prompt_text_is_absent_from_routing_errors`

Flow and provider isolation:

- `tests/unit/flows/test_personal_assistant.py::test_tier_0_does_not_access_registry_or_provider`
- `::test_provider_failure_is_not_retried`
- `::test_provider_failure_does_not_fall_back_to_another_provider`
- `::test_flow_has_no_filesystem_effect`
- `::test_flow_makes_no_network_calls`
- `tests/unit/adapters/crewai/test_personal_assistant.py::test_flow_delegates_to_the_framework_neutral_personal_assistant_flow`
- `::test_no_agent_crew_task_or_llm_is_instantiated`
- `tests/unit/providers/test_fake.py`
- `tests/unit/providers/test_registry.py`
- `tests/unit/providers/ollama/test_config.py`
- `tests/unit/providers/ollama/test_generate.py`
- `tests/unit/providers/ollama/test_discovery.py`
- `tests/unit/providers/test_openai_responses.py`

Grounding, privacy, and provenance:

- `tests/unit/librarian/test_content_grounding.py::test_evidence_preserves_source_document_and_chunk_provenance`
- `::test_sufficient_when_at_least_one_trusted_chunk_matches`
- `::test_insufficient_when_hits_exist_but_none_are_trusted`
- `::test_privacy_filtering_excludes_only_the_more_restrictive_chunk`
- `::test_non_empty_knowledge_packs_returns_insufficient_with_a_clear_gap`
- `::test_non_empty_knowledge_packs_performs_zero_repository_searches`
- `tests/unit/content/test_sqlite.py::test_trusted_chunk_rejected_when_parent_not_reviewed`
- `::test_privacy_filtering_is_applied_before_limit_not_after`
- `::test_sql_injection_shaped_search_query_is_treated_as_literal_text`
- `::test_search_results_are_deterministically_ordered`

Tutor and source verification:

- `tests/unit/tutor/test_evidence_checked.py::test_insufficient_grounding_causes_zero_model_calls`
- `::test_only_trusted_evidence_enters_the_prompt`
- `::test_missing_citations_fail_integrity_checking`
- `::test_unknown_citation_label_fails_integrity_checking`
- `::test_restricted_local_only_requests_cannot_use_hosted_providers`
- `tests/unit/tutor/test_coordinator.py::test_supplied_bundle_performs_zero_retrieval_calls`
- `::test_supplied_bundle_does_not_get_supplemented_with_fresh_retrieval`
- `::test_privacy_classification_propagates_identically_in_all_three_modes`
- `tests/unit/tutor/test_source_verification_gate.py::test_verified_result_returns_the_generated_answer_and_used_citations`
- `::test_partially_verified_result_fails_closed`
- `::test_unsupported_result_fails_closed`
- `::test_conflicting_result_fails_closed`
- `tests/unit/source_verification/test_model_backed.py::test_no_fallback_to_a_second_qualifying_provider`
- `::test_no_automatic_repair_occurs`
- `::test_unknown_verified_labels_fail_cross_validation`

Compatibility:

- `tests/unit/domain/test_tutor.py::test_teaching_request_old_shaped_payload_without_new_fields_still_validates`
- `::test_teaching_response_old_shaped_payload_without_new_fields_still_validates`
- `tests/unit/domain/test_librarian.py::test_retrieved_evidence_old_shaped_payload_without_new_fields_still_validates`
- `::test_grounding_bundle_json_round_trip`
- `tests/unit/test_cli.py`
- `tests/unit/test_cli_ask.py`
- `tests/unit/test_composition.py`

Source lifecycle:

- `tests/unit/source_inventory/test_sqlite.py`
- `tests/unit/extraction/test_sqlite.py`
- `tests/unit/promotion/test_eligibility.py`
- `tests/unit/promotion/test_service.py`
- `tests/unit/promotion/test_sqlite.py`
- `tests/unit/catalog/test_sqlite.py`

### 8. What new tests are required?

Every GO/NO-GO row has a planned symbol, exact planned test, and expected
artifact in `LINCHPIN_TRACEABILITY.md`. Major groups are:

- source-byte/MIME/hash/root/rights/version/region resolution;
- pack cardinality/reference/exposure and recomputed coverage/grounding;
- supported top-five and unsupported abstention retrieval;
- malicious, low-confidence, quarantined, unauthorized, and wrong-version
  exclusion before question/provider context;
- reproducible IDs and index hashes;
- deterministic bounded selection/grading/follow-up/practical behavior;
- ambiguity, invalid-command, injection, and fake-provider authority tests;
- append-only event integrity and interrupted replay;
- acquisition versus retention and frozen-clock tests;
- live/test fake isolation and current qualification;
- SDK/domain separation and canonical result revalidation;
- optional Qwen metadata/deferral behavior;
- no-code shadow pack and missing-practical-evidence behavior; and
- decision-matrix/golden-write enforcement.

### 9. Is the worktree clean, and what strategy avoids unrelated work?

No: both design directories were already untracked. There were no tracked
changes at baseline. This turn adds only the required five planning files.

For implementation, first make the approved design/planning artifacts available
from the selected base without disturbing the canonical worktree. Then create:

```text
branch:   codex/ccna-gate-1-objective-pack
worktree: /home/ajsch/projects/personal-lms-codex-ccna-gate1
```

Use a separate `codex/ccna-gate-1-local-extraction` worktree only for an
explicitly disjoint slice. One writer owns each path. Do not implement in the
canonical worktree. The typo duplicate has already been removed from the
repository path after byte-for-byte verification.

### 10. What blocks the August 1 trial versus scaling?

Blocks the one-objective August 1 trial:

- any Gate 1 failure;
- any Gate 2 failure; or
- any required route-isolation failure.

Does not alone block the narrow trial:

- unavailable Ollama/Qwen smoke marked deferred;
- factory-scalability failure;
- real private Docling ingestion not yet complete;
- fewer than 20 week-scale retrieval cases;
- no hosted provider route;
- no unified SQLite/WAL hardening; or
- no A2A/CML adapter.

Those latter gaps block broader production/scaling claims. A factory failure
allows objective 2.2 only when Gates 1, 2, and route isolation pass.

### 11. How can this be one cohesive PersonalLMS feature slice?

Use this ownership chain:

```text
generic Objective Pack data
  -> existing source/promotion/content/FTS5 eligibility
  -> generic deterministic session runner
     -> generic deterministic grader/follow-up/mastery/scheduler
     -> CCNA-only practical adapter
     -> existing Tutor supplied-bundle feedback
        -> existing privacy/budget/router/registry/provider
     -> generic append-only events and replay
  -> nested PersonalLMS CLI and gate reports
```

Only Objective Pack/session/event/mastery/schedule/practical contracts are new
generic control-plane capabilities. CCNA owns its scenario and CLI/gate
composition. Existing infrastructure stays in its existing packages.

This same task/result/policy/event boundary can later be adapted to the CML MCP
control plane or an A2A transport. The future adapter should prove that no core
domain change is needed; adding it now would obscure the linchpin and violate
the two-day boundary.

## Largest blockers

1. **Pre-clock content and fixture readiness.** The exact `tests/linchpin/`
   tree, complete reviewed item records, technical approvals, expected outputs,
   and hashes do not exist.
2. **Gate 1 actual-byte ingestion/evidence delta.** No declared production
   searchable-PDF/image extractor or byte/MIME/root/region/use/claim validator
   exists.
3. **Authoritative control-plane gaps after Gate 1.** There is no append-only
   deterministic mastery/replay/schedule loop and no live/test provider
   qualification/isolation. These are bounded Gate 2/3 work, not reusable
   capabilities already present.

## Risks, unknowns, and assumptions

| Type | Item | Planning treatment |
|---|---|---|
| Resolved local hygiene | The misspelled `docs/deasign/` duplicated the correct design tree. | Verified substantive files byte-identical and moved the typo tree to recoverable `/tmp`; only `docs/design/` remains. |
| Unknown | Approved local PDF parser/dependency. | Alan decision before clock; no sidecar substitution. |
| Unknown | Whether official blueprint bytes are required in the synthetic fixture. | Recommend frozen objective/version metadata for the proof and real authorized blueprint in one-week hardening. |
| Unknown | Exact golden-acceptance command/reviewer. | Must be defined and tested before clock. |
| Unknown | Focused-time measurement. | Use signed start/stop entries tied to evidence-ready and manifest hashes. |
| Risk | Objective Pack could become a second RAG bundle. | Keep it assessment/config data and resolve evidence through existing content/grounding seams. |
| Risk | Rich grounding fields could break old clients. | Use an evidence envelope/backward-compatible additions; retain compatibility tests. |
| Risk | One SQLite file silently skips schemas. | Separate store files in 48 hours; coordinated migrations later. |
| Risk | Legacy Build Week mastery semantics are reused accidentally. | Leave legacy code intact and name new generic study-session authority explicitly. |
| Risk | Router extension breaks no-retry/privacy behavior. | Add pre-ranking eligibility only and rerun all current router/provider tests. |
| Risk | “Structured output” remains a declaration rather than behavior. | Require canonical schema ID, provider projection, and Pydantic revalidation. |
| Assumption | 10+2 retrieval cases are the 48-hour gate set. | Expand to 20 in one-week hardening. |
| Assumption | Qwen may defer only when Ollama is unavailable. | Never defer static/live route isolation. |
| Assumption | Local-only, zero-hosted-spend is sufficient for the proof. | A hosted route needs budget ledger/attempt persistence later. |
| Strategic risk | CCNA details leak into the novel multi-agent control plane. | Keep generic contracts transport/domain neutral; future CML MCP/A2A spike must require no core changes. |

## Final audit conclusion

The architecture is suitable for a focused Gate 1 implementation because the
repository has real reusable safety infrastructure and the missing work can be
bounded cleanly. It is not build-ready for the timed proof because the reviewed
fixture contract and local extraction decision are absent.

Accordingly:

- **CONDITIONAL GO** to prepare and implement Gate 1 in an isolated worktree;
- **NO-GO** to start the 48-hour clock until every pre-clock condition passes;
- no authorization to begin Gate 2, Gate 3, A2A/MCP work, or
  production/private ingestion. Publication of these five planning artifacts
  was separately authorized; it does not authorize implementation publication.
