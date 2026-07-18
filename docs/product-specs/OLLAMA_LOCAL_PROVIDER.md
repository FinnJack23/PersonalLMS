# Ollama Local Provider

## What this is

`OllamaProvider` (`src/personal_lms/providers/ollama/`) is a `ModelProvider` implementation for local inference through [Ollama](https://ollama.com), using only Ollama's native HTTP API: `GET /api/version`, `GET /api/tags`, and `POST /api/chat` with `stream=false`. It is a model provider, not an agent framework or orchestration layer — see ADR-0002 and `docs/product-specs/MODEL_ROUTER_AND_COST_CONTROLS.md`.

## Optional extra

Ollama support is not installed by default. Install it explicitly:

```bash
uv sync --extra ollama
```

This adds only `httpx` (a thin, auditable HTTP client) — not the official `ollama` Python package, and not the `ollama` CLI. `personal_lms.providers.ollama.OllamaProviderConfig` is always importable, even without the extra, since it has no HTTP dependency; only `OllamaProvider` itself requires `httpx`. Requesting it without the extra raises a typed `OllamaExtraNotInstalledError` with installation guidance, never a raw `ModuleNotFoundError`.

The Ollama and CrewAI extras are independent — either, both, or neither can be installed, and the Personal LMS core (domain schemas, provider registry, routing policy, `PersonalAssistantFlow`) works in every combination.

## Loopback-only by default

`OllamaProviderConfig.base_url` defaults to `http://127.0.0.1:11434` (Ollama's standard local port) and rejects any non-loopback host unless `allow_non_loopback=True` is set explicitly. It also rejects embedded credentials, query strings, fragments, and non-HTTP(S) schemes. This is a deliberate default for a tool that talks to a model server running on the same machine — reaching an Ollama instance on another host (a second machine on the LAN, for example) requires an explicit, conscious opt-in, not an accidental one.

## No automatic model management

This provider never pulls, pushes, creates, copies, or deletes a model, and never shells out to the `ollama` CLI. `is_model_installed()` is read-only — it checks `GET /api/tags` and reports whether the configured model is present. If it is not, `generate()` will fail with a typed `ProviderExecutionError` (or whatever Ollama's own HTTP response indicates); nothing in this provider tries to fix that automatically. Installing a model is an operator action, not something this codebase does on your behalf.

## Qwen is a deployment-time configuration, not a hard-coded default

`OllamaProviderConfig.model` is just a string — there is no Qwen-specific code path. Which model this provider talks to (Qwen, Llama, or anything else Ollama can serve) is decided when the config object is constructed, matching ADR-0002's rule that agent identities and `ModelRequest` objects stay vendor-neutral. Capability profile fields (`supports_reasoning`, `supports_vision`, `max_context_tokens`, `latency_class`, `cost_class`) are likewise explicit configuration, never guessed from a model-name string.

## Cost

`OllamaProvider.cost_per_call_usd` is always `Decimal("0")` — local inference never produces a hosted-API charge, and `ModelResult` (the domain schema) has no cost field to populate either way. This is not the same as inference being free in every sense: the model files consume disk space, generation consumes CPU/GPU time, and the machine consumes electricity. None of that is currently tracked by this codebase; "zero cost" here specifically means "no hosted API bill."

## What this commit does not do

No RAG retrieval, no embeddings, no SQLite, no Obsidian access, no specialist agents or Crews, no hosted-model fallback, no retries, and no download/deletion of models. `generate()` performs exactly one HTTP request and never retries; if it fails, the typed error propagates unchanged to the caller — routing and fallback decisions belong to the deterministic router (Commit 4), not to this provider.
