# Grounded Tutor Release Notes

## Release candidate

Release candidate: `4014fbd3b975d3e52712e6eda2de39b75997cdcc`

Final hardening/package commit: `4b0d263057de07102e96693cb713d1496c08f875`

## Features

- Offline Grounded Tutor demonstration with synthetic fixtures
- Evidence citations, retrieval gaps, drill questions, and SQLite mastery state
- OpenAI Responses adapter with raw response parsing, `store=false`, zero retries, and public-only transport boundary
- Optional `openai-live` dependency extra
- Judge guide, demo script, evaluation report, failure matrix, and submission summary

## Validation

- `uv run pytest`: `1176 passed, 3 skipped`
- Focused release tests: `33 passed`
- Ruff, formatting, mypy, diff check, and secret scan: passed
- Offline loopback demo: HTTP 200 with expected judge markers
- Hosted synthetic adapter: passed on `gpt-5.6`; 2 cumulative calls, one corrective retry

## Security and privacy

Hosted transport rejects non-public content before HTTP client construction. No production vault or private archive was used. `.env.local` remained ignored, untracked, and unstaged.

## Known limitations and setup

The local qwen3.5:9b evaluation could not reach Windows localhost from WSL. Install `uv`, run `uv sync` for offline mode, and run `uv sync --extra openai-live` only for approved hosted validation. No Git remote is configured; nothing was pushed or published.
