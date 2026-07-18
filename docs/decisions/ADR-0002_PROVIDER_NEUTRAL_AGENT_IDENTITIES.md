# ADR-0002: Provider-neutral agent identities

**Status:** Accepted  
**Date:** 2026-07-16

## Decision

Agent roles request capability profiles. They do not name OpenAI, Anthropic, Gemini, Qwen, or Ollama directly.

## Rationale

The project begins with a hybrid model strategy and must transition toward local open-source inference without rewriting every agent.

## Consequences

- A dedicated model router and provider adapter layer are required.
- Tests use fake providers.
- Provider-specific configuration stays outside agent contracts.
