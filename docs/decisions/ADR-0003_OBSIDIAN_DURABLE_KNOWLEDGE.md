# ADR-0003: Obsidian as durable curated knowledge

**Status:** Accepted  
**Date:** 2026-07-16

## Decision

Obsidian Markdown is the durable store for approved learning knowledge. Raw archives, extracted candidates, queues, and runtime state remain outside the curated vault.

## Rationale

The system must preserve portability, human editability, backlinks, and long-term access without trapping knowledge in a proprietary application database.

## Consequences

- A controlled writer and stable templates are required.
- Bulk extraction does not equal promotion.
- Runtime databases must be recoverable without becoming the sole copy of learning knowledge.
