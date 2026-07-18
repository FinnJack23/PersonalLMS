# Grounded Tutor — Submission Summary

## Value proposition

Grounded Tutor turns approved learning evidence into a cited lesson, explicit uncertainty, three practice questions, and locally persisted mastery state.

## Problem and target user

It serves learners with messy personal archives who need trustworthy study help without sending private material to hosted services.

## Capabilities

- Source readiness and approval signals
- Evidence-grounded lesson generation
- Citation preservation and retrieval-gap reporting
- Recall, applied, and misconception drills
- SQLite mastery persistence
- Public-only hosted routing with pre-transport privacy enforcement
- Local-first architecture with optional Ollama routing

## Architecture and controls

CrewAI Flows provide deterministic orchestration boundaries; bounded provider adapters handle local or hosted inference. SQLite stores runtime state and audit data. Obsidian is durable reviewed knowledge. Hosted validation uses synthetic public-safe input, `store=false`, zero retries, no tools, and bounded output.

## Evaluation

The full suite passed `1176 passed, 3 skipped`; the focused release slice passed `33 passed`; Ruff, formatting, mypy, diff, and secret checks passed. The offline loopback demo returned HTTP 200 with Grounded Tutor, E1, retrieval-gap, and SQLite markers. The hosted synthetic adapter smoke passed on `gpt-5.6`. The qwen3.5:9b lane was attempted but could not reach Windows localhost from WSL, so no local-model pass rate is claimed.

## Demonstration

```bash
uv sync
uv run personal-lms build-week-demo
```

## Limitations and roadmap

The UI is a demonstration path, not a production application. SQLite/unwritable recovery, concurrency stress, and a reachable local-model evaluation rerun remain follow-up work. Publication requires a configured Git remote and explicit authority.
