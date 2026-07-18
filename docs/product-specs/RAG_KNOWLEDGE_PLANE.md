# RAG Knowledge Plane

## Purpose

Give agents source-grounded evidence retrieved from a curator-approved corpus before any generation happens, so that tutoring, drilling, and review answers are checked against real material rather than merely sounding consistent with it.

## Position in the architecture

Personal LMS has five planes:

```text
Orchestration plane   CrewAI Flows, Personal Assistant
Agent plane            Specialist agents (Tutor, Librarian, Curator, Drill Master, ...)
Knowledge plane         RAG: hybrid retrieval, grounding, provenance
Model plane              Tier 0 deterministic Python, Tier 1 local Qwen, Tier 2 hosted APIs
Data plane                 Obsidian vault, raw source archive, SQLite catalog, FTS5 index, vector index
```

RAG is not an agent. It has no persona, no prompt contract, and no decision authority. It is not a model provider — it never generates text; the model plane does that. It is not a replacement for Obsidian — Obsidian remains the durable, human-reviewed source of truth, and the RAG index is a derived read path over it. See `docs/decisions/ADR-0004_RAG_AS_THE_KNOWLEDGE_PLANE.md`.

## Knowledge packs: one platform, many domains

**One reusable RAG platform, many independently governed knowledge domains.**

RAG is domain-neutral. CCNA is the first production vertical and portfolio demonstration, not a hard-coded limitation of the platform. The same retrieval, grounding, and provenance infrastructure is designed to support independent **knowledge packs**, including — non-exhaustively:

- CompTIA A+ Core 1 and Core 2
- CCNA and Cisco networking
- KCNA and Kubernetes
- LFCS and Linux administration
- AWS, Azure, and GCP
- cybersecurity and Security+
- virtualization platforms
- Python, PowerShell, Bash, automation, and DevOps
- WGU and archived Wake Tech coursework
- career, resume, interview, and portfolio knowledge
- future user-defined domains

### Platform requirements

1. **Generic models.** Source, document, chunk, retrieval, citation, and grounding-bundle models are shared and domain-agnostic. None of them carries a CCNA-specific — or any other domain-specific — required field.
2. **Optional domain mappings.** Certification, course, objective-framework, project, and general-knowledge-domain associations are optional metadata attached to a source or chunk, not structural requirements of the shared models.
3. **Versioned knowledge packs.** Each domain is a versioned knowledge pack with its own source-approval policy — a CCNA pack and an A+ pack can have different promotion criteria, reviewers, and currency requirements without touching shared code.
4. **Single- and cross-domain retrieval.** A query can be scoped to one knowledge pack or span multiple packs; the retrieval interface does not assume a single active domain.
5. **Composable filtering.** Domain, certification, course, topic, and objective-framework filters compose independently of each other and of the existing privacy-classification filter.
6. **Independent evaluation.** Each knowledge pack has its own evaluation dataset; a regression in one pack's retrieval quality must not require touching another pack's evaluation suite.
7. **Conflict and supersession handling.** Conflicting or superseded material is resolved both within a knowledge pack (e.g. a revised CCNA edition superseding an older one) and across knowledge packs when the same underlying fact appears in more than one domain (e.g. a cloud-networking concept referenced from both an AWS pack and a Cisco-networking pack).
8. **Shared infrastructure, isolated governance.** SQLite catalog, FTS5 index, Qdrant collections, embedding pipeline, and reranker are shared infrastructure. Corpus-governance policy — who approves, what counts as authoritative, how currency is judged — is isolated per knowledge pack.
9. **Stable core interfaces.** Adding a new domain must never require changing the core retrieval, grounding, or citation interfaces — only registering a new knowledge pack with its own approval policy and evaluation dataset.

### Reference applications

- **CCNA Evidence-Checked Tutor** — the first reference application built on this platform (see Portfolio objective below).
- **CompTIA A+ Core 1/Core 2** — the next planned knowledge pack, exercising the platform's domain-neutrality claim with a second, structurally different certification (hardware/OS/troubleshooting rather than networking protocols).

## Principles

- RAG is domain-neutral: shared models carry no domain-specific required fields; domain scope is optional metadata plus a knowledge-pack registration, never a structural constraint (see Knowledge packs above).
- Obsidian remains the durable source of truth; the RAG index never is.
- The vector index (and the FTS5 keyword index) is derived and fully rebuildable from the curated vault plus catalog metadata. Deleting either index must never destroy knowledge.
- Raw sources stay outside the curated Obsidian vault and outside any trusted RAG corpus.
- Only curator-approved, promoted sources enter a knowledge pack's trusted RAG corpus. Candidate/unreviewed material may be searchable by internal curation tooling, but it is never presented to a learner as grounded evidence.
- Retrieval is hybrid: BM25/keyword plus vector similarity, combined with metadata filtering (domain, course, certification, topic, currency, source type).
- SQLite stores the source catalog and processing/indexing state.
- SQLite FTS5 is the initial keyword-search implementation.
- Qdrant is the planned local vector database, accessed only behind an abstract interface — no agent or Flow talks to Qdrant directly.
- Embeddings run locally by default.
- Qwen is the generation model for grounded answers; it is not necessarily the embedding model. Embedding model choice is an implementation detail behind the same abstract interface.
- A local reranker may reorder retrieval candidates before they reach generation.
- RAG retrieval happens before any premium-model escalation is even considered — hosted tiers see a grounding bundle, never a bare prompt.
- Hosted APIs receive only selected, redacted evidence from the grounding bundle, and only when privacy classification and budget policy permit it.
- Material classified `restricted_local_only` can never appear in a hosted request, whether as the user's own text or as retrieved evidence.
- Every grounded claim must preserve enough provenance to be checked: source, page, section, URL, or timestamp.
- Fine-tuning (e.g. LoRA) is a later behavioral-optimization step, not a substitute for RAG. RAG keeps answers checkable against real sources; fine-tuning changes how the model writes, not what it is allowed to assert.

## Ingestion-time workflow

```text
catalog -> extract -> classify -> deduplicate -> curate -> chunk -> embed -> index
```

1. **Catalog** — deterministic inventory of raw archive files/URLs; stable asset IDs and hashes (see `OBSIDIAN_SOURCE_ARCHITECTURE.md`).
2. **Extract** — text/transcript/metadata extraction into the candidate library. Machine-generated, unreviewed.
3. **Classify** — topic tagging, plus optional domain/knowledge-pack, course, certification, and objective-framework tagging. A chunk with no domain tag is still valid; it simply is not eligible for domain-scoped retrieval filters.
4. **Deduplicate** — exact and near-duplicate grouping so a corpus doesn't retrieve five copies of the same page.
5. **Curate** — human, Curator-assisted promotion decision, made against the target knowledge pack's own approval policy. This is the trust boundary: nothing to the left of this step is eligible for that pack's trusted RAG corpus.
6. **Chunk** — promoted content only, split with enough structure to preserve page/section/timestamp provenance per chunk.
7. **Embed** — a local embedding model produces vectors for each chunk.
8. **Index** — chunks land in SQLite FTS5 (keyword) and Qdrant (vector), tagged with the same metadata used for filtering at query time, scoped to their knowledge pack.

Re-running steps 6-8 against the same promoted content must reproduce an equivalent index — that is what "derived and rebuildable" means in practice.

## Query-time workflow

```text
request -> hybrid retrieval -> metadata filtering -> reranking -> grounding bundle -> Qwen/API generation -> source verification
```

1. **Request** — an agent (typically the Librarian, on behalf of the Personal Assistant, Tutor, or Drill Master) asks for evidence on a topic, optionally scoped to one or more knowledge packs.
2. **Hybrid retrieval** — BM25 (FTS5) and vector similarity (Qdrant) both run; candidates are merged.
3. **Metadata filtering** — domain/knowledge-pack, certification, course, topic, objective-framework, currency, and privacy classification narrow the candidate set. Filters compose independently and are all optional.
4. **Reranking** — an optional local reranker reorders candidates by relevance before anything reaches a generation model.
5. **Grounding bundle** — the final, small set of chunks plus their provenance (source, page/section/timestamp, approval status, knowledge pack) is assembled. This bundle is the only thing that can be forwarded toward Tier 2 if escalation is later needed, and only after redaction.
6. **Qwen/API generation** — Tier 1 (default) or Tier 2 (only after normal router escalation rules) generates an answer constrained to the grounding bundle.
7. **Source verification** — the Source Verifier, or a deterministic check where one applies, confirms the generated claims are actually supported by the grounding bundle before the response reaches the learner.

## Component responsibilities

| Component | Role |
|---|---|
| Knowledge pack registry | maps each domain to its own approval policy, evaluation dataset, and corpus scope |
| SQLite catalog | source inventory, processing state, promotion status, per knowledge pack |
| SQLite FTS5 | keyword/BM25 retrieval over promoted chunk text |
| Qdrant (planned) | vector similarity retrieval over promoted chunk embeddings, behind an abstract interface |
| Local embedding model | turns promoted chunks and queries into vectors; swappable, not Qwen-specific |
| Local reranker (optional) | reorders hybrid candidates before generation |
| Qwen (Tier 1) | default generation model for grounded answers |
| Hosted APIs (Tier 2) | escalation-only generation, restricted to redacted grounding-bundle evidence |

## Agent responsibilities

- **Librarian** — requests retrieval, optionally scoped to one or more knowledge packs; interprets the query; reports retrieval gaps, duplicate/superseded warnings, and approval status. Never invents a source that retrieval didn't return.
- **Curator** — approves corpus membership per knowledge pack, against that pack's own approval policy. Promotion in the Obsidian sense and trusted-corpus eligibility are the same decision, made once, per pack.
- **Tutor** and **Drill Master** — consume grounding bundles as their primary evidence when one is available; fall back to explicitly-labeled general knowledge only when retrieval returns nothing usable.
- **Source Verifier** — checks that a generated response is actually supported by its grounding bundle; flags unsupported claims rather than silently trusting fluent output.
- **Deterministic Python** — validates domain calculations (e.g. subnetting and longest-prefix-match for CCNA) independently of any retrieved or generated text, wherever a calculation rather than a citation can settle correctness.

## Privacy and hosted escalation

- The router's decision order runs RAG retrieval before it ever considers Tier 2 (see `MODEL_ROUTER_AND_COST_CONTROLS.md`).
- A grounding bundle is filtered by privacy classification before hosted escalation is evaluated; `restricted_local_only` chunks are dropped, not redacted-and-sent.
- What does reach a hosted provider is the minimal, redacted subset of the grounding bundle needed for the specific escalation reason — e.g. one ambiguous diagram's caption, not the whole retrieved set.

## Rebuildability

The FTS5 and Qdrant indexes are caches, not records. Loss or corruption of either is a maintenance event, not a data-loss event: both can be regenerated from the curated Obsidian vault and the SQLite catalog by re-running chunk -> embed -> index over currently-promoted content. Rebuilds may be scoped to a single knowledge pack or run globally. No workflow may treat the index as the only copy of anything.

## Portfolio objective

Design and implement a local-first CCNA Evidence-Checked Tutor that uses approved RAG sources, deterministic networking validation, local Qwen generation, citation verification, weak-area tracking, and controlled hosted API escalation. Later compare base Qwen, Qwen with RAG, LoRA Qwen, and LoRA Qwen with RAG using a reproducible evaluation suite.

The CCNA Evidence-Checked Tutor is the first reference application on the domain-neutral RAG platform described above (see Knowledge packs); CompTIA A+ is the next planned knowledge pack. If the platform can't add A+ without touching the core retrieval, grounding, or citation interfaces, the domain-neutrality claim hasn't actually been met.

This is also the concrete proof-of-value target for the RAG knowledge plane: if the Evidence-Checked Tutor cannot show a measurable citation-accuracy and hallucination-rate improvement from RAG — and later from RAG combined with LoRA over LoRA alone — the architecture is not earning its complexity.

## Explicitly not in this document

This is a design document. It defines no dependencies, no Pydantic schemas, no vector-database client, no embedding code, no CrewAI integration, and no retrieval implementation. Those belong to later, separately-approved commits.
