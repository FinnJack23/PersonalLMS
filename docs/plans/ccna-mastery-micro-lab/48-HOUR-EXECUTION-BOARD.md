# CCNA Mastery Micro-Lab 48-Hour Execution Board

Status: **clock not started**
Gate 1 implementation status: not started
Board rule: every task is 30–120 focused minutes; overrun triggers a stop and
report, not silent continuation

## Operating model

The work uses Alan's normal review loop:

1. ChatGPT 5.6 SOL Ultra owns the initial deep planning, architecture
   adjudication, acceptance criteria, and Gate 1 start prompt.
2. Claude Opus owns hard implementation, review gates, and smoke-test judgment.
3. Claude Sonnet owns routine, tightly bounded coding and later GitHub work.
4. Codex SOL High owns separate implementation slices in a separate worktree
   with disjoint file ownership.
5. GPT-5.6 Luna owns GitHub operations only after Alan explicitly authorizes
   publication.
6. Each implementation agent ends with a detailed structured report.
7. Alan pastes that report into ChatGPT SOL High for evidence review.
8. ChatGPT SOL High owns recurring in-clock checkpoints and produces each next
   bounded prompt only after the prior checkpoint is accepted.

No role assignment authorizes a commit, push, PR, external transmission, golden
rewrite, or merge. Those remain explicit Alan decisions.

## Workspace and file-ownership rules

Recommended Gate 1 lead branch/worktree:

```text
codex/ccna-gate-1-objective-pack
/home/ajsch/projects/personal-lms-codex-ccna-gate1
```

Claude Opus may remain the coding lead in that convention-named worktree. If a
second Codex process receives the independent extraction slice, use:

```text
codex/ccna-gate-1-local-extraction
/home/ajsch/projects/personal-lms-codex-ccna-gate1-extraction
```

Do not create either worktree until the approved design/planning artifacts are
available from the chosen base. Never write implementation in the canonical
shared worktree `/home/ajsch/projects/personal-lms`.

One agent owns one path at a time. A proposed split is:

| Lane | Exclusive paths while active |
|---|---|
| Claude Opus lead | `domain/objective_packs.py`, `objective_packs/`, `labs/ccna_mastery/gates.py`, Gate 1 linchpin tests |
| Codex SOL High optional slice | `extraction/local_fixture.py`, extraction protocol additions, its focused tests |
| Claude Sonnet routine slice | nested CLI registration and CLI tests only after handler interfaces freeze |
| ChatGPT SOL Ultra | planning/review documents only; no product code |
| Integration owner: Claude Opus on the lead worktree | Gate 1 shared paths (`domain/extraction.py`, `extraction/__init__.py`, `content/protocol.py`, `content/sqlite.py`, `librarian/content_grounding.py`, `src/personal_lms/cli.py`, approved dependency files), later shared router paths, and combined gate reports after a documented ownership handoff |

P0 selects exactly one integration mode: (a) local slice commits and
cherry-picks after Alan explicitly authorizes those local commits, with no
push/PR authority; or (b) single-worktree integration using reviewed diff
hashes and no commits. Slice agents run focused tests plus `git diff --check`;
the integration owner runs the full suite only after the slice is integrated.
Two agents may not edit `src/personal_lms/cli.py`,
`src/personal_lms/policies/router.py`, dependency files, combined reports, or
the same test file concurrently. Shared-path ownership transfers must be
recorded before editing begins.

## Status vocabulary

- `BLOCKED`: a prerequisite is absent; the agent must not start.
- `READY`: prerequisites are reviewed and frozen.
- `IN PROGRESS`: one named owner and one worktree are active.
- `REVIEW`: implementation stopped at a checkpoint with a complete report.
- `DONE`: acceptance evidence was independently reviewed.
- `STOPPED`: a stop condition fired; no later task begins.

Every task below is currently `BLOCKED` or `NOT STARTED`. Nothing in this
planning artifact is a gate pass.

## Before the clock — required preparation

These tasks are outside the 48 hours. Gate time measures the system, not
unfinished content authoring.

| ID | Size | Dependency | Owner role | Status | Stop condition | Expected output |
|---|---:|---|---|---|---|---|
| P0-01 | 30 min | None | Alan + ChatGPT SOL Ultra | BLOCKED | Design/plans are not available from a clean chosen base, or integration authority/mode is undecided. | Approved base SHA, branch/worktree names, scope hash, exact file-ownership/lease table, named Claude Opus integration owner, and either explicitly authorized local-commit/cherry-pick mode or no-commit single-worktree diff-hash mode. |
| P0-02 | 120 min | P0-01 | Alan + Claude Opus | BLOCKED | Any source right, version, byte, hash, MIME, or selector is unresolved. | Reviewed source manifest and immutable source hashes. |
| P0-03 | 120 min | P0-02 | Alan + technical reviewer | BLOCKED | Infographic claim, low-confidence label, malicious paragraph, or wrong-version expectation lacks review. | Visual-review record, quarantine decisions, accessible description, and expected non-effect. |
| P0-04 | 120 min | P0-02 | Alan + technical reviewer | BLOCKED | Six claims, 12 baseline items, six follow-ups, exit probe, or scenario is incomplete. | Complete reviewed objective-2.2 pack records and scenario starting hash. |
| P0-05 | 90 min | P0-04 | Claude Opus reviewer | BLOCKED | Any 10+2 retrieval expectation or scripted learner outcome is ambiguous. | Frozen retrieval cases and four learner vectors with exact expected outcomes. |
| P0-06 | 60 min | P0-02 | Alan | BLOCKED | No declared local parser can extract the searchable PDF from bytes. | Approved dependency/adapter decision, version constraint, and safety bounds. |
| P0-07 | 60 min | P0-03–P0-06 | Alan + ChatGPT SOL Ultra | BLOCKED | Golden command/reviewer, wall/focused-time method, observed-report path/comparison rule, or Alan's explicit authorization for the one local golden-baseline commit is absent. | Accepted read-only Gate 1/2/route base goldens committed locally at an explicitly authorized SHA; exact base-manifest tree/pins and SHA-256; gate schema; wall/focused ledger; `var/ccna-mastery/gates/<run-id>/` observed-path contract; comparison normalization limited to timestamps/elapsed seconds; factory-supplement schema that freezes criteria/evidence-ready inputs but not completed shadow content or a factory golden. No push/PR. |
| P0-08 | 60 min | P0-07 | Claude Opus | BLOCKED | Baseline checks fail or the worktree contains unrelated changes. | Clean worktree report and passing baseline commands. |
| P0-09 | 30 min | P0-08 | ChatGPT SOL Ultra | BLOCKED | Prompt scope exceeds Gate 1 or file ownership overlaps. | Approved Gate 1 start prompt and `clock_status: ready`. |

The clock starts only when P0-01 through P0-09 are `DONE`. The checked-in
design examples are drafts and cannot satisfy these tasks as-is.

The 48-hour wall clock never pauses for ChatGPT review, handoff, integration,
or checkpoint decisions. Every gate records both wall time and focused time;
its final in-clock task includes the recurring SOL High acceptance review.

## Gate 1 — Evidence and Objective-Pack assembler/validator

Timebox: 0–12 hours
Required result: every Gate 1 required check passes
Gate stop: any NO-GO criterion, fixture drift, hosted call, or unreviewed golden
write

| ID | Size | Dependency | Owner role | Status | Stop condition | Expected output |
|---|---:|---|---|---|---|---|
| G1-01 | 30 min | P0-09 | Claude Opus | NOT STARTED | Base SHA, manifest hash, or owned paths differ from prompt. | Start record with exact base, manifest, environment, and zero unrelated diff. |
| G1-02 | 90 min | G1-01 | Claude Opus | NOT STARTED | A criterion cannot be expressed as a failing automated test or explicit human-review assertion. | Red tests for Objective Pack cardinality, resolution, exposure, scoring, and gate schema. |
| G1-03 | 90 min | G1-02 | Claude Opus | NOT STARTED | Generic contracts need an objective-2.2 branch or break old-shaped domain models. | `ObjectivePack` contracts, deterministic loader, canonicalization, and focused green tests. |
| G1-04 | 90 min | G1-01, P0-06 | Codex SOL High or Claude Opus, exclusive lane | NOT STARTED | Adapter trusts sidecar text, reads outside configured roots, expands beyond the frozen fixture adapter, or requires a broad schema migration. | Actual-byte PDF/PNG adapter, hash/MIME/root/size checks, and focused extraction tests. |
| G1-05 | 90 min | G1-03–G1-04 | Claude Opus | NOT STARTED | Rights/use, privacy, trust, quarantine, or objective version is filtered after `LIMIT`. | Eligible evidence envelope and pre-limit content/FTS5 filtering tests. |
| G1-06 | 90 min | G1-05 | Claude Opus | NOT STARTED | Validator trusts declared totals/scores or any required ID is unresolved. | Recomputed reference graph, exposure intersections, six claim scores, stable reason codes. |
| G1-07 | 60 min | G1-05–G1-06 | Claude Opus | NOT STARTED | Any supported query misses top five or unsupported query returns support. | Deterministic 10+2 retrieval report and index-content hash. |
| G1-08 | 60 min | G1-06–G1-07 | Claude Sonnet, after interface freeze | NOT STARTED | Existing no-args/version/ask/demo CLI behavior changes or fixture YAML can self-assert text/image approval. | Nested `ccna-lab evidence approve-region`, `gate evidence`, and `gate report` registration with reviewer-only approval and CLI compatibility tests. |
| G1-09 | 60 min | G1-08 | Claude Opus | NOT STARTED | Normal run can write goldens, run is online, or run-A/run-B hashes differ. | Machine-readable Gate 1 report, golden guard, offline reproducibility result. |
| G1-10 | 60 min | G1-09 | Claude Opus review gate | NOT STARTED | Any required check is non-pass, elapsed time exceeds five minutes, or manual cleanup exceeds four focused hours. | Full checks, diff/secrets/status audit, and detailed Gate 1 completion report. |

Gate 1 tasks consume the full 12-hour allocation. There is no hidden
contingency. An overrun, missing fixture, broad extraction abstraction, schema
migration, UI request, or richer ingestion request stops the gate and triggers
an honest report rather than overtime.

### Gate 1 checkpoint

ChatGPT SOL High reviews the completion report against
`LINCHPIN_TRACEABILITY.md`. Outcomes:

- `ACCEPT`: freeze Gate 1 evidence hashes and write the Gate 2 prompt.
- `REPAIR`: write one bounded repair prompt; Gate 2 remains blocked.
- `NO-GO`: stop the sprint and revise evidence/pack architecture.

## Gate 2 — Closed mastery-loop replay

Timebox: 12–32 hours
Dependency: independently accepted Gate 1
Required result: deterministic, bounded, append-only, replayable learning state
Gate stop: any model authority, silent ambiguity, invalid-command mutation,
nondeterminism, unbounded phase, or premature retained mastery

| ID | Size | Dependency | Owner role | Status | Stop condition | Expected output |
|---|---:|---|---|---|---|---|
| G2-01 | 90 min | Gate 1 ACCEPT | Claude Opus | BLOCKED | Gate 1 fixture/report hashes do not match the prompt. | Red tests for all Gate 2 GO/NO-GO rows and four learners. |
| G2-02 | 90 min | G2-01 | Claude Opus | BLOCKED | Session contracts contain provider objects or objective-specific branches. | Strict generic session/event/grade/facet/bounds contracts. |
| G2-03 | 120 min | G2-02 | Claude Opus | BLOCKED | Selection or scoring depends on free text/model output. | Deterministic item selector, grader, ambiguity disposition, and tests. |
| G2-04 | 120 min | G2-02 | Codex SOL High, separate lane | BLOCKED | Simulator expands beyond reviewed grammar or invalid input mutates state. | Generic practical protocol plus trunk state machine, parser, reason IDs, equivalence tests. |
| G2-05 | 120 min | G2-02 | Claude Opus | BLOCKED | Repository permits update/delete of authoritative event rows or lacks ordering/hash invariants. | Append-only SQLite repository and integrity tests. |
| G2-06 | 90 min | G2-05 | Claude Opus | BLOCKED | Projection reads a mutable snapshot as authority or replay differs. | Pure event fold, interruption/resume path, canonical state/event hashes. |
| G2-07 | 120 min | G2-03, G2-04, G2-06 | Claude Opus | BLOCKED | Acquisition/retention collapse or frozen time alone changes mastery. | Generic mastery evaluator, scheduler, frozen clock, practical-grade integration, and delayed-novel guard. |
| G2-08 | 60 min | G2-03, Gate 1 bundle | Claude Sonnet after interface freeze | BLOCKED | Tutor retrieves outside supplied bundle, general-knowledge mode is reachable, or refusal/citation/source-verification status is ignored. | `GroundedFeedback` adapter that structurally constructs supplied-bundle requests; fake/no-provider authoritative-field comparison; refusal events may differ and remain append-only. |
| G2-09 | 120 min | G2-03–G2-08 | Claude Opus | BLOCKED | Any phase count is unbounded or a failure is averaged/ignored. | Bounded generic runner, CCNA adapter wiring, `gate loop`, and interrupted-session `replay` CLI with exact terminal reasons. |
| G2-10 | 90 min | G2-09 | Claude Opus | BLOCKED | Any scripted outcome differs from frozen expected output. | Four learner run records and exact follow-up/grade/mastery/schedule assertions. |
| G2-11 | 120 min | G2-10 | Claude Opus review gate | BLOCKED | Focused/full checks fail, replay differs, or fake replacement changes authority. | Gate 2 reports, full validation, diff/status audit, detailed completion report. |

Gate 2 has 19 hours of planned focused tasks and one hour contingency. A failed
Gate 2 blocks Gate 3 even if provider routing looks safe.

### Gate 2 checkpoint

ChatGPT SOL High independently compares:

- event and final-state hashes;
- all four learner outcomes;
- fake/no-provider authority hashes;
- phase-count bounds;
- acquisition versus retention evidence; and
- every G2 row in `LINCHPIN_TRACEABILITY.md`.

Only `ACCEPT` produces the Gate 3 prompt.

## Gate 3 — Route isolation and objective factory

Timebox: 32–44 hours
Dependency: independently accepted Gates 1 and 2
Required results: route isolation for August 1; separate factory result for
scale
Gate stop: fake selectable in live, qualification bypass, SDK/domain leakage,
provider authority, canonical validation bypass, or objective-specific core
branch

| ID | Size | Dependency | Owner role | Status | Stop condition | Expected output |
|---|---:|---|---|---|---|---|
| G3-01 | 60 min | Gate 2 ACCEPT | Claude Opus | BLOCKED | Gate 1/2 evidence hashes differ from the prompt. | Red route-isolation and factory tests, independent subgate status model. |
| G3-02 | 90 min | G3-01 | Claude Opus | BLOCKED | Qualification lacks task/schema/profile/expiry or can be self-asserted by a provider. | Generic execution profile and persisted qualification contracts. |
| G3-03 | 90 min | G3-02 | Claude Opus | BLOCKED | Existing Tier 0/local/privacy/static-budget/no-fallback tests regress. | Injected qualification-policy/router eligibility, exact capability matching, and deployment-owned fake-live prohibition; the structural registry remains unchanged. |
| G3-04 | 60 min | G3-02 | Codex SOL High, separate lane | BLOCKED | Provider schema or SDK object enters a domain model. | Canonical result projector and adversarial projection tests. |
| G3-05 | 60 min | G3-02 | Claude Sonnet or Codex, exclusive lane | BLOCKED | Code hard-codes a Qwen tag or smoke becomes required when Ollama is unavailable. | Ollama qualification metadata extension and deferred-smoke tests. |
| G3-06 | 60 min | G3-03–G3-05 | Claude Opus | BLOCKED | Any of five route checks is not pass; Qwen deferral hides a required failure. | Route-isolation subreport and existing router/provider regression suite. |
| G3-07 | 90 min | Gate 1 generic loader; evidence-ready mark | Technical content owner | BLOCKED | Measured work begins before evidence-ready or requires Python/schema edits. | First half of complete objective-1.5 shadow pack and focus ledger. |
| G3-08 | 90 min | G3-07 | Technical content owner + Claude Opus review | BLOCKED | Total exceeds three hours, records remain unreviewed/incomplete, or Python/domain-schema changes are required. | Reviewed 12-item shadow pack, follow-ups, exit probe, policy, and factory-supplement hash; shadow content changes are allowed but Python/domain-schema changes are not. |
| G3-09 | 60 min | G3-06, G3-08 | Claude Opus review gate | BLOCKED | Route or factory status is missing, same loader/runner cannot load the pack, missing practical evidence produces full mastery, or integrated checks fail. | Combined `gate factory`/Gate 3 report, full compatibility checks, independent trial/scale decisions, and detailed completion report. |

Gate 3 has 11 hours of planned focused tasks and one hour contingency. The Qwen
smoke may be deferred only for unavailable Ollama. Route isolation itself may
not defer.

### Gate 3 checkpoint

ChatGPT SOL High records two independent decisions:

- `route_isolation`: pass/fail for the August 1 one-objective trial;
- `factory_scalability`: pass/fail for several objectives per day.

Factory failure does not erase a valid objective-2.2 result. Route failure
blocks August 1 regardless of factory status.

## Final frozen comparison and decision

Timebox: 44–48 hours
Dependency: completed Gate 3 report, including both subgates

| ID | Size | Dependency | Owner role | Status | Stop condition | Expected output |
|---|---:|---|---|---|---|---|
| F-01 | 60 min | G3-09 | Claude Opus | BLOCKED | Any expected artifact changed outside reviewer acceptance or manifest hash drifted. | Frozen-versus-observed comparison with every mismatch listed. |
| F-02 | 60 min | F-01 | Claude Opus | BLOCKED | Format, lint, type, tests, diff-check, status, or secret scan is missing/failing. | Exact final command transcript and changed-path inventory. |
| F-03 | 60 min | F-02 | ChatGPT SOL High | BLOCKED | Report collapses route/factory statuses or treats Qwen deferral as core pass evidence. | Audited decision matrix and blocker/risk list. |
| F-04 | 60 min | F-03 | Alan | BLOCKED | Evidence does not support the decision or review requests remain unresolved. | Signed next-investment decision and next bounded prompt. |

## Mandatory checkpoint protocol

Every implementation prompt must contain:

1. exact objective and excluded work;
2. base SHA, branch, worktree, fixture manifest hash, and clock status;
3. owned paths and forbidden paths;
4. existing symbols/tests to reuse;
5. acceptance rows from `LINCHPIN_TRACEABILITY.md`;
6. required red tests before implementation;
7. focused and full validation commands;
8. stop conditions;
9. no commit/push/PR/golden-write authority unless explicitly granted; and
10. the report template below.

At each checkpoint, the coding agent must stop and report. It may not assume
silence means approval.

### Checkpoint A — Scope and baseline

- Confirm exact base/head SHA and worktree.
- Show `git status --short`.
- Confirm fixture manifest hash and owned paths.
- Report existing tests and intended new tests.
- Stop on unrelated tracked changes or overlapping ownership.

### Checkpoint B — Red proof

- List failing tests and why each failure represents missing behavior.
- Confirm failures are not fixture/path/import mistakes.
- Stop if a requirement cannot be made deterministic.

### Checkpoint C — Focused implementation

- Identify reused existing symbols.
- Identify every new bounded symbol.
- Show focused test outcomes.
- Stop on architectural duplication or scope expansion.

### Checkpoint D — Gate validation

- Run the exact gate and compare observed hashes to frozen expectations.
- Report every pass/fail/deferred check.
- Stop immediately on a required failure.

### Checkpoint E — Full review

- Run format, lint, mypy, full pytest, diff check, status, and secret review.
- Record skipped tests and why.
- Review compatibility and privacy/provider boundaries.
- Produce the detailed report; do not continue to the next gate.

## Detailed report template

Every Claude/Codex phase report pasted back to ChatGPT SOL High must use this
shape:

```markdown
# <Gate/Phase> Completion Report

## Executive result
- Result: COMPLETE | PARTIAL | STOPPED | BLOCKED
- Gate status: PASS | FAIL | NOT RUN
- Clock status and focused time:
- One-sentence evidence-based judgment:

## Provenance
- Repository/worktree:
- Branch:
- Base commit:
- Head commit:
- Fixture manifest SHA-256:
- Gate schema/policy versions:

## Scope
- Requested:
- Completed:
- Explicitly not done:
- Assumptions made:

## Changed paths
| Path | Purpose | Owner | New/modified | Why required |

## Existing capabilities reused
| Existing symbol/path | How reused | Regression test |

## Contract and authority review
- Deterministic authorities:
- Model/provider authority:
- Privacy and rights/use path:
- Budget and route path:
- Persistence/replay path:
- Golden-write behavior:

## Acceptance traceability
| Check ID | Required | Status | Planned test | Actual result | Evidence path/hash | Reason code |

## Commands and exact results
| Command | Exit | Passed/failed/skipped | Duration | Notes |

## Gate artifacts
- Observed report:
- Expected report:
- Comparison:
- Event/state/index hashes:
- Reviewer acceptance record, if explicitly authorized:

## Risks and findings
| Severity | Finding | Evidence | Blocks current gate? | Recommended action |

## Compatibility review
- Existing CLI:
- Routing/provider isolation:
- Grounding/Tutor:
- Privacy/budget:
- Legacy Build Week behavior:

## Control-plane portability note
- Reusable task/result/policy/event seam added:
- Any CCNA-specific coupling found:
- No A2A/MCP implementation was added: yes/no

## Git and workspace state
- `git status --short`:
- Unrelated changes preserved:
- Commit/push/PR performed: no, unless Alan explicitly authorized and report cites it

## Review requests
1. Exact question for Alan/ChatGPT.

## Recommended next bounded prompt
- Proceed | repair | stop:
- Next gate/slice:
- Preconditions:
```

The control-plane portability note identifies generic coupling only. Detailed
CML MCP/A2A mapping is a separate post-gate planning exercise; this sprint
authorizes no protocol or framework code.

## Required commands at every full review

```bash
uv run --no-sync ruff check --no-cache .
uv run --no-sync ruff format --check --no-cache .
uv run --no-sync mypy --no-incremental src
uv run --no-sync pytest -p no:cacheprovider
git diff --check
git status --short
```

Use synced commands only when an approved dependency intentionally changes the
lock/environment, and report that fact. Never replace a failed check with a
claim that it is “probably unrelated.”

## Sprint-level stop conditions

Stop all later work if:

- the pre-clock fixture is not complete, reviewed, and frozen;
- implementation begins in the canonical shared worktree;
- two agents own the same path;
- an unreviewed source or answer key enters the pack;
- a hosted call is attempted;
- an agent adds A2A, MCP, a second orchestration framework, vector database,
  browser UI, or standalone app;
- an LLM/provider gains selection, grading, mastery, scheduling, or golden
  authority;
- a normal command rewrites expected output;
- a required gate check fails;
- the implementation report omits evidence needed for independent review; or
- remaining time is used to waive a criterion rather than report an honest
  no-go.
