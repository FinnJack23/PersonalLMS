# Source Promotion and Extraction Queue

**Status:** Implemented (bounded milestone)
**Scope:** `src/personal_lms/domain/extraction.py`, `src/personal_lms/domain/promotion.py`, `src/personal_lms/extraction/`, `src/personal_lms/promotion/`

## Purpose

Give the raw-archive source inventory (`personal_lms.domain.source_inventory`, added in the prior milestone) an explicit, auditable path into the existing, unmodified curated source catalog (`personal_lms.domain.catalog.SourceCatalog`) — without ever auto-promoting anything, without weakening privacy or rights enforcement, and without merging the two bounded contexts into one table or model.

This milestone adds two new subsystems:

- **Extraction queue** (`personal_lms.extraction`) — a domain-neutral job queue for *requesting* extraction work against an inventory source, and recording metadata about what was produced. It implements no real extraction (no PDF, OCR, transcription, or archive handling).
- **Promotion bridge** (`personal_lms.promotion`) — an explicit, human-gated application service that reviews an extracted artifact and, only after an approved decision, writes a curated `SourceRecord` through the existing `SourceCatalog`.

## Why inventory and curated catalog stay separate

`SourceInventoryRecord`/`SourceVersion`/`SourceLocation` (raw-archive inventory) and `SourceRecord`/`SourceCatalog` (curated catalog, already consumed by the Librarian, grounding, content pipeline, and Tutor) are deliberately two different bounded contexts:

- The inventory layer may hold untrusted, unreviewed, or not-yet-extracted material. It uses its own `UUID` identity, its own processing/approval/rights enums, and its own SQLite tables (`source_inventory/sqlite.py`).
- The catalog layer represents material a human has already decided is trustworthy enough to ground tutoring, drilling, and retrieval. It keeps its existing caller-supplied `str` identity and its existing single combined lifecycle+approval enum (`SourceProcessingStatus`) unchanged.

Merging them into one model would force every consumer of the trusted catalog (Librarian, grounding, content pipeline, Tutor) to start reasoning about unreviewed material, and would make "is this eligible for retrieval" ambiguous. Keeping them separate, connected only through an explicit bridge, preserves the existing trust boundary exactly as it was before this milestone.

## Extraction job lifecycle

```text
PENDING → CLAIMED → RUNNING → SUCCEEDED
                            → FAILED_RETRYABLE → PENDING (explicit requeue)
                            → FAILED_TERMINAL
PENDING → CANCELLED
CLAIMED → CANCELLED
```

Every transition is enforced by `domain.extraction.ALLOWED_JOB_TRANSITIONS` / `is_valid_job_transition`, and every mutating queue operation is an explicit, caller-initiated call — there is no background retry loop, polling thread, or scheduler anywhere in this package. A `FAILED_RETRYABLE` job only returns to `PENDING` through an explicit `requeue()` call.

**Cancellation from `RUNNING` is unsupported in this milestone.** There is no cooperative-cancellation signal a running (fake, in this milestone) extractor could observe. A future milestone may add one; until then, a `RUNNING` job can only reach a terminal state via `record_success`/`record_retryable_failure`/`record_terminal_failure`.

### Atomic claim semantics

`SQLiteExtractionQueue.claim_next()` uses a single `UPDATE ... WHERE job_id = (SELECT ... LIMIT 1) RETURNING *` statement rather than a separate read-then-write pair. This was a deliberate correction during implementation: a `SELECT` followed by a conditional `UPDATE` (or a raw `BEGIN IMMEDIATE`, which conflicts with this codebase's `autocommit=False` connection convention) can let SQLite raise `SQLITE_BUSY` ("database is locked") on lock *upgrade*, bypassing `PRAGMA busy_timeout`'s retry-and-wait behavior. A single atomic statement sidesteps that entirely. This gives **at-most-one successful claim per job under this SQLite transaction boundary** — a guarantee about one database file and its connections, never a distributed-lock or exactly-once guarantee across processes or machines. See `tests/unit/extraction/test_sqlite.py::test_claim_next_at_most_one_winner_across_two_connections`.

## Artifact provenance

An `ExtractedArtifact` never carries the extracted payload itself — only metadata: an opaque `content_locator` (never a raw filesystem path a caller could use for unrestricted access), an optional `content_hash`, size/mime/language/page/duration metadata, and a nested `provenance` (job, inventory source, version, extractor name/version, timestamp). `artifact_id` is deterministic (`derive_artifact_id`, `uuid5` over job + artifact kind + content hash-or-locator), so re-recording the same logical artifact — e.g. a retried `record_success` call — never creates a duplicate row.

Extraction succeeding and being recorded is **not** the same as the artifact being trusted or approved. Warnings never silently convert a failure into a success, and nothing in this package ever writes to the curated catalog.

## Promotion candidate lifecycle

```text
build_candidate()  → PromotionCandidate (eligibility snapshot, never a promotion)
decide()           → PromotionDecision  (APPROVE / REJECT / DEFER, immutable, append-only)
promote()          → PromotionResult    (only after an APPROVE decision)
```

`PromotionCandidate.eligibility`/`.blockers` is a snapshot taken at creation time by the pure `evaluate_promotion_eligibility()` function. `SourcePromotionService.promote()` **re-evaluates eligibility independently at execution time** rather than trusting that snapshot — a source that was approved when the candidate was built but rejected before `promote()` runs is blocked, not silently promoted.

## The human approval boundary

No extraction completion, candidate creation, or approved decision automatically promotes anything. `SourcePromotionService.promote()` is always a separate, explicit call, and it requires the *latest* recorded decision for the candidate to have `outcome == APPROVE`. Extraction success is never itself an approval outcome — `PromotionDecisionOutcome` (`APPROVE`/`REJECT`/`DEFER`) is a distinct governance decision from `ExtractionJobStatus.SUCCEEDED`.

Decisions are immutable and append-only: a changed decision is always a *new* `PromotionDecision` row with a later `created_at`; `PromotionRepository.record_decision` never updates an existing row.

## Deterministic identity mapping

`SourceRecord.source_id` remains the existing caller-supplied `str` — this milestone never changes that public type.

- `derive_promotion_candidate_id(inventory_source_id, source_version_id, extracted_artifact_id)` — `uuid5` over a fixed namespace, so creating a candidate again for the same triple is idempotent.
- `derive_catalog_source_id(inventory_source_id)` — `uuid5` over a fixed namespace, keyed **only** on the inventory source, never the version.

### Version identity decision: Strategy B

Two strategies were possible (see the milestone brief's Part 7):

- **Strategy A** — each inventory source version promotes to a distinct curated `SourceRecord`.
- **Strategy B** — one curated identity stays stable while promoted version metadata is updated in place.

This implementation uses **Strategy B**: `derive_catalog_source_id` depends only on `inventory_source_id`. Promoting a later version of an already-promoted source updates the *same* `SourceRecord` row via the existing `SourceCatalog.upsert_source`'s insert-or-replace semantics — no new code was needed in the existing catalog to support this. Strategy A would have required generating distinct per-version source IDs and explicit `SUPERSEDES` `SourceAssetRelationship` bookkeeping to avoid leaving stale duplicate curated records behind; Strategy B is the less disruptive choice compatible with `SourceCatalog` as it already exists.

One consequence: the `ALREADY_PROMOTED` eligibility blocker is scoped to `(inventory_source_id, source_version_id)`, not to the inventory source alone — a second, *different* candidate for an already-completed version is blocked, but promoting a newer version of the same source is the intended, supported workflow and is never blocked by this check. See `PromotionRepository.has_completed_promotion_for_source_version`.

`PromotionMapping` is keyed uniquely by `inventory_source_id`; `mapping_version` records which *derivation strategy* produced `catalog_source_id` (today, always `1` = Strategy B) — not the promoted content's own version.

## Privacy and rights enforcement

`evaluate_promotion_eligibility()` (pure, deterministic, no I/O) blocks promotion unless **all** of the following hold, returning every applicable blocker rather than just the first:

| Blocker | Condition |
|---|---|
| `SOURCE_NOT_APPROVED` | inventory `approval_status != APPROVED` |
| `RIGHTS_NOT_CLEARED` | inventory `rights_status` not in `{OWNED, LICENSED, PUBLIC_REFERENCE}` (`UNKNOWN` and `RESTRICTED` both block — silence is never treated as clearance) |
| `EXTRACTION_NOT_SUCCESSFUL` | the extraction job's status isn't `SUCCEEDED` |
| `ARTIFACT_SOURCE_MISMATCH` | the artifact's provenance doesn't match the claimed inventory source |
| `ARTIFACT_VERSION_MISMATCH` | the artifact's provenance doesn't match the claimed source version |
| `PRIVACY_DOWNGRADE_FORBIDDEN` | the proposed privacy classification is *less* strict than the inventory source's (see `PRIVACY_STRICTNESS_ORDER`) |
| `ALREADY_PROMOTED` | a different candidate for the same `(source, version)` already completed |
| `MISSING_REQUIRED_METADATA` | the proposed title is blank |

A `RESTRICTED_LOCAL_ONLY` source may promote into the local catalog — promotion never touches or weakens the model router's hosted-escalation rules (`docs/product-specs/MODEL_ROUTER_AND_COST_CONTROLS.md`); the promotion module has no import of the routing/provider packages at all, and the router's own privacy filtering of the curated catalog is completely unaffected by anything in this milestone.

## Recoverable, idempotent — never a distributed transaction

The inventory, extraction queue, promotion repository, and curated catalog are four independently committed SQLite connections (in-memory in tests; independent files in production). This milestone makes **no claim of cross-database atomicity**. Instead, `SourcePromotionService.promote()` is built so that every step it takes is independently idempotent, and a partial failure is always safely retryable:

```text
PENDING → CATALOG_WRITE_STARTED → CATALOG_WRITE_CONFIRMED → COMPLETED
                                                            ↘ RECOVERY_REQUIRED (on failure at either write step)
```

- `derive_catalog_source_id` is a pure function of `inventory_source_id` alone — retrying always re-derives the same identity.
- `SourceCatalog.upsert_source` is insert-or-replace — re-running it after a partial failure never creates a duplicate curated record.
- `PromotionRepository.create_mapping` is idempotent: an existing mapping for the same inventory source is detected and either returned unchanged or updated in place, never duplicated.
- A second `promote()` call for an already-`COMPLETED` candidate short-circuits immediately and returns `PromotionResult(already_completed=True)` without touching the catalog again.

This means recovery never needs bespoke reconciliation branches for each failure point — a retried `promote()` call simply starts over and converges on `COMPLETED`. Tests demonstrate all three named recovery scenarios from the milestone brief:

- failure before any catalog write (`test_recovery_failure_before_catalog_write_leaves_no_curated_record`);
- catalog write succeeds but mapping persistence fails (`test_recovery_catalog_write_succeeds_mapping_fails_then_reconciles`);
- mapping exists but the completion flag was never persisted (`test_recovery_mapping_exists_completion_flag_missing_reconciles`).

This is accurately described as an **idempotent, recoverable promotion workflow across independently committed SQLite connections** — never as a distributed transaction, and this document deliberately avoids that phrase everywhere else too.

## `SourceRecord` construction (Part 12)

`SourcePromotionService._build_source_record` builds a curated `SourceRecord` from only reviewed and approved metadata:

- `source_id` — the deterministic `catalog_source_id`.
- `source_type`/`privacy_classification` — from the approved `PromotionCandidate`, never re-derived from raw extraction output.
- `original_location` — the inventory source's own `canonical_locator` (an existing, expected use of `SourceRecord.original_location`; never a candidate/`content_locator` value).
- `sha256_hash`/`byte_size` — from the immutable `SourceVersion`, falling back to the artifact's or the inventory record's own metadata only if the version doesn't carry it.
- `status` — always `APPROVED` (the curated-catalog meaning of "a human has reviewed and accepted this").
- `knowledge_scopes` — one `KnowledgeScope` per non-empty inventory `knowledge_domains`/`certifications`/`courses`/`topics` entry; all optional, none required (Rule 4 — no certification/course coupling anywhere in this milestone).
- `provenance.acquisition_note` — references only IDs (inventory source, version, extraction job, artifact) for audit traceability, never a raw locator, extraction warning, or credential.

## Non-goals (explicitly out of scope for this milestone)

- Any real extraction: no PDF, OCR, transcription, archive, or URL-fetch handling.
- Any production filesystem, network, Obsidian, or Personal Data Estate access — the fake extractor and every test use synthetic data and in-memory or temp-directory SQLite only.
- Auto-promotion of any kind.
- Changes to `Librarian`/grounding/content-chunk/Tutor retrieval behavior, or to `SourceRecord`/`SourceCatalog`'s existing public contract.
- Embeddings, Qdrant, FTS5 changes, or any RAG indexing.
- Distributed-transaction guarantees across the four SQLite stores.

## How future real extractors plug in

A real extractor (e.g. a PDF text-layer extractor) implements the same shape as `personal_lms.extraction.fake.FakeExtractor.extract()`: given an `ExtractionJob`, return an `ExtractionResult`. The queue, state machine, and idempotency guarantees are already extractor-agnostic — adding a real extractor requires no change to `domain/extraction.py`, `extraction/protocol.py`, or `extraction/sqlite.py`. A worker loop (out of scope here — "no implicit worker threads, no polling loop, no scheduler" is a hard requirement of this milestone) would call `claim_next()`, run the real extractor, and call `record_success`/`record_retryable_failure`/`record_terminal_failure` based on the result — exactly the pattern the fake extractor's tests already exercise.

## How approved sources reach the Librarian/RAG path

Once `SourcePromotionService.promote()` completes, the resulting `SourceRecord` is indistinguishable from any other curated catalog entry — it is immediately visible to `SourceCatalog.get_source`/`list_sources`/`search`, and therefore eligible for the existing `LibrarianContentGroundingService` and content-chunk pipeline exactly as it was before this milestone (`docs/product-specs/RAG_KNOWLEDGE_PLANE.md`'s ingestion-time `catalog → extract → classify → deduplicate → curate → chunk → embed → index` workflow — this milestone implements through *curate*; chunking, embedding, and indexing remain a later, separately-approved milestone).
