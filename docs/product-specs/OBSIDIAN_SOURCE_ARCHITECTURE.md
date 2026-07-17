# Obsidian and Source Architecture

## Principle

Obsidian is the durable curated learning memory. It is not the raw archive, processing scratch space, or primary job database.

## Storage classes

### Raw archive

Original PDFs, videos, images, documents, course exports, and URL lists.

Rules:

- read-only by default;
- stable source IDs and hashes;
- backed up separately;
- not automatically copied into the vault.

### Candidate data

Extraction output, transcripts, summaries, tags, duplicate comparisons, and proposed notes.

Rules:

- machine-generated;
- searchable;
- clearly marked unreviewed;
- may be regenerated;
- not trusted as canonical knowledge.

### Curated vault

Approved source cards, canonical concept notes, labs, question banks, drills, weak areas, study sessions, projects, and portfolio artifacts.

Rules:

- portable Markdown;
- stable frontmatter;
- source provenance;
- one controlled writer;
- manual edits preserved;
- promotion requires approval.

### RAG index (derived, rebuildable)

The SQLite FTS5 keyword index and the planned Qdrant vector index are a read path over the curated vault, not a fourth storage class. One reusable RAG platform serves many independently governed knowledge packs — see `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`.

Rules:

- built only from curator-approved, promoted vault content — never from raw archive or candidate data directly;
- scoped per knowledge pack, and fully rebuildable within a pack or globally: deleting an index and re-running chunk -> embed -> index against currently-promoted content must restore equivalent retrieval behavior;
- never treated as the source of truth for any fact, citation, or promotion status;
- indexed chunks carry the same provenance (page, section, URL, or timestamp) as their source note.

## Proposed vault tree

```text
00-System/
01-Inbox/
02-Sources/
03-Concepts/
04-Courses/
05-Certifications/
06-Labs/
07-Question-Banks/
08-Drills/
09-Weak-Areas/
10-Projects/
11-Portfolio/
99-Archive/
```

## Candidate note workflow

1. Agent creates a `VaultNoteDraft`.
2. Deterministic validator checks path, frontmatter, links, and prohibited content.
3. Writer creates a temporary file inside the allowed candidate root.
4. Validation reopens and parses the temporary file.
5. Atomic rename replaces or creates the target.
6. Audit record stores before/after hash.
7. Human reviews candidate.
8. Promotion moves content to a canonical location through a separate approved action.
9. Promoted content becomes eligible for RAG indexing (chunk, embed, index) as a derived artifact within its knowledge pack — see `docs/product-specs/RAG_KNOWLEDGE_PLANE.md`.

## Source frontmatter example

```yaml
asset_id: src-00001234
title: Routing Concepts Module 14
source_type: pdf
status: approved
original_location: archive/networking/module14.pdf
content_hash: sha256-value
course: D419
certification: CCNA
topics:
  - routing-table
  - longest-prefix-match
reviewed_at: 2026-07-16
canonical_topics:
  - "[[Longest Prefix Match]]"
```

## Study-session frontmatter example

```yaml
session_id: session-20260716-001
session_date: 2026-07-16
workflow: study-session-v1
agents:
  - personal-assistant
  - tutor
  - drill-master
model_tiers:
  - local
course: D419
certification: CCNA
topics:
  - longest-prefix-match
accuracy: 0.80
confidence: developing
review_due: 2026-07-18
source_ids:
  - src-00001234
status: reviewed
```

## Promotion criteria

- source identity known;
- authority and accuracy acceptable;
- relevant to current or planned learning;
- not an inferior duplicate;
- licensing/private-use constraints recorded;
- claims traceable to page, timestamp, or URL where possible;
- human approval recorded.

## Backup

Required before production:

- vault backup independent of synchronization service;
- catalog database backup;
- restore into a clean test location;
- documented recovery point and restore commands;
- hash verification after restore.
