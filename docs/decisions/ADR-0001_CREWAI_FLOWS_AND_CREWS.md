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
