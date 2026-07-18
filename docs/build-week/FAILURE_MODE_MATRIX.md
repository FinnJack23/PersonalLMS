# Build Week Failure-Mode Matrix

| Case | Result | Evidence / recovery |
|---|---|---|
| `OPENAI_API_KEY` absent | Verified safe stop | Phase 2 preflight stopped before any hosted request; offline demo remains available. |
| Hosted model unavailable | Covered by bounded adapter error path | Adapter maps request failures to a sanitized provider error; no retry loop. |
| Ollama unavailable | Verified | Existing Ollama tests cover connection failure mapping; live WSL attempt recorded as unavailable. |
| Invalid hosted response | Verified | Focused adapter tests cover raw Responses text extraction; empty output remains a controlled error. |
| Empty retrieval result | Verified | Grounding tests require an explicit insufficient-evidence gap. |
| Conflicting evidence | Verified | Grounding/source-verification tests preserve conflicts and prevent verified claims with conflicts. |
| Restricted-local-only request | Verified | Hosted transport-spy tests reject restricted, sensitive, and internal classifications before client construction. |
| SQLite unavailable/unwritable | Not independently exercised in this release lane | Keep as a follow-up hardening case; no claim of completed recovery behavior. |
| Duplicate enqueue under concurrency | Not independently exercised in this release lane | Existing idempotency contracts remain covered; concurrency stress test is deferred. |
| Interrupted demo followed by rerun | Partially verified | Demo uses temporary/in-memory runtime state and was rerun successfully after a bounded loopback check; interruption injection was not separately automated. |

No critical or high-severity failure remains in the validated vertical slice. The three unexercised cases are explicit follow-up work, not hidden pass claims.
