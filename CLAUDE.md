# Claude Code instructions — Personal LMS

## Purpose

Implement the Personal LMS as a local-first, provider-portable multi-agent learning system centered on Obsidian.

## Before editing

Read, in order:

1. `README.md`
2. `docs/exec-plans/active/2026-07-16_PERSONAL_LMS_MULTI_AGENT_MASTER_PLAN.md`
3. `docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md`
4. Relevant files in `docs/product-specs/`
5. Relevant architecture decisions in `docs/decisions/`

## Core implementation model

- CrewAI Flows own routing, state, policy, approvals, and workflow sequencing.
- Specialist CrewAI agents are invoked by the Flow for bounded reasoning tasks.
- Agent role definitions are provider-neutral.
- Model selection occurs through a dedicated model router.
- Deterministic Python runs before local or hosted inference.
- Qwen through Ollama is the default inference tier.
- Hosted APIs are used only after explicit escalation rules and cost/privacy checks.
- Obsidian stores durable reviewed knowledge; SQLite stores catalogs and runtime state.

## Scope control

Do not implement the whole product in one session. Follow the active handoff exactly. Prefer small, reviewable commits with tests.

## Parallel-agent rule

The canonical worktree is `/home/ajsch/projects/personal-lms`.

Do not edit files that another coding agent is editing. Qwen and Codex implementation work must occur in separate Git worktrees. Documentation-only suggestions can be deposited into `docs/inbox/` for later review.

## Security

- No real credentials in code, tests, fixtures, docs, logs, or commits.
- No unrestricted shell tool.
- No unrestricted filesystem tool.
- No automatic upload of raw private files to hosted APIs.
- Hosted escalation accepts only redacted, minimal excerpts unless the user explicitly approves otherwise.
- All write tools require configured roots, path validation, and atomic replacement.

## Code quality

- Python 3.12+ and `uv`.
- Pydantic for schemas.
- Ruff for formatting/linting.
- Mypy for typing.
- Pytest for tests.
- Structured logging with secret redaction.
- Explicit interfaces for model providers, vault access, catalog access, and budget policy.

## Commit discipline

Use focused commits. Suggested initial sequence:

1. repository and tooling scaffold;
2. domain schemas and configuration;
3. model-provider interfaces and fake adapters;
4. deterministic router policy;
5. Obsidian read-only adapter and tests;
6. vertical-slice Flow with fake agents;
7. Ollama adapter and local smoke-test script;
8. documentation and handoff.

Do not enable hosted paid calls automatically during scaffolding.

## Completion report

Report:

- commits created;
- files changed;
- tests run and exact results;
- unresolved decisions;
- local commands Alan should run next;
- whether any API or real vault access was used.
