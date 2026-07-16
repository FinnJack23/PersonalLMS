# Model Router and Cost Controls

## Purpose

Choose the least expensive and most private execution path that can reliably complete the task.

## Tiers

### Tier 0 — deterministic

No model call. Use tested Python services.

### Tier 1 — local

Qwen through Ollama. Default for routine language tasks.

### Tier 2 — hosted

Paid API used only after policy approval.

## Router inputs

- requested capability;
- task category;
- source trust level;
- privacy classification;
- image/vision requirement;
- estimated context size;
- required structured output;
- previous attempts;
- validation failures;
- local health and model availability;
- latency preference;
- local-only flag;
- premium request;
- daily/monthly budget state.

## Router outputs

```yaml
tier: local
profile: local_general
provider_key: ollama_qwen_default
reasons:
  - routine_text_task
  - local_model_available
  - within_context_limit
approval_required: false
redaction_required: false
fallbacks:
  - local_reasoning
  - hosted_reasoning
```

## Decision order

1. Determine whether a deterministic service can complete the task.
2. Classify privacy and prohibited transmission.
3. Check local-only mode.
4. Check local model health and capability.
5. Attempt local processing.
6. Validate output against schema and deterministic checks.
7. Retry locally only within bounded policy.
8. Evaluate hosted escalation eligibility.
9. Check budget and approval thresholds.
10. Redact and minimize context.
11. Execute hosted call and record audit event.
12. Return result with model-tier provenance.

## Privacy classes

- `public` — eligible for hosted routing.
- `private_low` — hosted only after minimization and policy.
- `private_restricted` — local only.
- `secret` — never included in model prompts; handled by deterministic services or omitted.

## Initial restricted examples

- passwords and API keys;
- private IP addresses when project policy requires withholding;
- `.env` contents;
- financial account records;
- health records;
- private identity documents;
- unredacted school/account credentials;
- unpublished licensed source documents unless explicitly approved.

## Validation examples

- Pydantic output parses;
- all requested answer choices are present;
- citations refer to retrieved sources;
- subnet calculations pass deterministic verification;
- CLI commands match configured platform constraints;
- Markdown frontmatter validates;
- no prohibited strings appear;
- a second local verifier does not materially disagree.

## Budget event schema

Each hosted use records:

- run ID;
- agent ID;
- workflow ID;
- provider and model;
- input/output token counts;
- estimated and actual cost when available;
- approval ID;
- escalation reasons;
- redaction status;
- date and time.

## Required user controls

- local-only global toggle;
- per-workflow local-only toggle;
- monthly hard limit;
- daily warning;
- approval threshold;
- provider enable/disable;
- model-profile override;
- visible current tier in the interface.
