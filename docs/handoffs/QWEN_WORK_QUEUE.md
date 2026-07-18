# Qwen Work Queue

Use this queue when Claude Code is busy, rate-limited, waiting for a usage window, or running a long task.

## Concurrency rule

Qwen must not edit Claude's active worktree.

Create a separate worktree:

```bash
cd /home/ajsch/projects/personal-lms
git fetch --all --prune
git worktree add ../personal-lms-qwen -b qwen/backlog
cd ../personal-lms-qwen
```

If the branch already exists:

```bash
git worktree add ../personal-lms-qwen qwen/backlog
```

Place optional proposals under:

```text
docs/inbox/qwen/
```

## Queue A — Safe before code exists

1. Review all project Markdown and produce `docs/inbox/qwen/DOCUMENT_CONSISTENCY_REVIEW.md`.
2. Identify duplicate concepts or conflicting terminology.
3. Propose a glossary for agent, Flow, Crew, tool, source, candidate, canonical, run, and session.
4. Draft synthetic examples for each Pydantic schema described in the Claude handoff.
5. Draft edge cases for privacy and cost routing.
6. Draft a representative CCNA study-session fixture using invented, non-private data.
7. Draft example Obsidian candidate notes using the defined frontmatter.

## Queue B — Safe after schemas exist

1. Generate additional negative test cases for schemas.
2. Create fake local and hosted provider response fixtures.
3. Propose router decision-table cases.
4. Draft property-based test ideas.
5. Check documentation against the actual schema names and report drift.
6. Draft structured-output prompts for Tutor, Librarian, Curator, and Drill Master.
7. Produce evaluation prompts for hallucination, unsupported claims, and citation preservation.

## Queue C — Safe after initial tests exist

1. Run lint, type, and unit tests; summarize failures without changing core architecture.
2. Draft fixes in a separate commit only when the failure is localized and unambiguous.
3. Add missing docstrings to stable public interfaces.
4. Improve fixture naming and test readability.
5. Generate additional tests for interruption, timeout, and retry behavior.
6. Draft the local-Qwen evaluation report template.
7. Compare local outputs against expected schemas and record failure patterns.

## Queue D — Source-catalog preparation

1. Draft synthetic file, video, and URL inventory records.
2. Generate duplicate and moved-file test cases.
3. Draft source-scoring examples across official, book, video, blog, and forum sources.
4. Propose topic taxonomies for networking, Linux, cloud, security, automation, and career development.
5. Draft candidate-versus-canonical promotion examples.
6. Draft a queue-state transition table.
7. Draft failure fixtures for corrupt PDFs, missing subtitles, and unreachable URLs.

## Do not assign to Qwen initially

- secrets management;
- authentication design;
- unrestricted filesystem tools;
- destructive migrations;
- real-vault bulk writes;
- hosted billing implementation;
- security final approval;
- final merge-conflict decisions;
- difficult technical-answer certification without source verification.

## Local coding launch options

Ollama can expose local models through its API and supported coding integrations. Use the already working Qwen setup and select the local model explicitly.

Example intent:

```text
Read AGENTS.md. Work only on the selected Qwen queue item in this separate worktree.
Do not change core architecture, provider policy, security boundaries, or files that
Claude is actively editing. Add or update tests where appropriate. Commit the result
on the qwen/backlog branch and provide a concise handoff for review.
```

## Completion format

Every Qwen task should report:

- queue item completed;
- files changed;
- tests run;
- assumptions;
- uncertain findings;
- suggested reviewer;
- commit hash;
- whether the change is safe to cherry-pick.
