# Personal LMS Night Closeout — Foundation Session

**Date:** 2026-07-16
**Branch:** `claude/phase-0-foundation`
**Type:** Documentation-only closeout. No product functionality was changed in this session.

## 1. Executive summary

Tonight's session built the framework-neutral foundation for the Personal LMS: domain schemas, provider-neutral model contracts, a deterministic routing policy, a testable Personal Assistant application flow, an optional CrewAI orchestration adapter, and an optional Ollama local-provider adapter. Every layer above the domain core is opt-in: the base install (`uv sync`) resolves to a lightweight core with no CrewAI and no HTTP client for Ollama; `--extra crewai` and `--extra ollama` each add one adapter independently, and the core (including `PersonalAssistantFlow`) behaves identically with any combination of extras installed. No hosted API calls were made, no real Obsidian vault was touched, and no source material (PDF, video, URL, or private record) was ingested. This matches the "Immediate action for tonight" scope in the master plan (section 17) and the Night Run handoff's prohibited-scope list.

## 2. Exact commit history (approved)

```text
9f21721 feat: add optional Ollama local provider
ca8d994 refactor: make CrewAI orchestration optional
02024aa feat: add CrewAI orchestration adapter
5b8b10c feat: add Personal Assistant Flow skeleton
31b5da2 feat: add deterministic model-routing policy
8a39613 feat: add model provider contracts and fakes
fc7da62 docs: define RAG knowledge plane and CCNA portfolio goal
b48d073 feat: add core domain schemas
43c6a54 chore: pin development environment to Python 3.12
5f7404d chore: scaffold Personal LMS Python project
f8c22c4 docs: initialize Personal LMS architecture
```

`f8c22c4` is `main`. All ten subsequent commits are on `claude/phase-0-foundation`.

## 3. Current package/deployment modes

| Mode | Install command | Contents |
|---|---|---|
| Core only | `uv sync` | Domain schemas, provider registry, fake providers, `DeterministicRouter`, framework-neutral `PersonalAssistantFlow`. No CrewAI, no `httpx`. |
| Ollama enabled | `uv sync --extra ollama` | Core plus `httpx` and `OllamaProvider` (loopback-only by default; see `docs/product-specs/OLLAMA_LOCAL_PROVIDER.md`). |
| CrewAI enabled | `uv sync --extra crewai` | Core plus `CrewAIPersonalAssistantFlow`, a thin adapter over `PersonalAssistantFlow` (see ADR-0001). |
| CrewAI plus Ollama | `uv sync --extra crewai --extra ollama` | Both adapters installed together; each extra is independent of the other and of the core. |

## 4. Test results (latest approved commit, `9f21721`)

| Mode | Result |
|---|---|
| core | 206 passed, 6 skipped |
| Ollama | 245 passed, 3 skipped |
| CrewAI plus Ollama | 262 passed, 3 skipped |

The core figure (206 passed, 6 skipped) was re-verified live during this closeout session by running `uv run pytest` in the current environment (no extras installed) — result matched exactly. The Ollama and CrewAI+Ollama figures are carried forward from the approved session record; this closeout did not run `uv sync` or install extras, per the constraint on this session.

Skipped tests in the core-only run are gated behind `--extra crewai` and `--extra ollama` (`OllamaExtraNotInstalledError` / `CrewAIExtraNotInstalledError` guard tests, plus adapter test modules that `importorskip`).

## 5. Major architectural decisions

- **Provider-neutral agent identities** (ADR-0002) — agent roles request capability profiles, never a vendor or model name directly.
- **Deterministic routing outside CrewAI** — `DeterministicRouter` is pure Python, fully testable without any LLM or framework, and is the single place routing logic lives regardless of which orchestration layer calls it (ADR-0001).
- **Optional CrewAI adapter** — `CrewAIPersonalAssistantFlow` is a thin `crewai.flow.flow.Flow` subclass that delegates to `PersonalAssistantFlow.run()` unchanged and projects only audit-safe fields into CrewAI's flow state; CrewAI is loaded lazily and is a 129-transitive-package extra, not a core dependency (ADR-0001).
- **Optional Ollama provider** — `OllamaProvider` speaks Ollama's native HTTP API only (`/api/version`, `/api/tags`, `/api/chat`), defaults to loopback-only, never manages models, and is gated behind the `ollama` extra so `httpx` is not a core dependency (`docs/product-specs/OLLAMA_LOCAL_PROVIDER.md`).
- **Local-first / API-by-exception** — Tier 0 (deterministic Python) and Tier 1 (local Qwen via Ollama) are the defaults; Tier 2 (hosted APIs) requires explicit escalation rules, budget policy, and privacy classification checks that have not yet been implemented against a real hosted provider.
- **Domain-neutral RAG knowledge plane** (ADR-0004) — RAG is documented as its own architectural plane, generic across knowledge packs (CCNA first, CompTIA A+ next), with no domain-specific required fields on shared models. This ADR and the accompanying spec are design-only tonight: no RAG code, schemas, or dependencies were added.
- **Obsidian as durable truth** (ADR-0003) — the vault remains the sole durable store of reviewed knowledge; RAG indexes and SQLite runtime state are derived/rebuildable, never authoritative.

## 6. Security and privacy controls already established

- No credentials, secrets, or real private data exist in code, tests, fixtures, docs, or commits.
- `OllamaProviderConfig` rejects non-loopback hosts by default (`allow_non_loopback` must be set explicitly), and rejects embedded credentials, query strings, fragments, and non-HTTP(S) schemes.
- `OllamaProvider` performs no model management (no pull/push/create/copy/delete) and never shells out to the `ollama` CLI — `generate()` is exactly one HTTP request with a typed failure on error, no retry.
- The CrewAI adapter projects only audit-safe fields (request ID, run ID, run status, routing outcome, provider ID, error type) into CrewAI's own flow state — prompt text never crosses that boundary.
- `DeterministicRouter` enforces that privacy-classified restricted data cannot route to a hosted provider, and that hard budget limits block hosted calls — both are unit-tested.
- No unrestricted shell tool and no unrestricted filesystem tool exist anywhere in `src/`.
- Fake providers only are exercised in `PersonalAssistantFlow` tests; no network call occurs in the test suite by default (Ollama/CrewAI integration tests are opt-in and skip without their extra installed).

## 7. Explicitly deferred work

Per the master plan's phase boundaries and the Night Run's prohibited-scope list, none of the following were started tonight:

- Obsidian read/write adapter (Phase 2) — no vault access exists yet.
- PDF/video/URL extraction and the source catalog (Phases 6–7).
- RAG implementation — schemas, SQLite FTS5, Qdrant, embedding pipeline, chunking, retrieval (design-only via ADR-0004 and the RAG spec; no code).
- Hosted API adapter and live escalation (Phase 5) — no hosted provider has been called, configured with real credentials, or exercised beyond fakes.
- Specialist agent runtimes beyond the Personal Assistant Flow skeleton (Tutor, Librarian, Curator, Drill Master, Lab Coach, Coach, and the rest of the roster in the master plan section 5).
- CLI-driven end-to-end vertical slice using a real local model.
- Any UI, authentication, CML integration, or OpenClaw gateway work.

## 8. Tomorrow's first live procedure

To be performed manually, on Windows, with Ollama running — not part of tonight's session:

1. Launch Ollama on Windows.
2. Call `GET /api/version` and confirm the server responds.
3. Call `GET /api/tags` and record the exact installed Qwen tag (e.g. `qwen2.5:7b-instruct` — confirm the actual string, do not assume).
4. Identify the exact installed Qwen tag from that response and record it in configuration.
5. Perform one bounded local chat request directly against Ollama (outside this codebase) to confirm the model responds as expected.
6. Perform one Personal LMS `OllamaProvider` smoke test (`uv sync --extra ollama`, then the opt-in integration test or a small manual script) against the now-running local server, using the confirmed model tag.

Do not start Ollama or run a live network request as part of this closeout session — that constraint applied to tonight only.

## 9. Recommended next implementation sequence (after the live smoke test)

1. Application configuration and composition root (load `.env`/config, build the provider registry and router from real settings).
2. Register the real `OllamaProvider` in that composition root behind the existing provider-neutral interface.
3. CLI-driven Personal Assistant vertical slice using the real local provider (still no vault write, still no hosted call).
4. Qwen work queue (`docs/handoffs/QWEN_WORK_QUEUE.md`) — bounded low-risk backlog tasks Qwen can pick up in its own worktree.
5. Librarian and Tutor contracts (prompt contracts and test fixtures first, per master plan section 5.4, before runtime activation).
6. Source catalog and RAG schemas — the first RAG code, strictly after ADR-0004's domain-neutral model shape is honored.

## 10. Known risks and unresolved decisions

- The exact installed Qwen tag on the Windows Ollama instance is unconfirmed — tomorrow's `GET /api/tags` call is the first source of truth; do not assume a tag name in configuration before that.
- No live round-trip through `OllamaProvider` has occurred yet; the adapter is validated only against `MockTransport` in the automated suite, so real-world timeout, streaming-disabled, and error-shape behavior from a live Ollama server is unverified.
- Hosted-provider budget policy and privacy-classification rules are implemented and unit-tested, but have never been exercised against a real hosted API — Phase 5 (hosted escalation) has not started.
- RAG is design-only (ADR-0004, `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`); no schema, dependency, or interface commitment has been made yet, so nothing there is at risk of drift, but nothing there is implemented either.
- No Obsidian vault access exists yet, so vault write-safety controls (path allowlist, atomic replacement, approval gate) remain unimplemented, not just unverified.

## 11. Commands for resuming work tomorrow

```bash
cd /home/ajsch/projects/personal-lms
git status --short --branch
git log --oneline --decorate -12
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

To exercise the Ollama adapter locally once Ollama is running on Windows and reachable:

```bash
uv sync --extra ollama
uv run pytest tests/unit/providers/ollama -v
```

## 12. Worktree state at closeout

`git status --short --branch` reported a clean working tree (`## claude/phase-0-foundation`, no output) immediately before this closeout began, matching the approved commit history in section 2 exactly. This closeout session added and committed only this document and `PACKAGE_MANIFEST.md`; `src/`, `tests/`, `pyproject.toml`, `uv.lock`, and all provider/routing/CrewAI/Ollama behavior were left untouched.
