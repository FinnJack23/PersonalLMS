# Build Week Scope

## Pre-existing foundation

The Personal LMS domain models, SQLite inventory, extraction queue, promotion
bridge, retrieval/grounding services, source verifier, provider protocols,
router, fake providers, and Ollama adapter were present at `c3f7e88`.

## New Codex-built functionality

- concurrent extraction enqueue convergence;
- redacted source-readiness contracts and importer boundary;
- evidence-to-drill contracts and local mastery persistence;
- Build Week tutor service, synthetic fixture, loopback demo page, and OpenAI
  Responses adapter scaffold;
- submission documentation and judge setup.

## Boundaries and non-goals

The data-cleaner remains a separate producer of safe manifests. This slice does
not scan archives, access cloud drives, extract ZIPs, write production Obsidian,
delete files, build Qdrant/embeddings, or deploy publicly.
