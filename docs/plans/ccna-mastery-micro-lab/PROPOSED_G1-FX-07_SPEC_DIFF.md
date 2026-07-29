# Approved G1-FX-07 specification diff — Gate 1 / WP6 hash sequencing

Status: **approved; traceability contract applied**
Date: 2026-07-28
Authority: Alan

This file keeps its historical `PROPOSED_` name so existing references do not
break. It now records the narrower decision that was approved and the
implementation boundary that follows from it.

## Decision

G1-FX-07 keeps every semantic pin that P0 can compute and byte-verify before
Gate 1:

- all required item, claim, scenario, policy, and schema versions;
- all four scripted learner files and their raw hashes;
- canonical response-vector semantics, expected dispositions and points,
  deterministic follow-up triggers and IDs, CLI grades, exit-probe grades,
  facet/outcome semantics, status transitions, and schedule bands;
- the full executed CLI starting-state and target-state SHA-256 values;
- allowed offline profile/provider combinations and the zero-hosted-spend
  boundary; and
- the exact base-manifest marker assigning production of the real event-stream
  and interrupted-replay hashes to WP6.

The marker is passing sequencing provenance for Gate 1 after the computable
pins validate. It is not a deferred GateCheck and it is not a substitute for a
runtime hash.

Existing G2-GO-08 exclusively owns the actual canonical event-stream and
interrupted-replay hashes. WP6 generates them from the append-only repository
and `SessionProjector`, and a reviewer accepts the resulting runtime golden.
No new Gate 2 row is created.

## Exact ownership marker

Gate 1 must compare the complete frozen value, not search for a convenient
substring:

> ALGORITHM FROZEN AT WP6, NOT NOW. See plan-amendment (RC-05): P0 freezes the
> canonical event model; the actual event-stream hash is generated and
> reviewer-accepted at the WP6 checkpoint. No value is fabricated here.

The immutable base manifest retains that marker. WP6 records its real hashes
in Gate 2 evidence; it does not replace the marker or rewrite the base fixture.

## Why this resolves the cycle

The former G1-FX-07 wording required an actual event-stream hash. The
implementation plan assigns that hash to WP6, while WP6 depends on work that
Gate 1 blocks. Combined with the rule that every required Gate 1 check must
pass, the old wording made the gate impossible to satisfy.

This split follows artifact ownership:

1. P0 freezes and verifies inputs, semantic targets, already executed CLI
   hashes, permitted routes, and the later-hash owner.
2. Gate 1 proves those P0-computable facts without pretending WP6 ran.
3. WP6 executes persistence/replay, produces the real event/replay hashes, and
   supplies them to existing G2-GO-08 for reviewer acceptance.

No required evidence disappears and no later gate compensates for an earlier
failure.

## Changes from the rejected broad draft

The earlier draft was not adopted because it would have:

- moved P0-computable learner vectors, outcomes, and profile pins out of Gate
  1;
- introduced an event-stream algorithm identifier the manifest does not
  define;
- added G2-GO-11 even though G2-GO-08 and G2-NG-03 already own replay
  determinism;
- allowed replacement of a marker in an immutable base manifest; and
- proposed tests under `tests/linchpin/`, whose exact-tree guard forbids new
  files.

The approved contract does none of those things.

## Implementation boundary

The fixture adapter exposes typed, fail-closed pins in
`FixtureExtensions`. Learner JSON and scenario YAML are decoded only after
their raw bytes match the manifest inventory. Parsing rejects duplicate IDs or
paths, duplicate JSON keys, malformed scalar types, summary/body drift,
unknown item or hint references, inconsistent follow-up mappings, invalid
grade totals, scenario/hash disagreement, and profile contradictions.

Focused positive and negative parser coverage lives at
`tests/unit/gate1_adversarial/test_fx07_semantic_pins.py`, outside the frozen
tree. Neither that test nor the adapter changes any fixture or golden byte.

The separate Gate 1 wiring change must return `PASSED` only after validating:

1. every required version pin;
2. exactly four fully populated typed learner pins and their cross-file
   invariants;
3. the full scenario start/target hashes and learner target agreement;
4. the allowed offline profile/provider pins and zero hosted scope; and
5. exact equality with the WP6 ownership marker above.

A substring marker check, presence-only check, fabricated hash, empty typed
envelope, or manifest-summary-only check must fail closed. That wiring change
is intentionally outside this parser/documentation lane.

## Artifact boundary

`tests/linchpin/expected/*` remain frozen P0 candidate specifications, not
accepted runtime gate reports. Accepted runtime gate-report goldens live under
`tests/goldens/ccna-mastery/`. Normal commands are read-only to both; observed
runs write under `var/ccna-mastery/gates/<run-id>/`.

## Sibling traceability note — test identities

Status: **approved 2026-07-29; applied to `LINCHPIN_TRACEABILITY.md`**

A fixture artifact path is never an executable test path. `tests/linchpin/` is
frozen fixture material, and its exact-tree guard admits only manifest-pinned
files, so a `.py` module placed there — and the `__pycache__` its import would
create — breaks every frozen-fixture load in this repository. Executable test
identities therefore always live under `tests/unit/…`; `tests/linchpin/…` may be
cited only as fixture data or as an expected artifact.

`LINCHPIN_TRACEABILITY.md` previously named 17 module paths under
`tests/linchpin/*.py` across Gates 1, 2, and 3, none of which could ever exist.
All of them now carry real `tests/unit/…` identities where the test exists, or a
valid planned `tests/unit/…` path marked `(planned)` where it does not.
`G1-FX-08` in particular resolves to
`tests/unit/gate1_adversarial/test_region_approval_cli.py`, which is where its
two tests have always actually lived.
