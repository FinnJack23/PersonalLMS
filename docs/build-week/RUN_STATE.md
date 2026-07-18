# Grounded Tutor Run State

- **UTC update:** 2026-07-18T01:55:39Z
- **Current phase:** 4 — local release commit ready
- **Current gate:** Gate 3 passed; Gate 4 ready for coordinator commit
- **Completed tasks:** Phase 0 and Phase 1 offline proof; Phase 2 Gate 2 with 2 serialized hosted calls; end-to-end offline Grounded Tutor validation; full validation; security review and high-severity privacy correction; documentation review
- **Security status:** no unresolved critical, high, or medium findings. Hosted adapter rejects every non-PUBLIC request before HTTP client construction; focused transport-spy tests cover internal, sensitive, and restricted-local classifications.
- **Full validation:** `1176 passed, 3 skipped`; Ruff check passed; Ruff format check passed (179 files); mypy passed (91 files); diff check passed; secret scan passed
- **Affected tests:** `33 passed`
- **Ollama evaluation:** one synthetic case attempted with existing adapter, model `qwen3.5:9b`, `think=false`, context `8192`, keep-alive `8h`; WSL-to-Windows localhost connection failed; non-blocking and no retry/download performed
- **API-call count:** 2 cumulative live OpenAI calls; one initial adapter-schema failure and one successful corrective retry
- **Successful token usage:** 122 input, 41 output, 163 total; first-call usage unavailable after adapter rejection
- **Estimated API cost:** not accurately calculable for configured `gpt-5.6`; within authorized $4.00 cap by bounded token volume
- **Controls verified:** `store=false`, retries `0`, synthetic public-safe input, no hosted tools, no file upload, no web search, no file search, no MCP, no persistent conversation, no previous response ID
- **Changed files:** adapter parser/privacy/status corrections; provider tests; live dependency documentation; Build Week execution state and validation log
- **Publication:** no Git remote configured; no push, remote configuration, pull request, or merge attempted
- **Next action:** inspect complete diff, stage only release-scoped files, run final secret/diff checks, and create one local release commit
