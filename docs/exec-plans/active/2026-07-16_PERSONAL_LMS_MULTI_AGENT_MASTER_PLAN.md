# Personal LMS Multi-Agent Master Plan

**Status:** Active design and implementation plan  
**Date:** 2026-07-16  
**Canonical workspace:** `/home/ajsch/projects/personal-lms`  
**Target:** Begin production use before 2026-08-01  
**Primary operator:** Alan  
**Design lead:** ChatGPT  
**Primary implementation agent:** Claude Code  
**Secondary implementation/review agents:** Codex and local Qwen

---

## 1. Executive decision

The Personal LMS is not an MCP product. It is a **local-first multi-agent learning system** with a Personal Assistant as the primary interface and specialized agents behind it.

MCP may later be used as a tool-access protocol for selected systems, but it is not the product architecture, the user interface, or the knowledge model.

The system will begin with paid frontier-model APIs where their quality materially helps, while being designed from the first commit to transition toward local open-source models. Qwen through Ollama will perform routine work. Hosted APIs will be reserved for difficult reasoning, vision, long-context synthesis, and validated escalation cases.

The durable learning record lives in Obsidian as portable Markdown. The system must remain useful if CrewAI, a particular model provider, or the hosted APIs are removed.

### Product statement

> Personal LMS is a local-first learning operating system that coordinates specialized agents for assistance, tutoring, curation, retrieval, drilling, labs, critical review, coaching, project management, and career alignment while preserving durable knowledge in a portable Obsidian vault.

### Governing principles

1. **Local by default.**
2. **API by exception.**
3. **Deterministic whenever possible.**
4. **Obsidian contains curated knowledge, not every raw artifact.**
5. **Catalog everything; promote only the best sources.**
6. **Human approval controls destructive, expensive, or publishing actions.**
7. **Agent identity is independent of model provider.**
8. **A single Personal Assistant presents one coherent experience.**
9. **The system must improve study time rather than compete with it.**
10. **Production means a reliable vertical slice, not completion of the entire archive backlog.**

---

## 2. Desired user experience

Alan should normally interact with one Personal Assistant.

Example:

```text
Review what I studied today, identify the weak areas, drill me for 30 minutes,
and save the session to Obsidian.
```

The Personal Assistant should then coordinate only the specialists needed:

```text
Personal Assistant
  -> Librarian retrieves trusted source notes
  -> Progress Analyst identifies weak objectives
  -> Drill Master creates a bounded drill
  -> Tutor explains misses
  -> Coach selects the next action
  -> Vault Writer stores the reviewed session
```

Alan may also invoke a specialist directly:

```text
@Tutor Explain OSPF DR and BDR elections.
@Librarian Find my strongest IPv6 static-routing sources.
@DevilsAdvocate challenge this study plan.
@Proofreader revise this lab reflection.
@LabCoach create a CML longest-prefix-match exercise.
```

The interface should never require Alan to manually supervise a conversation among a dozen agents.

---

## 3. System boundaries

### 3.1 Agents

Agents provide bounded reasoning roles. They do not own the data store, credentials, filesystem, or application lifecycle.

### 3.2 Flows

CrewAI Flows control deterministic execution paths, state, routing, retries, approvals, cost limits, privacy restrictions, and writes.

### 3.3 Tools and services

Python tools and services perform deterministic work such as hashing, metadata extraction, validation, calculations, database access, scheduling, and atomic Markdown writes.

### 3.4 Knowledge store

Obsidian stores durable reviewed learning knowledge. Raw archives and runtime state live elsewhere.

### 3.5 Runtime state

SQLite initially stores source inventory, queues, run state, agent events, budgets, approvals, metrics, and audit logs.

### 3.6 Model providers

Model providers are interchangeable inference backends selected by capability and policy.

### 3.7 RAG knowledge plane

RAG is not an agent, not a model provider, and not a replacement for Obsidian. It is the knowledge plane: hybrid retrieval, grounding, and provenance, sitting between the agent plane and the data plane. It is domain-neutral — one reusable RAG platform serving many independently governed knowledge packs (CCNA first, CompTIA A+ next), not a mechanism hard-coded to a single certification. See `docs/decisions/ADR-0004_RAG_AS_THE_KNOWLEDGE_PLANE.md` and `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`.

---

## 4. Target architecture

```text
Alan
  |
  v
Personal LMS UI
  |
  v
Personal Assistant Flow
  |
  +--> RAG knowledge plane (hybrid retrieval and grounding, many knowledge packs)
  |
  +--> Tier 0 deterministic services
  |
  +--> Tier 1 local Qwen through Ollama
  |
  +--> Tier 2 hosted API escalation
  |
  +--> Specialist agents and bounded crews
  |
  +--> Obsidian vault adapter
  |
  +--> Source catalog and runtime database
  |
  +--> Optional lab/tool integrations
```

The system has five planes: orchestration (Flows, Personal Assistant), agent (specialists), knowledge (RAG), model (Tier 0/1/2), and data (Obsidian, raw archive, SQLite catalog, FTS5 index, vector index). See `docs/decisions/ADR-0004_RAG_AS_THE_KNOWLEDGE_PLANE.md`.

### Deployment target

```text
PH42
├── Personal LMS application
├── CrewAI runtime
├── FastAPI service
├── Local web interface
├── Ollama and Qwen
├── SQLite databases
├── Controlled Obsidian vault access
├── Source-processing workers
└── Optional Open WebUI access

Wilber
└── CML environment, accessed later through a restricted learning integration
```

OpenClaw may later provide external messaging channels, but it should sit outside the core application and call a restricted Personal LMS API. It must not independently own the vault or duplicate the agent system.

---

## 5. Agent organization

### 5.1 Layer 1: Orchestration

#### Personal Assistant

The Personal Assistant is the main interface and routing authority.

Responsibilities:

- understand the request;
- check the active course, certification, deadline, and session context;
- decide whether the request needs deterministic processing, local inference, hosted escalation, or a human decision;
- invoke the smallest useful set of specialists;
- combine results into one answer;
- request approval before expensive, destructive, private, or publishing actions;
- record approved results and next actions.

The Personal Assistant must be implemented primarily as a Flow, not as an unconstrained autonomous agent. Implementation is split across two layers: a framework-neutral application flow (`PersonalAssistantFlow`) that owns routing and provider execution and is independently testable without CrewAI installed, and a thin CrewAI orchestration adapter (`CrewAIPersonalAssistantFlow`) that runs it through CrewAI's `Flow` boundary and delegates every decision to it unchanged. Future specialist Crews are invoked only from controlled steps of the CrewAI adapter, never by embedding routing or provider logic in Crew/Task/Agent definitions. See ADR-0001.

### 5.2 Layer 2: Learning agents

#### Tutor

- explains concepts at the learner's current level;
- uses approved sources;
- checks understanding rather than only delivering an answer;
- adapts the explanation after errors;
- distinguishes memorization from conceptual mastery;
- maps learning to CCNA, D419, KCNA, LFCS, and later objectives;
- consumes RAG grounding bundles as primary evidence when one is available.

#### Drill Master

- creates active-recall questions;
- controls difficulty and coverage;
- avoids leaking answers before response;
- tracks misses and recurring traps;
- generates review queues from approved source material;
- supports multiple-choice, short answer, CLI completion, topology reasoning, and troubleshooting drills;
- draws question material from RAG grounding bundles, never from unreviewed candidate content.

#### Lab Coach

- guides Packet Tracer, NetLab, CML, Linux, cloud, and automation practice;
- uses exact lab details from approved sources;
- provides paste-ready commands without prompts;
- requires verification after major sections;
- records evidence, mistakes, and troubleshooting patterns.

#### Troubleshooter

- forms and tests hypotheses;
- requests only the evidence needed;
- distinguishes configuration, state, addressing, routing, platform, and syntax failures;
- escalates after bounded failed diagnostic attempts;
- stores reusable troubleshooting patterns.

#### Exam Strategist

- maps mastery to exam objectives;
- identifies coverage gaps;
- balances practice tests, labs, and targeted review;
- estimates readiness from evidence, not optimism;
- creates time-boxed review plans.

#### Reflection Guide

- converts sessions into lessons learned;
- records mistakes, corrections, memory cues, and next actions;
- supports weekly and milestone reflections;
- avoids repetitive reflection language.

#### Progress Analyst

- measures mastery, accuracy, confidence, latency, and recency;
- identifies weak objectives and trend changes;
- separates exposure from demonstrated ability;
- provides dashboards and study recommendations.

### 5.3 Layer 3: Knowledge and quality agents

#### Librarian

- searches the source catalog and curated vault;
- resolves titles, versions, and duplicates;
- retrieves the smallest useful source set;
- requests hybrid RAG retrieval (keyword + vector + metadata), optionally across knowledge packs (see `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`);
- provides provenance and source locations;
- never treats unreviewed archive material as trusted knowledge.

#### Curator

- scores PDFs, videos, URLs, notes, and courses;
- compares duplicates and revised editions;
- recommends promotion, rejection, supersession, or deferral;
- prevents the vault from becoming an unfiltered archive;
- prioritizes official, current, practical, and unique sources;
- approval is also the gate for RAG trusted-corpus membership, evaluated per knowledge pack.

#### Archivist

- maintains immutable source identity, hashes, locations, and provenance;
- records renamed and moved files;
- preserves original archives outside the vault;
- supports backup and recovery.

#### Source Verifier

- checks authority, date, version, internal consistency, and conflicts;
- identifies unsupported claims;
- recommends hosted escalation for difficult contradictions;
- marks confidence and unresolved uncertainty.

#### Proofreader

- improves grammar, clarity, organization, formatting, citations, and audience fit;
- preserves Alan's natural voice;
- does not silently change technical meaning.

#### Brainstormer

- produces diverse options, analogies, labs, projects, scripts, and portfolio ideas;
- avoids prematurely choosing one direction;
- records assumptions and feasibility questions.

#### Devil's Advocate

- challenges plans, claims, architectures, and exam answers;
- searches for failure modes, hidden costs, missing evidence, and alternative explanations;
- is intentionally skeptical but must provide actionable corrections.

#### Coach

- protects momentum and focus;
- identifies avoidance, overload, and unrealistic scheduling;
- proposes one clear next action;
- uses evidence without becoming punitive.

#### Project Manager

- converts projects into milestones, dependencies, tasks, risks, and acceptance criteria;
- protects the critical path;
- prevents scope expansion during production sprints.

#### Career Mentor

- connects coursework and projects to job requirements;
- identifies skill gaps;
- helps create interview stories and learning priorities;
- uses current job-market information only when retrieved and cited.

#### Portfolio Builder

- turns approved work into GitHub, LinkedIn, website, diagram, and report artifacts;
- strips secrets and private evidence;
- separates private source material from public proof of work.

### 5.4 V1 agent set

Before August 1, prioritize these six agents:

1. Personal Assistant
2. Tutor
3. Librarian
4. Curator
5. Drill Master
6. Lab Coach

The Coach can be added early if the vertical slice remains stable. Other roles may begin as prompt contracts and test fixtures without full runtime activation.

---

## 6. Model-tier architecture

### 6.1 Tier 0: deterministic services

Use Python rather than an LLM for:

- file hashing and duplicate identity;
- PDF/video metadata;
- URL normalization;
- YAML and Markdown validation;
- subnet and binary calculations;
- spaced-review dates;
- progress metrics;
- path validation;
- database queries;
- template rendering;
- budget arithmetic;
- source status checks;
- schema validation.

Tier 0 is faster, cheaper, auditable, and testable.

### 6.2 Tier 1: local Qwen through Ollama

Qwen is the default model for routine language and reasoning tasks:

- intent classification;
- routing suggestions;
- catalog tagging;
- source summaries;
- title and topic extraction;
- simple tutoring;
- ordinary proofreading;
- first-pass flashcards;
- first-pass quiz generation;
- note linking suggestions;
- transcript cleanup;
- daily and weekly reflection drafts;
- test-fixture generation;
- documentation consistency checks.

The local model endpoint must be behind a provider-neutral adapter. The rest of the application should see a capability profile such as `local_fast`, `local_general`, or `local_reasoning`, not an Ollama URL or Qwen model name.

### 6.3 Tier 2: hosted APIs

Use hosted APIs for tasks that materially need frontier capabilities:

- difficult networking or code troubleshooting;
- ambiguous screenshots and diagrams;
- complex visual exhibits;
- large multi-document synthesis;
- long-context comparison;
- high-stakes source conflicts;
- final review of public or formal deliverables;
- security and architecture review;
- repeated local failure;
- user-requested premium analysis.

### 6.4 Escalation policy

The model router should evaluate:

- deterministic validation results;
- source availability and trust level;
- RAG grounding-bundle availability and quality;
- task complexity;
- privacy classification;
- local context-window fit;
- vision requirement;
- repeated failures;
- disagreement between local passes;
- cost policy;
- explicit user preference.

Do not rely only on a model's self-reported confidence. RAG retrieval always runs before hosted escalation is evaluated — hosted calls see a grounding bundle, never a bare prompt (see `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`).

Example conditions:

```yaml
escalation:
  local_attempts_max: 2
  require_hosted_when:
    - complex_vision
    - unresolved_source_conflict
    - local_schema_validation_failed
    - local_context_limit_exceeded
    - publication_final_review
  never_hosted_when:
    - contains_credentials
    - contains_private_financial_data
    - contains_private_health_data
    - contains_unredacted_private_documents
```

### 6.5 Budget controls

Required features:

- cost estimate before hosted calls when available;
- per-agent and per-workflow usage logs;
- daily and monthly warning thresholds;
- hard stop limit;
- explicit approval for large or batch calls;
- no automatic recharge;
- local fallback when budget is exhausted;
- audit record for every hosted escalation.

Initial policy example:

```yaml
budgets:
  daily_api_limit_usd: 3.00
  monthly_api_limit_usd: 40.00
  warn_at_percent: 70
  automatic_single_call_limit_usd: 0.15
  approval_single_call_limit_usd: 0.50
```

These are initial configuration values, not permanent decisions.

---

## 7. Qwen's development role

Qwen is not only a runtime model. It should contribute useful low-risk development work whenever Claude is unavailable, rate-limited, running a long task, or waiting for a new usage window.

### Good Qwen development tasks

- draft test fixtures from approved schemas;
- generate synthetic catalog records;
- review documentation for contradictions;
- create initial agent-prompt variants;
- classify backlog items;
- draft docstrings and comments;
- generate example Markdown notes;
- produce local evaluation prompts;
- summarize test failures;
- propose edge cases;
- draft migration checklists;
- improve non-critical documentation;
- run local lint and test commands and summarize results;
- compare generated outputs against schemas.

### Tasks Qwen should not own initially

- authentication architecture;
- destructive file operations;
- secrets management;
- API billing policy;
- final security decisions;
- unreviewed mass vault writes;
- merge conflict resolution in Claude's active files;
- production database migrations;
- final factual review of difficult networking answers.

### Parallel-work rule

Qwen must use a separate Git worktree for active edits:

```bash
cd /home/ajsch/projects/personal-lms
git worktree add ../personal-lms-qwen -b qwen/backlog
```

The root worktree may remain open in Codex for inspection while Claude works, but only one writer is permitted per worktree.

Qwen should deposit optional proposals into:

```text
docs/inbox/qwen/
```

Claude or Alan reviews and promotes those proposals.

---

## 8. Obsidian and archive architecture

### 8.1 Scale

The collection is expected to include approximately:

- 1,000 PDFs;
- many hours of video;
- thousands of URLs;
- screenshots, diagrams, CLI output, question banks, course notes, and lab records.

The system must not dump all extracted material into the curated vault.

### 8.2 Three-library model

#### Raw archive

Original files and URL lists. Immutable or treated as read-only.

#### Candidate library

Machine-extracted text, transcripts, metadata, classifications, summaries, duplicate groups, and candidate notes.

#### Curated Obsidian vault

Human-approved canonical knowledge, source cards, labs, question banks, weak-area notes, study sessions, and portfolio artifacts.

### 8.3 Core funnel

```text
Raw archive
  -> inventory and stable identity
  -> extraction and normalization
  -> duplicate detection
  -> classification and scoring
  -> review queue
  -> approved promotion
  -> Obsidian concepts and learning artifacts
  -> RAG chunk/embed/index, per knowledge pack (derived, rebuildable)
  -> drills, labs, reviews, and progress tracking
```

Indexing is downstream of promotion and never a substitute for it — see `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`.

### 8.4 Source-quality scoring

Suggested weighted criteria:

| Criterion | Weight |
|---|---:|
| Authority and accuracy | 25% |
| Current course/certification relevance | 25% |
| Instructional clarity | 15% |
| Currency | 10% |
| Uniqueness | 10% |
| Practical usefulness | 10% |
| Provenance and rights clarity | 5% |

Promotion remains a human-controlled decision.

### 8.5 Vault rules

- One controlled writer service.
- Atomic file replacement.
- Configured allowed roots.
- Stable frontmatter schemas.
- Provenance on every generated note.
- Page, timestamp, or source-location references where possible.
- No credentials or private secrets.
- No auto-promotion from raw extraction to canonical concepts.
- Preserve Alan's manual edits.
- Generated notes enter an inbox or candidate area first.

### 8.6 Initial vault structure

```text
Personal-LMS/
├── 00-System/
├── 01-Inbox/
├── 02-Sources/
├── 03-Concepts/
├── 04-Courses/
├── 05-Certifications/
├── 06-Labs/
├── 07-Question-Banks/
├── 08-Drills/
├── 09-Weak-Areas/
├── 10-Projects/
├── 11-Portfolio/
└── 99-Archive/
```

### 8.7 Source catalog record

```yaml
asset_id: src-00001234
source_type: pdf
title: Routing Concepts Module 14
original_location: archive/networking/module14.pdf
content_hash: sha256-value
file_size: 18420392
page_count: 56
course: D419
certification: CCNA
topics:
  - routing-table
  - longest-prefix-match
status: cataloged
extraction_status: complete
authority_score: 5
relevance_score: 5
quality_score: 4
currency_score: 3
uniqueness_score: 4
promotion_status: candidate
canonical_note:
```

---

## 9. Repository architecture

```text
personal-lms/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── uv.lock
├── src/personal_lms/
│   ├── agents/
│   ├── crews/
│   ├── flows/
│   ├── tools/
│   ├── models/
│   ├── policies/
│   ├── schemas/
│   ├── catalog/
│   ├── rag/
│   ├── vault/
│   ├── api/
│   └── observability/
├── config/
│   ├── agents.example.yaml
│   ├── models.example.yaml
│   ├── permissions.example.yaml
│   └── vault.example.yaml
├── prompts/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── evaluation/
│   └── fixtures/
├── docs/
│   ├── decisions/
│   ├── exec-plans/
│   ├── handoffs/
│   ├── product-specs/
│   ├── operations/
│   └── inbox/
├── scripts/
├── docker/
└── docker-compose.yml
```

### Provider portability

Agents request capabilities, not vendors:

```yaml
agents:
  tutor:
    model_profile: learning_reasoning
  librarian:
    model_profile: local_general
  curator:
    model_profile: local_general
```

```yaml
model_profiles:
  local_fast:
    primary: ollama_qwen_small
  local_general:
    primary: ollama_qwen_default
  learning_reasoning:
    primary: ollama_qwen_reasoning
    escalation: hosted_reasoning
  vision:
    primary: hosted_vision
```

---

## 10. Interaction surfaces

### V1

A local browser interface or simple API-backed chat on PH42 provides:

- Personal Assistant conversation;
- explicit specialist selection;
- current model tier indicator;
- source citations and provenance;
- approval prompts;
- study-session results;
- link to the created Obsidian note.

### V1.1

Add:

- study dashboard;
- weak-area dashboard;
- source review queue;
- API cost dashboard;
- agent execution trace;
- current queue and failed jobs.

### Later

- Open WebUI integration;
- OpenClaw external messaging gateway;
- mobile access;
- voice interaction;
- calendar and email triggers;
- controlled CML lab launch and evidence retrieval.

---

## 11. Phased implementation plan

## Phase 0 — Architecture freeze and shared workspace

### Objective

Create a stable repository, shared instructions, development boundaries, and decisions before implementation expands.

### Deliverables

- canonical repository at `/home/ajsch/projects/personal-lms`;
- Git initialized with protected main branch policy documented;
- `AGENTS.md` and `CLAUDE.md` at root;
- project documentation structure;
- initial architecture decisions;
- Python/uv tooling scaffold;
- CI-ready local checks;
- no real model calls;
- no real vault writes.

### Exit criteria

- both Claude Code and Codex can open the same root;
- Qwen worktree procedure is documented;
- tests run from one command;
- project scope is frozen for the first vertical slice.

## Phase 1 — Domain contracts and deterministic foundation

### Objective

Define the stable interfaces before implementing autonomous behavior.

### Deliverables

- Pydantic schemas for agent request, response, source citation, task, run state, approval, budget event, and vault note;
- provider-neutral model interface;
- fake model adapters;
- model router policy engine;
- privacy classifier interface;
- budget-policy interface;
- structured logs with secret redaction;
- SQLite schema for runs, tasks, approvals, and usage;
- unit and contract tests.

### Exit criteria

- routing decisions can be tested without an LLM;
- fake local and hosted providers demonstrate escalation behavior;
- private data is blocked from hosted routing in tests.

## Phase 2 — Obsidian safe-access layer

### Objective

Create a controlled knowledge boundary.

### Deliverables

- read-only vault search and note retrieval;
- path allowlist;
- frontmatter parser and validator;
- atomic candidate-note writer;
- human approval gate;
- write-audit records;
- backup-before-write option;
- test vault fixtures;
- no raw bulk ingestion yet.

### Exit criteria

- no path traversal;
- no overwrite without policy;
- generated candidate note can be written, validated, and rolled back;
- manual content is preserved.

## Phase 3 — First vertical study-session Flow

### Objective

Deliver one usable end-to-end learning workflow.

### Flow

```text
User request
  -> deterministic request classification
  -> Librarian retrieves approved notes
  -> Tutor explains
  -> Drill Master creates a short drill
  -> Tutor evaluates response
  -> Coach creates next action
  -> approval
  -> session note written to Obsidian
```

### Deliverables

- Personal Assistant Flow;
- initial Tutor, Librarian, Drill Master, and Coach contracts;
- fake-model tests;
- session state persistence;
- structured final response;
- candidate session-note template;
- evaluation fixtures using CCNA topics.

### Exit criteria

- one complete session runs with fake adapters;
- state resumes after interruption;
- no hosted call is required;
- session produces a valid reviewable Markdown note.

## Phase 4 — Local Qwen integration

### Objective

Replace fake local inference with Ollama/Qwen.

### Deliverables

- Ollama health and model discovery;
- OpenAI-compatible or native Ollama adapter;
- timeout, retry, cancellation, and token metrics;
- local model profiles;
- local prompt and structured-output evaluation suite;
- local-only study-session smoke test;
- no paid API requirement.

### Exit criteria

- Qwen completes routine Tutor, Librarian, Curator, and Drill Master tasks;
- output schemas validate at an acceptable rate;
- failures route to correction or escalation policy;
- local token and latency metrics are recorded.

## Phase 5 — Hosted API escalation

### Objective

Add frontier-model quality without making APIs the default.

### Deliverables

- hosted-provider adapter interface;
- at least one configured provider through environment variables;
- redaction and minimal-context packaging;
- cost estimation and usage recording;
- approval threshold;
- daily/monthly hard limits;
- escalation audit record;
- hosted-provider contract tests using mocks;
- one explicitly approved live test.

### Exit criteria

- ordinary tasks remain local;
- restricted data never routes to hosted providers in tests;
- budget limits stop calls;
- user can force local-only mode.

## Phase 6 — Source catalog and curation

### Objective

Create the foundation for the 1,000 PDFs, videos, and thousands of URLs.

### Deliverables

- read-only filesystem inventory;
- stable asset IDs and SHA-256 hashes;
- URL importer and canonicalization;
- file, video, and URL schemas;
- exact duplicate groups;
- extraction job queue;
- Curator scoring and review states;
- source card template;
- candidate-versus-canonical separation;
- knowledge-pack registration for the sources being cataloged (see `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`).

### Exit criteria

- full inventory can resume after interruption;
- no originals are modified;
- duplicates are grouped;
- one approved source promotes to Obsidian with provenance.

## Phase 7 — PDF, video, and URL processing

### Objective

Build prioritized processing pipelines without attempting to finish the full backlog.

### PDF requirements

- embedded text first;
- selective OCR only when extraction fails;
- page-level provenance;
- diagram/table/exhibit preservation where meaningful;
- chunking and candidate notes.

### Video requirements

- existing captions first;
- transcription only when needed;
- timestamps and chapters;
- selected keyframes when useful;
- no automatic full raw transcript promotion;
- promoted, chunked output becomes eligible for RAG embedding and indexing within its knowledge pack once curator-approved.

### URL requirements

- tracking-parameter removal;
- canonical URLs;
- rate-limited status checks;
- source date and domain;
- duplicate content groups;
- broken and superseded statuses.

### Exit criteria

A representative test batch succeeds:

- 25 varied PDFs;
- at least 2 hours of video;
- at least 100 URLs;
- duplicate, corrupt, unreachable, interruption, and retry cases.

## Phase 8 — Labs, question mode, and CML integration

### Objective

Connect learning knowledge to practical execution.

### Deliverables

- Lab Coach workflow;
- Packet Tracer/NetLab evidence templates;
- question-bank schema and import;
- explicit exhibit handling;
- weak-area updates;
- read-only CML lab catalog lookup;
- lab session notes and verification commands.

### Exit criteria

- a source concept can create a drill and a lab exercise;
- evidence returns to the correct Obsidian note;
- CML integration remains read-only initially.

## Phase 9 — Production hardening

### Objective

Make the system dependable enough for daily study.

### Deliverables

- backup and tested restore;
- service startup after reboot;
- health checks;
- structured logs;
- failed-job queue;
- documented upgrade and rollback;
- local-only emergency mode;
- API budget dashboard;
- agent and model evaluation report;
- production runbook.

### Exit criteria

- representative vertical slice runs after reboot;
- backup restore works to a clean location;
- production-blocking defects are closed;
- known limitations are documented.

## Phase 10 — Expansion after August 1

Potential additions:

- Source Verifier, Troubleshooter, Exam Strategist, Career Mentor, and Portfolio Builder runtimes;
- OpenClaw gateway;
- voice interaction;
- Google Calendar/Gmail triggers;
- Quizlet export;
- advanced local retrieval;
- local embedding evaluation;
- more capable local models;
- controlled write-enabled CML operations;
- remote deployment and authentication.

---

## 12. Production definition for August 1

The August 1 goal is a **production-capable vertical slice**, not a finished enterprise platform.

### Required

- canonical repository and documentation;
- working local environment;
- Personal Assistant interface;
- Tutor, Librarian, Curator, Drill Master, Lab Coach contracts;
- at least one complete study-session Flow;
- Qwen/Ollama local inference;
- model router with hosted escalation interface and hard limits;
- safe Obsidian candidate-note write;
- initial source catalog;
- tests, logs, backup, restore, and runbook;
- local-only mode.

### Optional before August 1

- polished dashboard;
- full video transcription;
- OpenClaw;
- all planned agents;
- broad CML operations;
- complete archive processing;
- cloud deployment.

### Explicitly not required

- processing every PDF, video, and URL;
- fully autonomous agents;
- hosted CrewAI platform;
- vendor-specific lock-in;
- unrestricted filesystem or shell access.

---

## 13. Development operating model

### Design and review

ChatGPT provides:

- system architecture;
- phase plans;
- agent contracts;
- data and safety schemas;
- test requirements;
- Claude and Qwen steering;
- design review and adversarial review.

### Primary implementation

Claude Code performs the main implementation in focused commits.

### Codex role

Codex opens the canonical folder for inspection, review, targeted fixes, and later parallel work through a separate worktree.

### Qwen role

Qwen performs local low-risk backlog tasks in a separate worktree, especially during Claude limits or waiting periods.

### Human role

Alan controls:

- product decisions;
- source promotion;
- model budget;
- privacy exceptions;
- acceptance of learning behavior;
- final merge and production release.

### Branch strategy

```text
main
├── claude/<feature>
├── codex/<review-or-fix>
└── qwen/<bounded-backlog-task>
```

No two agents edit the same branch or worktree concurrently.

---

## 14. Testing strategy

### Unit tests

- schemas;
- router decisions;
- budget checks;
- privacy rules;
- path validation;
- Markdown generation;
- source scoring;
- deterministic calculators.

### Contract tests

- every model adapter;
- every vault tool;
- every agent structured output;
- every database repository.

### Integration tests

- Flow with fake models;
- Flow with local Qwen;
- candidate vault write;
- interruption and resume;
- failure and retry;
- local-to-hosted escalation with mocked hosted calls.

### Evaluation tests

- known CCNA questions and explanations;
- source-grounding accuracy;
- answer-choice preservation;
- CLI syntax correctness;
- hallucination and unsupported-claim rate;
- local-versus-hosted comparison;
- escalation precision;
- retrieval precision/recall against a curated corpus, per knowledge pack;
- grounding faithfulness — generated claims traceable to their grounding bundle;
- base Qwen vs. Qwen+RAG vs. LoRA Qwen vs. LoRA Qwen+RAG comparison (portfolio evaluation suite; see section 20).

### Security tests

- path traversal;
- prompt injection from source documents;
- secret redaction;
- prohibited hosted transmission;
- budget bypass;
- unauthorized overwrite and deletion;
- malicious Markdown/frontmatter.

---

## 15. Risks and controls

| Risk | Control |
|---|---|
| Too many agents increase cost and confusion | Personal Assistant routes to the minimum set; many roles begin as contracts only |
| Vault becomes an unfiltered dump | candidate library and human promotion gate |
| API cost expands silently | hard budgets, audit logs, local-first routing |
| Private material reaches hosted APIs | classification, redaction, deny rules, explicit approval |
| Claude, Codex, and Qwen overwrite each other | one writer per worktree; Git worktrees for parallel work |
| Qwen produces plausible but wrong technical material | approved-source grounding, deterministic checks, verifier, escalation |
| August build competes with D419 study | vertical slice only; daily production freeze and scope gate |
| CrewAI framework changes | adapters, pinned dependencies, provider-neutral domain layer |
| Obsidian sync conflicts | single writer, atomic writes, candidate folder |
| Source archive overwhelms processing | queue, prioritization, resumability, representative acceptance batch |
| Local model is too slow or weak | capability profiles, smaller routing model, optional larger local model, hosted escalation |
| Prompt injection in PDFs/web content | treat sources as data, isolate instructions, tool permissions, output validation |
| RAG index drifts from the curated vault | index is derived and fully rebuildable from Obsidian plus the SQLite catalog; never treated as source of truth |
| Hosted escalation leaks restricted material via retrieved evidence | privacy classification filters the grounding bundle before hosted eligibility is checked; `restricted_local_only` chunks are dropped, not redacted |
| A single knowledge pack's assumptions leak into the shared RAG platform | generic source/chunk/citation models with no domain-specific required fields; new domains added by registering a knowledge pack, not by changing core interfaces |

---

## 16. Initial decision register

### ADR-0001

Use CrewAI open-source framework with Flows as the deterministic backbone and bounded Crews for collaborative reasoning.

### ADR-0002

Agent identities are provider-neutral. Models are selected through capability profiles and policy.

### ADR-0003

Obsidian is the durable source of reviewed learning knowledge. Runtime state and archive catalogs stay outside the vault.

### ADR-0004

RAG is the knowledge plane — hybrid retrieval, grounding, and provenance — distinct from the agent, orchestration, model, and data planes, and never a replacement for Obsidian. RAG is domain-neutral: one reusable RAG platform serves many independently governed knowledge packs, with CCNA as the first production vertical and CompTIA A+ as the next planned pack. See `docs/decisions/ADR-0004_RAG_AS_THE_KNOWLEDGE_PLANE.md`.

### ADR-0005

Use SQLite for initial catalogs and state. Reassess only after measured scaling limits.

### ADR-0006

The canonical project root is `/home/ajsch/projects/personal-lms`. Claude and Codex may view the same root; parallel writers use worktrees.

### ADR-0007

Use Tier 0 deterministic Python, Tier 1 local Qwen/Ollama, and Tier 2 hosted API escalation.

---

## 17. Immediate action for tonight

Tonight's Claude run should **not** attempt the full multi-agent platform.

It should establish:

1. repository and Python tooling;
2. concise shared instructions;
3. domain schemas;
4. provider-neutral model interfaces;
5. fake local and hosted adapters;
6. deterministic model-routing policy;
7. initial tests;
8. a skeletal Personal Assistant Flow with no real vault write and no paid call;
9. an Ollama health-check or adapter stub if time remains;
10. a clean handoff.

Use `docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md` as the exact execution document.

---

## 18. Official design references

Validated against official project documentation available on 2026-07-16:

- CrewAI documentation: https://docs.crewai.com/
- CrewAI agents and Flows concepts: https://docs.crewai.com/core-concepts/Agents
- CrewAI source and MIT license: https://github.com/crewAIInc/crewAI
- Ollama API: https://docs.ollama.com/api/introduction
- Ollama OpenAI compatibility: https://docs.ollama.com/api/openai-compatibility
- Ollama integration overview: https://docs.ollama.com/integrations
- Qwen official Qwen3 information: https://qwenlm.github.io/blog/qwen3/
- OpenAI Codex and `AGENTS.md`: https://openai.com/index/introducing-codex/
- OpenAI repository-knowledge guidance: https://openai.com/index/harness-engineering/

---

## 19. Final success condition

The project succeeds when Alan can open one local interface, ask for a learning outcome, receive a source-grounded response from the appropriate agents, drill the concept, perform a practical lab when relevant, and store a reviewed, portable record in Obsidian—mostly using local Qwen, with paid APIs invoked only when their added quality is justified.

---

## 20. Portfolio objective: CCNA Evidence-Checked Tutor

Design and implement a local-first CCNA Evidence-Checked Tutor that uses approved RAG sources, deterministic networking validation, local Qwen generation, citation verification, weak-area tracking, and controlled hosted API escalation. Later compare base Qwen, Qwen with RAG, LoRA Qwen, and LoRA Qwen with RAG using a reproducible evaluation suite.

This is the first reference application built on the domain-neutral RAG platform (see section 3.7, `docs/decisions/ADR-0004_RAG_AS_THE_KNOWLEDGE_PLANE.md`, and `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`) — **one reusable RAG platform, many independently governed knowledge domains.** CCNA is the first production vertical and portfolio demonstration, not a hard-coded limitation. CompTIA A+ Core 1/Core 2 is the next planned knowledge pack, chosen specifically because it is structurally different from networking (hardware/OS/troubleshooting rather than protocols) — a real test of whether new domains can be added by registering a knowledge pack rather than by changing core retrieval, grounding, or citation interfaces.

Fine-tuning (LoRA) is a later behavioral-optimization step layered on top of RAG, not a substitute for it. The evaluation suite exists to make that distinction measurable rather than asserted: citation accuracy and hallucination rate should improve from RAG, and separately from LoRA, in ways that can be shown, not just claimed.
