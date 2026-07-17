# ADR-0004: RAG as the knowledge plane

**Status:** Accepted
**Date:** 2026-07-16

## Decision

Retrieval-Augmented Generation (RAG) is introduced as a distinct architectural plane — the knowledge plane — sitting between the agent plane and the data plane. It is not an agent, not a model provider, and not a replacement for Obsidian.

The five planes are:

- orchestration plane — CrewAI Flows, Personal Assistant;
- agent plane — specialist agents;
- knowledge plane — RAG: hybrid retrieval, grounding, provenance;
- model plane — Tier 0 deterministic Python, Tier 1 local Qwen, Tier 2 hosted APIs;
- data plane — Obsidian vault, raw source archive, SQLite catalog, FTS5 index, vector index.

RAG supports two workflows: ingestion-time (catalog -> extract -> classify -> deduplicate -> curate -> chunk -> embed -> index) and query-time (request -> hybrid retrieval -> metadata filtering -> reranking -> grounding bundle -> Qwen/API generation -> source verification).

RAG is also domain-neutral: **one reusable RAG platform, many independently governed knowledge domains.** CCNA is the first production vertical and portfolio demonstration, not a hard-coded limitation of the platform. The same infrastructure is designed to support independent knowledge packs — CompTIA A+, CCNA/Cisco networking, KCNA/Kubernetes, LFCS/Linux, AWS/Azure/GCP, Security+, virtualization, Python/PowerShell/Bash/DevOps automation, WGU/Wake Tech coursework, career/portfolio knowledge, and future user-defined domains.

See `docs/product-specs/RAG_KNOWLEDGE_PLANE.md` for the full specification.

## Rationale

The project's learning agents need answers that are checkable against real, curator-approved material, not merely fluent. A single durable knowledge store (Obsidian) is not itself a retrieval engine, and treating a vector database as a second source of truth would create two competing authorities. Naming RAG as its own plane keeps the responsibility boundary explicit: Obsidian decides what is true and reviewed; RAG decides what is retrievable and how it is ranked; the model plane decides how it is phrased.

Alan's learning scope already spans multiple certifications and technology areas beyond CCNA. Building CCNA-specific fields into the shared retrieval, grounding, or citation models would force a rewrite the first time a second domain (A+) is added. Requiring domain-neutral shared models with optional domain metadata, plus versioned knowledge packs with their own governance, lets each domain's approval policy, evaluation dataset, and currency requirements evolve independently without touching shared code.

This also sets up the project's portfolio objective — a CCNA Evidence-Checked Tutor evaluated across base Qwen, Qwen with RAG, LoRA Qwen, and LoRA Qwen with RAG — which requires RAG to be a clean, independently swappable layer rather than something baked into agent prompts.

## Consequences

- The vector index (Qdrant, planned) and the keyword index (SQLite FTS5) are derived, fully rebuildable artifacts. Neither is ever the durable source of truth; both can be deleted and regenerated from the curated vault and SQLite catalog, per knowledge pack or globally.
- Only curator-approved, promoted sources enter a knowledge pack's trusted RAG corpus — candidate/unreviewed extraction output stays out of retrieval results shown to a learner.
- Source, document, chunk, retrieval, citation, and grounding-bundle models are generic; no domain-specific field is required on any shared model. Certification, course, objective-framework, and project mappings are optional metadata.
- Each knowledge pack is versioned and carries its own source-approval policy, reviewers, and evaluation dataset, isolated from other packs.
- Retrieval and filtering support single-domain and cross-domain queries via composable domain/certification/course/topic/objective-framework filters, layered on top of the existing privacy-classification filter.
- Conflict and supersession handling operates both within a knowledge pack and across knowledge packs.
- New domains are added by registering a knowledge pack — never by changing the core retrieval, grounding, or citation interfaces.
- The router's escalation policy must run RAG retrieval before it evaluates hosted-tier escalation, and any hosted call may only see a redacted subset of the grounding bundle.
- `restricted_local_only` material is excluded from grounding bundles before hosted escalation is even considered, not redacted afterward.
- Qwen is fixed as the default generation model, but the embedding model and reranker are separate, swappable choices — RAG does not assume Qwen is also the embedding model.
- Fine-tuning (e.g. LoRA) is scoped as a later behavioral optimization layered on top of RAG, not an alternative to it.
- CompTIA A+ Core 1/Core 2 is documented as the next planned knowledge pack after CCNA.
- This ADR adds no dependencies, schemas, or code. Implementation is deferred to later, separately-approved commits.
