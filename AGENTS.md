# Codex project map

This file is the short entrypoint for Codex. Detailed requirements live under `docs/`.

## Mission

Build a local-first Personal LMS that coordinates specialized learning agents while keeping durable knowledge in an Obsidian vault.

## Read order

1. `README.md`
2. `docs/exec-plans/active/2026-07-16_PERSONAL_LMS_MULTI_AGENT_MASTER_PLAN.md`
3. The product specification relevant to the current task
4. The active handoff document
5. Existing tests and architecture decisions

## Non-negotiable architecture

- CrewAI Flows orchestrate deterministic control.
- Crews are bounded, task-specific collaborations.
- Agent identities must not contain hard-coded model vendors.
- Tier 0 deterministic services run before any LLM call.
- Tier 1 local Qwen through Ollama is the default.
- Tier 2 hosted APIs require router approval and budget checks.
- Obsidian is durable knowledge, not runtime state.
- SQLite stores inventory, queues, metrics, approvals, and audit records.
- Raw source archives remain outside the vault.
- No unreviewed bulk promotion into curated Obsidian folders.

## Shared-workspace rule

The canonical root is `/home/ajsch/projects/personal-lms`.

Codex may inspect the same worktree Claude is using, but concurrent writes are prohibited. For active Codex implementation, create a separate Git worktree and branch.

Example:

```bash
git worktree add ../personal-lms-codex -b codex/<task-name>
```

## Safety and privacy

- Never commit credentials, tokens, passwords, private IP addresses, financial records, health records, or private account data.
- Never send restricted content to a hosted API.
- Do not give agents unrestricted shell or filesystem access.
- Vault writes must be atomic and restricted to configured paths.
- Deletion, overwrite, publication, and external transmission require explicit approval.

## Engineering rules

- Python 3.12+.
- Prefer `uv` for environment and dependency management.
- Use Pydantic models for structured boundaries.
- Add tests with every behavior change.
- Keep modules small and explicit.
- Prefer dependency injection over global clients.
- Add provider adapters rather than embedding provider calls in agents.
- Store configuration in versioned examples and local untracked overrides.
- Run format, lint, type, unit, and integration tests before completion.

## Required checks

The implementation phase should establish and maintain commands equivalent to:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Do not claim completion if required checks were not run. State what was not validated.

## Documentation discipline

- Keep this file concise.
- Put durable architecture in `docs/product-specs/`.
- Put decisions in `docs/decisions/`.
- Put active execution plans in `docs/exec-plans/active/`.
- Put completed plans in `docs/exec-plans/completed/`.
- Update the relevant document when an implementation decision changes.

## Current task priority

Use `docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md` as the initial implementation scope. Do not build every agent in the first run.
