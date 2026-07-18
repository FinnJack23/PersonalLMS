# Claude Code Night Run — Personal LMS Foundation

**Date:** 2026-07-16  
**Working directory:** `/home/ajsch/projects/personal-lms`  
**Mode:** Foundation implementation only  
**Do not use paid model APIs in this run.**

## Mission

Create a tested, provider-neutral foundation for the Personal LMS multi-agent system. Do not build the full agent roster or process the source archive tonight.

## Required reading

1. `README.md`
2. `CLAUDE.md`
3. `docs/exec-plans/active/2026-07-16_PERSONAL_LMS_MULTI_AGENT_MASTER_PLAN.md`
4. `docs/product-specs/AGENT_ROSTER_AND_CONTRACTS.md`
5. `docs/product-specs/MODEL_ROUTER_AND_COST_CONTROLS.md`
6. `docs/product-specs/OBSIDIAN_SOURCE_ARCHITECTURE.md`

## Scope

### Commit 1 — Repository and development tooling

Create:

- `pyproject.toml` for Python 3.12+;
- `uv` dependency workflow;
- `src/personal_lms/` package;
- `tests/` structure;
- Ruff, mypy, and pytest configuration;
- `.gitignore` covering secrets, local databases, caches, vault test outputs, and environment files;
- `.env.example` containing names only, never values;
- a minimal command-line entrypoint that reports application version.

No CrewAI feature code beyond dependency setup is required in this commit.

### Commit 2 — Domain schemas

Implement Pydantic schemas for:

- `AgentRequest`;
- `AgentResponse`;
- `SourceCitation`;
- `ModelCapabilityProfile`;
- `ModelRequest`;
- `ModelResult`;
- `RoutingDecision`;
- `PrivacyClassification`;
- `BudgetPolicy`;
- `ApprovalRequest`;
- `RunState`;
- `VaultNoteDraft`.

Add serialization, validation, and negative tests.

### Commit 3 — Model-provider contracts

Implement provider-neutral interfaces:

- `ModelProvider` protocol or abstract base class;
- `FakeLocalProvider`;
- `FakeHostedProvider`;
- provider registry;
- typed provider errors;
- usage and latency metadata.

No real OpenAI, Anthropic, Gemini, or Ollama call is required yet.

### Commit 4 — Deterministic router policy

Implement a pure, testable router that considers:

- whether the task needs an LLM;
- requested capability;
- privacy classification;
- presence of images;
- context-size estimate;
- previous local failures;
- validation failures;
- user local-only preference;
- budget availability;
- explicit premium request.

Required behaviors:

- deterministic work returns Tier 0;
- routine work defaults to local Qwen profile;
- prohibited private data cannot route to hosted providers;
- hard budget limit blocks hosted calls;
- complex vision can recommend hosted escalation;
- local-only mode prevents hosted escalation;
- every decision includes machine-readable reasons.

### Commit 5 — Personal Assistant Flow skeleton

Create a CrewAI Flow or compatible skeleton that:

- accepts an `AgentRequest`;
- calls the deterministic router;
- uses fake providers only;
- returns an `AgentResponse`;
- persists minimal state in memory or a temporary SQLite fixture;
- has no unrestricted tools;
- performs no real vault write;
- performs no network request.

Keep the Flow small. Do not implement all specialist agents.

### Commit 6 — Ollama discovery stub or adapter

Only after the previous commits and tests pass:

- implement an Ollama adapter behind the provider interface, or create a documented stub;
- configurable base URL, defaulting to local only;
- health/model-list method;
- timeout and typed failure;
- do not require Qwen to be running for unit tests;
- integration test must be opt-in and skipped by default.

### Commit 7 — Documentation and handoff

Update:

- README local setup;
- architecture summary;
- exact test commands;
- current limitations;
- next recommended commit sequence;
- Qwen-compatible backlog tasks.

## Prohibited scope

Do not implement tonight:

- complete Obsidian write access;
- PDF/video/URL extraction;
- vector databases;
- authentication;
- OpenClaw;
- cloud deployment;
- automatic hosted API calls;
- every planned agent;
- unrestricted shell or filesystem tools;
- full UI;
- CML write operations.

## Required checks

Run and report exact results:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Add a single aggregate command if useful.

## Acceptance criteria

- clean package scaffold;
- provider-neutral domain layer;
- deterministic router fully tested;
- fake-provider Personal Assistant Flow executes;
- private-data and budget rules are tested;
- no credentials or private data in repository;
- no paid API call;
- no real source archive or Obsidian vault modified;
- clean Git status after commits;
- handoff lists exact next steps.

## Claude launch prompt

Paste the following after the project files are in the canonical folder:

```text
You are implementing the Personal LMS foundation in this repository.

Read CLAUDE.md and all required documents listed in
`docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md` before editing.

Execute the night-run plan commit by commit. Keep scope strictly bounded to the
foundation: repository tooling, domain schemas, provider contracts, deterministic
model routing, fake-provider Personal Assistant Flow, and optionally an Ollama
adapter after all earlier checks pass.

Do not use paid APIs. Do not access or modify a real Obsidian vault. Do not ingest
PDFs, videos, URLs, or private data. Do not implement the entire agent roster.

Use Python 3.12+, uv, Pydantic, Ruff, mypy, and pytest. Add tests with each behavior.
Preserve provider portability: agent roles request capability profiles, never vendor
model names. Tier 0 is deterministic Python, Tier 1 is local Qwen/Ollama, and Tier 2
is hosted escalation controlled by policy.

Before each commit, summarize its scope. After each commit, run the relevant tests.
At the end run the full required check suite and report exact results, commits,
remaining risks, and next commands. Leave the worktree clean.
```
