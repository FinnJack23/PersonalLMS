# Grounded Tutor Run State

- **UTC update:** 2026-07-18T02:07:19Z 2026-07-18T02:06:07Z
- **Current phase:** 10 — final local release checkpoint ready
- **Current gate:** Phases 5–10 complete; final local release committed
- **Release candidate checkpoint:** `4014fbd3b975d3e52712e6eda2de39b75997cdcc`
- **Completed tasks:** hosted adapter validation; privacy correction; router approval/budget gate; offline judge demo; judge guide and demo script; evaluation report; failure matrix; submission summary; release notes; independent release review
- **Security status:** no unresolved critical, high, or medium findings. Hosted construction now requires explicit router approval and a non-local, nonzero BudgetPolicy; non-PUBLIC requests are rejected before transport.
- **Final validation:** `1177 passed, 3 skipped`; affected tests `34 passed`; Ruff check passed; Ruff format check passed (179 files); mypy passed (91 files); diff check passed; secret scan passed
- **Demo:** loopback HTTP 200 with Grounded Tutor, E1, retrieval-gap, and SQLite markers
- **Ollama evaluation:** one synthetic case attempted with `qwen3.5:9b`, think=false, context 8192, keep-alive 8h; WSL could not reach Windows localhost; no pass rate claimed
- **Hosted API ledger:** 2 cumulative calls; no calls after Phase 2; successful call 122 input, 41 output, 163 total; no private content
- **Publication:** no Git remote configured; no push, remote configuration, pull request, merge, or release publication attempted
- **Active worktrees/branches:** current branch `codex/build-week-grounded-tutor`; no temporary worktree or branch created by this continuation
- **Final hardening commit:** `4b0d263057de07102e96693cb713d1496c08f875`
- **Next action:** publication requires external Git remote and authority; no further local release work is required
