# Grounded Tutor Supervised Execution Plan

## Mission

Complete the smallest safe live GPT-5.6 Responses validation, prove privacy
blocking before transport, preserve sanitized evidence, run final checks, and
prepare a local release package. Publication requires a separately configured remote.

## Phases and lanes

0. Coordinator preflight and control files.
1. Three read-only lanes: privacy boundary, offline slice, and release readiness.
2. Serial live validation: at most two calls, target one, synthetic public-safe
   prompt, `store=false`, no retries/tools/persistence.
3. Three post-live lanes: complete validation, security review, and documentation
   review. Writing agents use isolated worktrees.
4. Coordinator integration, final verification, and sanitized commit.
5. Prepare a local release commit; publication is blocked because no remote is configured.

## Dependencies and gates

Gate 0 requires the expected clean branch/commit and repository instructions.
Gate 1 requires offline proof that restricted content cannot reach transport.
Gate 2 requires a grounded live response with controls recorded. Gate 3
requires full validation and no critical/high/medium findings. Gate 4 requires
a clean worktree. Gate 5 is blocked when no remote or publication authority is
available.

## Stop conditions

Stop on identity/worktree mismatch, absent API key at the live phase, any
possible secret exposure, unavailable model, live billing/auth/quota failure,
privacy proof failure, significant design defect, or unsafe publication.

## Expected outputs

Sanitized execution state, validation log, privacy proof, live response
metadata, final test results, reviewed documentation, and local release commit(s).
No pull request is created while the repository has no configured remote.
