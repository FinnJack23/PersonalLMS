# ADR-0001: CrewAI Flows and bounded Crews

**Status:** Accepted for initial implementation  
**Date:** 2026-07-16

## Decision

Use the open-source CrewAI framework. CrewAI Flows will control deterministic workflow sequencing, routing, state, retries, policy, and approvals. Crews will be used only for bounded collaborative reasoning tasks.

## Rationale

The product needs both predictable control and specialized agent reasoning. A Flow-first design limits cost, improves testing, and prevents an uncontrolled agent swarm.

## Consequences

- CrewAI is an implementation dependency, not the system of record.
- Domain contracts must remain portable.
- The project will pin and test framework versions.
- Replacing CrewAI should not require rewriting Obsidian content or model-provider contracts.

## Implementation pattern (added after the first CrewAI integration commit)

CrewAI is introduced as an outer orchestration adapter, not by embedding routing or provider logic inside `@start`/`@listen`-decorated methods. `PersonalAssistantFlow` (`src/personal_lms/flows/`) is the framework-neutral application service: it owns routing (`DeterministicRouter`), provider execution, and the domain `RunState` audit trail, and remains directly usable and independently testable without CrewAI installed. `CrewAIPersonalAssistantFlow` (`src/personal_lms/adapters/crewai/`) is a thin `crewai.flow.flow.Flow` subclass with a single `@start()` method that delegates to `PersonalAssistantFlow.run()` unchanged and projects only audit-safe fields (request ID, run ID, run status, routing outcome, provider ID, error type — never prompt text) into CrewAI's own Flow state. This keeps the deterministic routing algorithm in exactly one place regardless of which orchestration framework calls it.

## CrewAI is optional (added after the environment-hardening commit)

Installing CrewAI as a normal dependency pulled 129 transitive packages (ChromaDB, LanceDB, ONNX Runtime, the OpenAI SDK, Kubernetes client libraries, and more) into every Personal LMS install, including deployments that never touch CrewAI. CrewAI is now an optional extra (`uv sync --extra crewai`); the core install (`uv sync`) resolves to 18 packages and does not include CrewAI or any of its transitive graph.

- `src/personal_lms/domain/`, `src/personal_lms/providers/`, `src/personal_lms/policies/`, and `src/personal_lms/flows/` never import `crewai` and work identically with or without the extra.
- `src/personal_lms/adapters/crewai/__init__.py` loads `CrewAIPersonalAssistantFlow` lazily, so merely importing the package never imports CrewAI; requesting the adapter without the extra raises a typed `CrewAIExtraNotInstalledError` with installation guidance instead of a raw `ModuleNotFoundError`.
- `src/personal_lms/adapters/crewai/runtime.py` is a standard-library-only bootstrap that applies CrewAI's offline/privacy environment defaults (`OTEL_SDK_DISABLED`, `CREWAI_DISABLE_VERSION_CHECK`, `CREWAI_TRACING_ENABLED=false`, `CREWAI_DISABLE_TRACKING`) before the one place in this codebase that imports the external package, independent of `.env` loading, pytest fixtures, or CLI callers.
- Future removal or replacement of CrewAI requires no change to domain schemas, provider contracts, routing policy, or `PersonalAssistantFlow` — only to the adapter package.
