# Grounded Tutor Validation Log

All entries are sanitized. No credentials, authorization headers, private
environment values, account identifiers, or private source content are stored.

## 2026-07-18T01:10:59Z — preflight

- Branch: `codex/build-week-grounded-tutor`
- Commit: `a8e19d7`
- Worktree: clean
- OpenAI SDK: available, version `2.45.0`
- Live API calls: `0`
- Remote: none configured; publication not attempted

## 2026-07-18T01:14:00Z — Phase 1 offline gate

- Privacy sentinel: `BUILD_WEEK_RESTRICTED_SENTINEL_DO_NOT_SEND` was not
  transmitted; hosted client constructions `0`; POST calls `0`.
- Offline tests: targeted Build Week tests `3 passed`; provider control tests
  `3 passed`.
- Offline result: grounded lesson, E1 citation, expected administrative-distance
  gap, exactly 3 questions, SQLite persistence, and cleaned temporary storage.
- Provider correction: explicit `store=false`, retries fixed at `0`, no tools or
  previous response state; transport-spy tests pass.
- Live OpenAI calls: `0`.
- Publication: not attempted; no remote configured.

## 2026-07-18T01:16:00Z — Phase 2 hard stop

- API-key preflight checked only whether `OPENAI_API_KEY` was non-empty.
- Result: unavailable in this process.
- Live OpenAI calls: `0`; no charge incurred by this run.
- No live request, push, or pull request was attempted.

## 2026-07-18T01:46:05Z — Phase 2 Gate 2 live validation

- Purpose: one synthetic public-safe grounded IPv4 routing explanation.
- Provider: `openai-responses`; configured model: `gpt-5.6`.
- Call 1: failed adapter schema parsing (`output_text` was not present in the raw Responses payload); no valid adapter result.
- Narrow correction: adapter now extracts `output_text` from Responses `output[].content[]` items with type `output_text`.
- Call 2: passed acceptance; grounded answer included `/32`, the router own interface address, the connected-route distinction, and citation `E1`.
- Successful response schema fields: request ID, capability profile, local flag, output text, input tokens, output tokens, latency, finish reason.
- Successful token usage: 122 input, 41 output, 163 total. Call 1 usage unavailable because the adapter rejected its response before returning a result.
- Live OpenAI calls: `2` cumulative; one initial request and one narrowly justified corrective retry.
- Controls: `store=false`; SDK retries `0`; no tools, files, web search, MCP, persistence, or previous response state.
- Input/output: synthetic public-safe evidence only; response excerpt and content hash were captured outside the repository output and are not stored here.
- Estimated cost: not accurately calculable for configured `gpt-5.6`; token volume is within the authorized $4.00 cumulative cap.
- Gate 2: passed.

## 2026-07-18T01:55:39Z — Phase 3/4 release validation

- Full validation: `uv run pytest` — `1176 passed, 3 skipped`; `uv run ruff check .` — passed; `uv run ruff format --check .` — 179 files formatted; `uv run mypy src` — 91 source files, no issues; `git diff --check` — passed.
- Affected tests after final correction: `33 passed` (`test_openai_responses.py`, `test_build_week.py`, `test_evidence_checked.py`).
- Secret scan: passed; no key-shaped secrets or private-key blocks found in repository content. `.env.local` remained ignored, untracked, and unstaged.
- Security review: initial high finding (INTERNAL/SENSITIVE hosted transport bypass) corrected by rejecting every non-PUBLIC request before HTTP client construction; focused transport-spy coverage now covers INTERNAL, SENSITIVE, and RESTRICTED_LOCAL_ONLY. Medium findings corrected for live dependency setup, adapter error wording, completion-status reporting, and stale publication instructions.
- Local Ollama lane: one synthetic case attempted through the existing `OllamaProvider` with `qwen3.5:9b`, `think=false`, 8192 context, and `keep_alive=8h`; WSL could not connect to the reported Windows `localhost:11434`, so pass rate was unavailable. This local-only limitation did not block the hosted adapter release; no model download or repeated request was attempted.
- Synthetic-data statement: hosted request used only synthetic public-safe IPv4 routing evidence; no production source material, Obsidian vault, or private archive was accessed.
- Hosted API ledger: 2 cumulative calls; successful call used 122 input and 41 output tokens (163 total); first call usage unavailable after adapter schema rejection; estimated cost not accurately calculable for configured `gpt-5.6` but within the authorized $4.00 cap by bounded token volume; 10 call slots remain.
- No remote is configured; no push, remote configuration, pull request, or merge was attempted.
- Gate 3: passed. Gate 4: ready for local commit.

## 2026-07-18T02:06:07Z — Phases 5–10 post-release package

- Phase 5 judge experience: offline command verified with `uv sync`/`uv run personal-lms build-week-demo`; loopback HTTP 200 and Grounded Tutor, E1, retrieval-gap, and SQLite markers present. Judge guide and five-minute script added.
- Phase 6 evaluation evidence: deterministic focused slice `34 passed` after hardening; local Ollama case attempted with `qwen3.5:9b`, `think=false`, 8192 context, and 8h keep-alive but WSL could not reach Windows localhost; no pass rate claimed.
- Phase 7 failure matrix: created with verified, partial, and unexercised cases labeled honestly.
- Phase 8 independent review: one high finding (direct hosted adapter lacked explicit router approval/budget boundary) corrected; medium stale-checklist issue corrected; provider now requires explicit router approval and a non-local, nonzero budget policy before hosted construction.
- Final post-hardening validation: `uv run pytest` — `1177 passed, 3 skipped`; Ruff check passed; formatting check passed (179 files); mypy passed (91 files); diff check passed; secret scan passed.
- No new hosted API calls were made after the 2-call Phase 2 ledger. No remote, push, PR, merge, or release publication was attempted.
