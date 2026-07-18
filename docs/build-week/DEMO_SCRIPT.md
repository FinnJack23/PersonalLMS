# Grounded Tutor Demo Script

## 0:00–0:30 — Opening

“Personal learning archives are useful but messy: they contain duplicates, placeholders, stale versions, and unclear rights. Grounded Tutor demonstrates a safer path from approved evidence to a lesson and measurable practice.”

## 0:30–1:00 — Problem and architecture

Show the Source Readiness panel. Explain that deterministic services prepare evidence first, SQLite holds runtime review state, and Obsidian remains durable reviewed knowledge rather than runtime state. The demo fixture is synthetic.

## 1:00–2:00 — Evidence to lesson

Show the objective, approved evidence E1, and the generated lesson. Point out the E1 citation. Point out the explicit retrieval gap: the fixture does not support an administrative-distance claim, so the system preserves the gap instead of inventing an answer.

## 2:00–3:00 — Evidence to drill

Show the three questions: recall, applied, and misconception. Explain that the drill is derived from the verified lesson and evidence rather than from an ungrounded free-form answer.

## 3:00–3:30 — Mastery tracking

Show the review state and explain that mastery persistence uses local SQLite. No Obsidian write or hosted call is required for this path.

## 3:30–4:15 — Privacy block

Explain that hosted routing is public-only. The adapter rejects internal, sensitive, and restricted-local requests before HTTP client construction; local Ollama is the intended local route.

## 4:15–5:00 — Closing and fallback

“The portfolio value is a local-first learning workflow with evidence checks, citations, explicit uncertainty, privacy gates, and durable review state.” If hosted API access is unavailable, use the exact offline command below; it is the primary judge path.

```bash
uv sync
uv run personal-lms build-week-demo
```
