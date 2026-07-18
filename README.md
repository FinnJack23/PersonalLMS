# Personal LMS

Local-first, multi-agent learning system centered on an Obsidian vault.

## Product statement

Personal LMS coordinates specialized agents for tutoring, source curation, drilling, labs, proofreading, critical review, coaching, project management, and career alignment. Durable learning knowledge remains portable in Markdown inside Obsidian. Runtime state, inventories, queues, and metrics live in local databases and can be rebuilt.

## Canonical workspace

Use this WSL path as the shared project root:

```text
/home/ajsch/projects/personal-lms
```

Open this same root folder in Claude Code and Codex for shared visibility. Do not let two coding agents write to the same worktree at the same time. Use Git worktrees for parallel implementation.

## Start here

1. Read `AGENTS.md` when using Codex.
2. Read `CLAUDE.md` when using Claude Code.
3. Read the master plan:
   `docs/exec-plans/active/2026-07-16_PERSONAL_LMS_MULTI_AGENT_MASTER_PLAN.md`
4. For tonight's Claude session, use:
   `docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md`
5. When Claude is unavailable or rate-limited, use:
   `docs/handoffs/QWEN_WORK_QUEUE.md`
6. For the RAG knowledge plane, read:
   `docs/product-specs/RAG_KNOWLEDGE_PLANE.md` and
   `docs/decisions/ADR-0004_RAG_AS_THE_KNOWLEDGE_PLANE.md`

## Core decisions

- CrewAI open-source framework is the initial orchestration foundation.
- CrewAI Flows control deterministic routing, permissions, state, approvals, and writes.
- Crews provide bounded specialist collaboration only where it adds value.
- CrewAI is an optional orchestration adapter, not a core dependency: `uv sync` installs a lightweight core (domain schemas, providers, routing policy, and the framework-neutral `PersonalAssistantFlow`); `uv sync --extra crewai` additionally installs CrewAI for the orchestration adapter. See ADR-0001.
- Qwen through Ollama is the default model tier for routine work.
- Hosted APIs are escalation resources, not the default.
- Python services handle deterministic work without an LLM.
- Obsidian is the durable learning knowledge store.
- RAG is the knowledge plane: one reusable retrieval platform serving many independently governed knowledge packs (CCNA first, A+ next), never a replacement for Obsidian.
- The RAG keyword and vector indexes are derived and fully rebuildable; only curator-approved sources enter any knowledge pack's trusted corpus.
- SQLite is the initial source catalog and runtime state store.
- Raw PDFs, videos, and archives remain outside the curated vault.
- Catalog everything; curate selectively; promote only the best sources.

## Production milestone

The first production milestone is a usable vertical slice, not the complete processing of the entire archive. It must support:

- one Personal Assistant interface;
- a Tutor, Librarian, Curator, Drill Master, Lab Coach, and Coach;
- local Qwen routing with API escalation controls;
- safe Obsidian read/write through controlled templates;
- a source catalog for files, videos, and URLs;
- one complete study-session workflow;
- audit logs, tests, backups, and recovery instructions.

## OpenAI Build Week: Grounded Tutor

Grounded Tutor is a runnable Education-track vertical slice: a redacted
Source Readiness manifest is imported, approved evidence is retrieved, a cited
micro-lesson and exactly three evidence-backed questions are produced, and
review results are stored in local SQLite. The fixture is synthetic and never
reads Alan's archive or Obsidian vault.

```bash
uv sync
uv run personal-lms build-week-demo
```

Open `http://127.0.0.1:8000`. The page is labeled offline simulated mode. For
an approved live demo, install the HTTP client extra with `uv sync --extra
openai-live`, then set `OPENAI_API_KEY` and optionally
`PERSONAL_LMS_BUILD_WEEK_MODEL=gpt-5.6`; tests run with `uv run pytest`.
