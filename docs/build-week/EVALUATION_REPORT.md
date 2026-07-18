# Build Week Evaluation Report

## Scope and honesty statement

This report uses synthetic/public-safe fixtures only. No production Obsidian vault, private archive, or hosted private content was accessed.

## Local-model lane

- Model: `qwen3.5:9b`
- Requested settings: `think=false`, context `8192`, `keep_alive=8h`
- Cases attempted: 1
- Completed cases: 0
- Pass rate: not available
- Result: the WSL process could not connect to the reported Windows `localhost:11434` service through the existing adapter. No retry, model download, or alternate integration was attempted.

This limitation does not invalidate the deterministic or hosted adapter results, and it does not block the offline judge path.

## Deterministic evidence

The focused Grounded Tutor/provider slice passed `33 tests`; the full suite passed `1176 tests` with `3 skipped`. These tests cover grounded lesson behavior, E1 citation preservation, explicit retrieval gaps, exactly three drill questions, SQLite mastery persistence, privacy classification behavior, malformed provider responses, incomplete Ollama responses, unavailable providers, and no-retry behavior.

The offline loopback demo returned HTTP 200 and visibly contained the Grounded Tutor title, E1, retrieval-gap text, and SQLite mastery text.

## Hosted adapter evidence

Two serialized synthetic hosted calls were made. The first exposed a local response-parser defect; the correction extracted `output[].content[]` text. The second passed with model `gpt-5.6`, 122 input tokens, 41 output tokens, grounded `/32` explanation, E1 citation, `store=false`, and retries `0`. No private content was transmitted.

## Limitations and next action

A local Ollama result must be rerun from an environment that can reach the running service. The current release package makes no claim of a completed qwen3.5:9b pass rate. Publication remains blocked until a Git remote and publication authority are supplied.
