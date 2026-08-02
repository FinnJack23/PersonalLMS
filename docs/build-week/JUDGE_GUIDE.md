# Grounded Tutor Judge Guide

## Purpose

Grounded Tutor is a local-first Personal LMS vertical slice that turns approved evidence into a cited lesson, explicit retrieval gaps, three drill questions, and locally persisted mastery state.

## Prerequisites

- Python 3.12+
- `uv`
- No API key for the default offline demo
- Optional: `uv sync --extra openai-live` and a configured `OPENAI_API_KEY` for the separately approved hosted adapter smoke

The offline demo uses bundled synthetic fixtures and does not read a production vault or private archive.

## 60-second quick start

```bash
uv sync
uv run personal-lms build-week-demo
```

Open `http://127.0.0.1:8000`. The page should show `Grounded Tutor`, an E1
citation, a retrieval-gap warning, exactly three questions, and no answer key.
Submit all three answers to see deterministic correct/incorrect feedback. The
default database is `$PWD/.local/personal-lms/grounded-tutor.sqlite3`; it is
ignored by Git and persists across restarts. Use `--db-path /path/to/demo.sqlite3`
for an isolated database, or remove that file to reset it.

## Five-minute demonstration

1. Show Source Readiness: imported items, duplicate indicators, placeholder exclusion, rights review, and approved evidence.
2. Show the objective: explain connected and local Cisco routes.
3. Show approved evidence E1 and the generated lesson.
4. Point out the preserved E1 citation and explicit administrative-distance retrieval gap.
5. Show exactly three drill questions: recall, applied, and misconception.
6. Submit three answers and show the deterministic results plus the local SQLite
   review-record count. Restart with the same database path to show persistence.
7. Explain that the default path is deterministic offline simulation; hosted GPT-5.6 is an opt-in, separately controlled adapter path.

## Privacy boundary demonstration

The hosted adapter accepts only `PUBLIC` requests and rejects `INTERNAL`, `SENSITIVE`, and `RESTRICTED_LOCAL_ONLY` before constructing the HTTP client. The focused transport-spy tests prove this boundary. Local Ollama is the intended route for restricted-local material.

## Offline versus hosted behavior

- Offline demo: no API key, no network, synthetic fixture, loopback UI.
- Hosted validation: only synthetic public-safe evidence, `store=false`, zero retries, no tools or persistence, and a bounded output limit. Do not submit private archives or production vault content.

## Troubleshooting

- `uv` missing: install `uv`, then rerun `uv sync`.
- Hosted extra missing: run `uv sync --extra openai-live` before the approved live smoke.
- Port busy: use `uv run personal-lms build-week-demo --port 8001` and open the matching URL.
- Ollama unavailable: the offline demo remains usable; do not pull models automatically.
- Hosted key unavailable: stop the live phase; the offline path does not require it.

## Known limitations

The UI is an offline simulated fixture rather than the full production Tutor
composition or a production web application. The WSL validation process could
not reach the reported Windows `localhost:11434` Ollama service, so no completed
local-model evaluation result is claimed. Historical release records that mention
an absent Git remote or an earlier branch are superseded by current repository
state; publication remains a manual follow-up.

Question 1 uses exact-text matching against the quoted evidence passage, so a
reasonable paraphrase may be marked incorrect. During a controlled demo, copy
the quoted evidence text when answering q1.

The independent review's optional edge-case tests remain follow-up work and are
not included in this repair.
