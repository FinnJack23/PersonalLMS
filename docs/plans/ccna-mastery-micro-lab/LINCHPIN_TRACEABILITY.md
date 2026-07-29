# CCNA Mastery Micro-Lab Linchpin Traceability

Status: planned, not implemented
Source of truth:
`docs/design/ccna-mastery-micro-lab-design/micro/11-48-HOUR-LINCHPIN-GATES.md`

No row below has passed yet. Planned symbols, tests, and artifacts name the
evidence required to earn a result; they are not evidence that the capability
already exists.

## Traceability conventions

- Required check status is `pass`, `fail`, or `deferred`.
- A gate passes only when every required check passes and none fail.
- `deferred` is permitted only for the optional Qwen smoke and the later
  week-scale retest-bank comparison.
- A manifest marker that assigns a runtime hash to its later owning work
  package is provenance, not a deferred GateCheck. G1-FX-07 may pass after it
  validates the exact WP6 ownership marker and every P0-computable pin; the
  real event-stream and interrupted-replay hashes remain required by
  G2-GO-08.
- A later gate cannot compensate for an earlier failure.
- JSON pointers below are logical targets within the named expected artifact.
- Negative tests must prove the gate reports `fail`; they must not rewrite a
  golden so the observed output appears to pass.
- **A fixture artifact path is never an executable test path.** `tests/linchpin/`
  is frozen fixture material only. Its exact-tree guard
  (`FrozenFixtureAssembler._verify_tree`, the same check G1-FX-06 depends on)
  admits only the files the manifest's `fixture_path_hash_inventory` pins, plus
  the manifest itself. A `.py` module placed there — and the `__pycache__` its
  import would create — makes every frozen-fixture load in this repository fail,
  and pinning one would be a fixture re-freeze requiring explicit approval.
  Executable test identities therefore always live under `tests/unit/…`, and
  `tests/linchpin/…` may be cited only as fixture data or as an expected
  artifact. Rows still awaiting their test name that path under `tests/unit/…`
  and mark it `(planned)`.
- Test identities are pytest node IDs. A leading `::name` continues the
  immediately preceding module **and class**, so
  `` `a/b.py::TestX::test_one`; `::test_two` `` means
  `a/b.py::TestX::test_two`. Every identity not marked `(planned)` resolves
  against the current tree.
- `tests/linchpin/expected/*` are frozen P0 candidate specifications; the
  evidence candidate explicitly records that it is not a runtime gate report.
- Accepted runtime gate-report goldens live under
  `tests/goldens/ccna-mastery/`. Normal commands are read-only with respect to
  both that tree and `tests/linchpin/expected/`. Every observed run writes
  instead beneath `var/ccna-mastery/gates/<run-id>/`.
- Candidate specifications, accepted runtime goldens, and observed reports
  must validate against the applicable frozen schema before comparison.
  Comparison may normalize only `started_at`, `finished_at`, and
  `elapsed_seconds`; no status, check, ID, reason code, content/state hash,
  fixture hash, or code revision may be normalized.
- Provider refusal or failure may append non-authoritative audit/refusal events.
  Provider-replacement checks compare the authoritative item, grade, mastery,
  and schedule projections rather than requiring identical full event streams.

Planned gate implementation:

```text
src/personal_lms/labs/ccna_mastery/gates.py
  GateCheck
  GateReport
  EvidenceGateRunner
  LoopGateRunner
  RouteIsolationGateRunner
  FactoryGateRunner
  GoldenArtifactGuard

src/personal_lms/labs/ccna_mastery/reporting.py
  GateDecision
  GateDecisionService
  ObservedGateReportStore
  GateReportComparator
```

## Gate-wide contract

| ID | Required statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| GLOBAL-01 | Gates are sequential; a later gate cannot compensate for an earlier failure. | `GateDecisionService.decide` | `tests/unit/labs/ccna_mastery/test_gate_decision.py::test_later_gate_cannot_compensate_for_earlier_failure` *(planned)* | `route-and-factory-report.json#/decision/ordered_gate_results` |
| GLOBAL-02 | Objective 2.2 sources, claims, baseline, follow-ups, and exit probe are technically reviewed and frozen before the 48-hour clock starts. | `FixtureManifest.assert_preclock_ready` | `tests/unit/objective_packs/test_linchpin_fixture.py::test_clock_refuses_unreviewed_or_unfrozen_fixture` *(planned)*; related today: `::TestManifestAdmission::test_a_manifest_never_grants_itself_reviewed_status` | `evidence-report.json#/clock` plus manifest SHA-256 |
| GLOBAL-03 | A gate passes only if every required check passes and none fail. | `GateReport.derive_status` | `tests/unit/gate1_adversarial/test_gate_authority.py::TestGlobalContractsAreExecutable::test_global_03_a_gate_passes_only_when_every_required_check_passes` | Each expected report `#/status` and `#/checks` |
| GLOBAL-04 | Deferred cannot hide an evidence, grading, state, route-safety, or authority failure. | `GateReport.derive_status` | `tests/unit/gate1_adversarial/test_gate_authority.py::TestGlobalContractsAreExecutable::test_global_04_deferral_cannot_hide_a_core_failure` | Negative case in `route-and-factory-report.json#/schema_validation` |
| GLOBAL-05 | Golden outputs require an explicit reviewer command and then a committed baseline; normal runs cannot rewrite them. The local baseline commit requires Alan's explicit authorization and never implies push/PR authority. | `GoldenArtifactGuard.assert_write_authorized` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestGoldenGuard::test_accepted_goldens_live_outside_the_exact_frozen_fixture_tree`; `::test_an_authorized_guard_still_requires_a_named_reviewer` | Accepted manifest hash, reviewer ID, command, explicitly authorized local commit SHA, and unchanged-file proof |
| GLOBAL-06 | Every machine report records `schema_version`, `gate_id` constrained to `gate-1 \| gate-2 \| gate-3`, `status`, fixture hash, code revision, `started_at`, `finished_at`, elapsed seconds, Gate 3 `route_isolation` and `factory_scalability` subgates, and checks containing `check_id`, `required`, `status`, `expected_ref`, `observed_hash`, and stable `reason_code`. | `GateReport` | `tests/unit/gate1_adversarial/test_gate_authority.py::TestGlobalContractsAreExecutable::test_global_06_a_report_records_every_required_structural_field`; `::test_global_06_gate_id_is_constrained_to_the_three_gates` | `schemas/gate-report.schema.json` validation result |
| GLOBAL-07 | Qwen smoke and week-scale retest comparison are the only deferrable checks. | `GateCheck.defer` allowlist | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestFrozenSchemaProjection::test_only_the_two_frozen_optional_checks_may_be_deferred`; `tests/unit/gate1_adversarial/test_gate_authority.py::TestDeferralAllowlistsAreUnified::test_the_internal_and_frozen_allowlists_are_identical` | `route-and-factory-report.json#/deferred_checks` |
| GLOBAL-08 | Expected reports are read-only; observed reports are written under `var/ccna-mastery/gates/<run-id>/`, validated in full, and compared after normalizing only timestamps and elapsed seconds. | `ObservedGateReportStore`; `GateReportComparator` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestGoldenGuard::test_a_normal_run_cannot_write_into_the_expected_tree`; `::test_an_observed_report_cannot_be_steered_into_the_expected_tree`; `::test_the_committed_expected_tree_is_untouched_by_a_gate_run` | Observed run directory, expected-artifact hashes, schema results, and comparison report |
| GLOBAL-09 | The immutable base manifest covers Gate 1, Gate 2, and the approved route fixture. Gate 3 uses a separate factory supplement that freezes criteria, evidence-ready inputs, and timing method—not the completed shadow pack or a pre-authored factory golden. | `FixtureManifest`; `FactorySupplementManifest` | Base tree today: `tests/unit/objective_packs/test_linchpin_fixture.py::TestTreeAdmission::test_an_unlisted_file_breaks_the_exact_tree_contract`; supplement: `tests/unit/labs/ccna_mastery/test_factory_supplement.py::test_supplement_freezes_inputs_not_completed_pack` *(planned)* | Base manifest SHA-256 plus `packs/objective-1.5-shadow/factory-supplement.yaml` SHA-256 |

## Gate 1 — Evidence and Objective-Pack assembler/validator

Expected artifact:
`tests/linchpin/expected/evidence-report.json`

### GO traceability

| ID | Required GO statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| G1-GO-01 | Every source hash, page, and image region resolves. | `SourceArtifactManifest.verify_bytes`; `EvidenceRegionResolver.resolve` in `objective_packs/validation.py` | `tests/unit/objective_packs/test_linchpin_fixture.py::TestTreeAdmission::test_every_listed_file_is_byte_verified`; `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRealSourceExtraction::test_png_pixels_reproduce_the_frozen_region_hashes` | `#/checks/G1-GO-01`, source/region resolution rows and hashes |
| G1-GO-02 | One infographic claim is visually reviewed and approved. | `VisualReviewDecision`; `EvidenceEligibility.is_teaching_eligible` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRetrievalContract::test_nothing_is_eligible_without_a_persisted_decision`; `::TestRealSourceExtraction::test_derived_pixel_boxes_match_the_frozen_review_record` | `#/checks/G1-GO-02`, reviewer, timestamp, image/region hash, accessible description |
| G1-GO-03 | Exactly 12 baseline items resolve exactly once. | `ObjectivePackValidator.validate_baseline_cardinality` | `tests/unit/objective_packs/test_linchpin_fixture.py::TestRecomputation::test_cardinality_is_recomputed_from_the_reference_graph`; `tests/unit/objective_packs/test_validation.py::TestCardinalityAndExposure::test_baseline_must_hold_exactly_the_policy_count` | `#/checks/G1-GO-03`, ordered item IDs and resolution counts |
| G1-GO-04 | Every follow-up and exit-probe ID resolves exactly once. | `ObjectivePackValidator.validate_item_references` | `tests/unit/objective_packs/test_validation.py::TestReferenceIntegrity::test_unknown_item_id_fails_fast`; `::test_duplicate_baseline_reference_is_reported` | `#/checks/G1-GO-04`, reference-to-record map |
| G1-GO-05 | Baseline, follow-up, and exit-probe exposure sets do not overlap. | `ObjectivePackValidator.validate_exposure_sets` | `tests/unit/objective_packs/test_linchpin_fixture.py::TestRecomputation::test_exposure_sets_are_pairwise_disjoint`; `tests/unit/objective_packs/test_validation.py::TestCardinalityAndExposure::test_exposure_sets_must_not_overlap` | `#/checks/G1-GO-05`, three sorted exposure sets and empty intersections |
| G1-GO-06 | Every answer-bearing claim has a grounding score of at least 85. | `ClaimEvidencePolicy.recompute_score`; `ObjectivePackValidator.validate_claim_grounding` | `tests/unit/objective_packs/test_linchpin_fixture.py::TestRecomputation::test_claim_grounding_reproduces_the_frozen_scores_exactly`; `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestCalculationPolicyReconciliation::test_the_six_frozen_scores_are_unchanged` | `#/checks/G1-GO-06`, factors, recomputed score, policy version, evidence IDs |
| G1-GO-07 | All 10 supported queries retrieve eligible support in the top five. | `LibrarianContentGroundingService.retrieve`; `EvidenceGateRunner.run_retrieval_cases` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRetrievalContract::test_the_diagram_cases_return_their_expected_region_in_the_top_five`; `::test_every_remaining_miss_is_a_pending_approval_not_a_ranking_defect` | `#/checks/G1-GO-07`, query IDs, ranked eligible evidence IDs, index hash |
| G1-GO-08 | Both unsupported queries abstain. | `EvidenceGateRunner.run_retrieval_cases`; `UnsupportedEvidenceDecision` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRetrievalContract::test_both_unsupported_cases_abstain_with_the_frozen_codes`; `::test_both_unsupported_cases_still_abstain_with_the_full_approved_corpus` | `#/checks/G1-GO-08`, exact abstention reason codes and zero support |
| G1-GO-09 | Zero quarantined, unauthorized, injected, or wrong-version region reaches a question or model context. | `EvidenceEligibility`; `ObjectivePackAssembler`; existing Tutor `trusted_blocks` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRetrievalContract::test_the_injected_and_wrong_version_regions_are_never_returned`; `::TestRetrievalContract::test_the_eligible_corpus_is_exactly_the_approved_regions` | `#/checks/G1-GO-09`, exclusion trace and provider-context digest |
| G1-GO-10 | A second run produces the same IDs and index-content hashes. | `CanonicalRecordHasher`; `EvidenceIndexSnapshot`; `EvidenceGateRunner` | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRetrievalContract::test_a_fresh_run_reproduces_the_same_ids_and_index_hash`; `::test_insertion_order_never_decides_a_ranking` | `#/checks/G1-GO-10`, run-A/run-B ID lists and hashes |
| G1-GO-11 | The pack validates offline in under five minutes. | `EvidenceGateRunner.run` with network-deny fixture and monotonic timer | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::test_g1_go_11_offline_under_five_minutes` *(planned)* | `#/checks/G1-GO-11`, elapsed seconds and zero transport calls |

### NO-GO traceability

| ID | Required NO-GO statement | Planned implementation symbol | Planned test | Expected failure evidence |
|---|---|---|---|---|
| G1-NG-01 | Stop if a citation is invented or cannot resolve. | `ObjectivePackValidator.validate_evidence_references` | `tests/unit/objective_packs/test_validation.py::TestReferenceIntegrity::test_an_invented_citation_cannot_resolve`; `::test_evidence_citing_an_unknown_source_is_reported` | `#/negative_cases/G1-NG-01`, stable unresolved-citation code |
| G1-NG-02 | Stop if an answer key depends on unapproved content. | `ObjectivePackValidator.validate_answer_evidence` | `tests/unit/objective_packs/test_validation.py::TestClaimGrounding::test_an_unsupported_answer_bearing_claim_scores_zero_and_fails`; `::test_a_weakly_supported_answer_bearing_claim_fails_the_threshold` | `#/negative_cases/G1-NG-02`, item/claim/evidence IDs |
| G1-NG-03 | Stop if the malicious paragraph changes behavior. | `EvidenceEligibility`; canonical question/context/output comparison | `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRealSourceExtraction::test_the_injected_paragraph_extracts_as_inert_data`; `::TestRetrievalContract::test_the_injected_and_wrong_version_regions_are_never_returned` | `#/negative_cases/G1-NG-03`, excluded region and unchanged output hashes |
| G1-NG-04 | Stop if the wrong exam version leaks through retrieval. | `EvidencePolicy.allows_objective_version`; pre-limit content filter | `tests/unit/objective_packs/test_validation.py::TestObjectiveVersionConsistency::test_a_claim_from_a_different_blueprint_version_is_reported`; `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRealSourceExtraction::test_the_wrong_version_pdf_extracts_but_stays_out_of_scope` | `#/negative_cases/G1-NG-04`, wrong-version source excluded from all ranked/context IDs |
| G1-NG-05 | Stop if manual cleanup exceeds four focused hours for the tiny pack. | `evaluate_gate_1_cleanup`; `EvidenceGateRunner._focused_time_ledger_check` | `tests/unit/gate1_adversarial/test_external_focused_work_ledger.py` | Complete external start/entry/closure attestations bound to fixture baseline `3798e218...997a3`, attempt, gate definition, authorized-scope hash, start revision, and trusted signer IDs; exact focused person-time in integer microseconds; no embedded entries, backfill, or absent-evidence zero |
| G1-NG-06 | Stop if the validator trusts declared coverage instead of recomputing it. | `ObjectivePackValidator.recompute_coverage` | `tests/unit/objective_packs/test_validation.py::TestRecomputedCoverage::test_declared_coverage_that_lies_cannot_pass`; `tests/unit/objective_packs/test_linchpin_fixture.py::TestRecomputation::test_declared_coverage_is_compared_against_recomputation` | `#/negative_cases/G1-NG-06`, declared versus observed reference graph |

### Gate 1 fixture-level requirements

| ID | Required fixture behavior | Planned test | Expected evidence |
|---|---|---|---|
| G1-FX-01 | Manifest verifies actual bytes, SHA-256, MIME, size, rights/use, version, and region selectors. | `tests/unit/objective_packs/test_linchpin_fixture.py::TestTreeAdmission::test_every_listed_file_is_byte_verified`; `::TestTranslation::test_source_currency_comes_from_the_source_not_its_regions` | `fixture-manifest.yaml` plus `evidence-report.json#/sources` |
| G1-FX-02 | Searchable PDF and PNG are locally extracted; no hosted model or sidecar substitution. | `tests/unit/extraction/test_local_fixture.py`; `tests/unit/labs/ccna_mastery/test_gate1_frozen_fixture.py::TestRealSourceExtraction::test_pdf_text_comes_from_the_frozen_bytes_via_pdfminer` | extractor ID/version and extracted-artifact hashes |
| G1-FX-03 | All six required technical claim IDs exist and have claim-specific evidence factors. | `tests/unit/objective_packs/test_linchpin_fixture.py::TestApprovalBoundary::test_exactly_six_claims_are_technically_approved`; `::test_answer_bearing_claims_are_derived_not_asserted` | `evidence-report.json#/claims` |
| G1-FX-04 | Unknown or unresolved item IDs fail immediately. | `tests/unit/objective_packs/test_validation.py::TestReferenceIntegrity::test_unknown_item_id_fails_fast` | stable validation reason code |
| G1-FX-05 | Retrieval corpus includes 10 supported, 2 unsupported, malicious, low-confidence, and wrong-version cases. | `tests/unit/objective_packs/test_linchpin_fixture.py::TestRetrievalContract::test_the_frozen_cases_load_as_typed_records`; `::test_unsupported_cases_pin_their_exact_abstention_codes` | manifest case inventory and hashes |
| G1-FX-06 | The exact required `tests/linchpin/` tree exists, and the base manifest pins every fixture path and SHA-256 across sources, packs, queries, learners, expected artifacts, and the gate schema. | `tests/unit/objective_packs/test_linchpin_fixture.py::TestTreeAdmission::test_an_unlisted_file_breaks_the_exact_tree_contract`; `::test_a_missing_file_breaks_the_exact_tree_contract`; `::TestManifestAdmission::test_self_hash_reproduces_the_reviewed_value` | Canonically sorted path/hash inventory in `fixture-manifest.yaml` |
| G1-FX-07 | The base manifest pins exact item, claim, scenario, policy, and schema versions; all four byte-verified scripted learner response vectors; expected follow-up IDs, grade dispositions, facet/outcome semantics, achievement/review/evidence states, and schedule bands; the full executed CLI starting/target state hashes; allowed offline provider/profile combinations; and the exact marker assigning generation and reviewer acceptance of the real event-stream and interrupted-replay hashes to WP6. Gate 1 validates that marker as sequencing provenance, not as a deferred check or fabricated runtime hash. The real WP6 hashes are exclusively G2-GO-08 evidence. | `tests/unit/gate1_adversarial/test_fx07_semantic_pins.py` | Typed learner/scenario/profile pins derived only after inventory byte verification, manifest-summary/body cross-checks, full CLI hashes, and exact WP6 ownership marker |
| G1-FX-08 | Text/image-region review is recorded through a bounded approval CLI that binds reviewer, decision, exact source/region hash, correction/accessibility text, and timestamp; stale or mismatched regions cannot be approved. | `tests/unit/gate1_adversarial/test_region_approval_cli.py::test_approval_cli_binds_decision_to_exact_region`; `::test_stale_region_approval_fails` | Approval command result and immutable visual-review record |
| G1-FX-09 | The local fixture extractor remains a narrow searchable-PDF/PNG adapter and does not introduce a broad ingestion schema migration or parallel extraction service. | `tests/unit/extraction/test_local_fixture.py::TestArchitectureDiffGuard::test_adapter_uses_existing_extraction_contracts`; architecture diff guard | Adapter contract inventory and empty broad-schema-migration set |

## Gate 2 — Closed mastery-loop replay

Expected artifacts:

- `tests/linchpin/expected/loop-results.json`
- `tests/linchpin/expected/event-and-state-hashes.json`

*Gate 2 is not implemented. Every test module named below —
`test_gate_2_loop.py`, `test_gate_2_negative.py`, `test_scripted_learners.py`,
`test_trunk_scenario_equivalence.py`, `test_interrupted_replay.py`, and
`test_grounded_feedback_adapter.py`, all under
`tests/unit/labs/ccna_mastery/` — is planned and does not exist yet.*

### GO traceability

| ID | Required GO statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| G2-GO-01 | Exactly 12 baseline items are presented. | `DeterministicItemSelector.select_baseline`; `BoundedStudySessionRunner` | `tests/unit/labs/ccna_mastery/test_gate_2_loop.py::test_g2_go_01_exactly_twelve_baseline_items` | `loop-results.json#/checks/G2-GO-01`, ordered presented IDs |
| G2-GO-02 | Zero to six follow-ups come only from deterministic mappings. | `DeterministicItemSelector.select_followups`; `FollowUpRule` | `tests/unit/labs/ccna_mastery/test_gate_2_loop.py::test_g2_go_02_followups_are_bounded_and_mapped` | `#/checks/G2-GO-02`, finding-to-rule-to-item trace |
| G2-GO-03 | At most one adaptive round, one CLI scenario, and one exit probe occur. | `SessionBounds`; `BoundedStudySessionRunner.advance` | `tests/unit/labs/ccna_mastery/test_gate_2_loop.py::test_g2_go_03_session_phase_counts_are_bounded` | `#/checks/G2-GO-03`, phase counters and terminal transition |
| G2-GO-04 | Replacing every model result with the fake provider cannot change a grade, item selection, mastery status, or due date. | `GroundedFeedbackService`; authority-field comparison | `tests/unit/labs/ccna_mastery/test_gate_2_loop.py::test_g2_go_04_provider_replacement_cannot_change_authority` | `#/checks/G2-GO-04`, fake/no-provider canonical-authority hashes |
| G2-GO-05 | Both correct CLI command orderings reach the same canonical state and grade. | `TrunkCommandParser`; `TrunkScenarioGrader` | `tests/unit/labs/ccna_mastery/test_trunk_scenario_equivalence.py::test_g2_go_05_correct_orderings_are_equivalent` | `#/checks/G2-GO-05`, command traces, state hash, grade ID |
| G2-GO-06 | Invalid commands never mutate state. | `CcnaTrunkPracticalAdapter.apply_command` | `tests/unit/labs/ccna_mastery/test_trunk_scenario_equivalence.py::test_g2_go_06_invalid_commands_do_not_mutate` | `#/checks/G2-GO-06`, before/after equal hashes and reason codes |
| G2-GO-07 | Every score reproduces from stored grade IDs and policy version. | `DeterministicGrader.reproduce`; `MasteryEvaluator` | `tests/unit/labs/ccna_mastery/test_gate_2_loop.py::test_g2_go_07_scores_reproduce_from_grade_ids_and_policy` | `#/checks/G2-GO-07`, grade IDs, policy version, recomputed scores |
| G2-GO-08 | WP6 generates the real canonical event-stream and interrupted-replay hashes and obtains reviewer acceptance; interrupted replay reproduces the event sequence, final state hash, facet scores, achievement, review status, and due window. | `SQLiteStudySessionRepository`; `SessionProjector.replay`; canonical event hashing | `tests/unit/labs/ccna_mastery/test_interrupted_replay.py::test_g2_go_08_interrupted_replay_matches_uninterrupted` | `event-and-state-hashes.json#/checks/G2-GO-08` with both projections, real WP6 hashes, and reviewer-acceptance evidence |
| G2-GO-09 | `clean-pass` receives `acquired`, never `retained_mastery`. | `MasteryEvaluator.evaluate_acquisition` | `tests/unit/labs/ccna_mastery/test_scripted_learners.py::test_g2_go_09_clean_pass_is_acquired_not_retained` | `loop-results.json#/learners/clean-pass` |
| G2-GO-10 | Advancing the frozen clock alone never creates retained mastery. | `ReviewScheduler`; `FrozenClock`; `MasteryEvaluator` | `tests/unit/labs/ccna_mastery/test_gate_2_loop.py::test_g2_go_10_clock_advance_alone_never_retains` | `#/checks/G2-GO-10`, before/after mastery status and event proof |

### NO-GO traceability

| ID | Required NO-GO statement | Planned implementation symbol | Planned test | Expected failure evidence |
|---|---|---|---|---|
| G2-NG-01 | Do not scale if a model selects an item, changes points, or commits status. | `GroundedFeedback` schema and `AuthorityFieldGuard` | `tests/unit/labs/ccna_mastery/test_gate_2_negative.py::test_g2_ng_01_model_authority_fields_are_rejected` | `loop-results.json#/negative_cases/G2-NG-01` |
| G2-NG-02 | Do not scale if a missing or ambiguous grade is silently averaged away. | `GradeDisposition.REVIEW_REQUIRED`; `MasteryEvaluator` | `tests/unit/labs/ccna_mastery/test_gate_2_negative.py::test_g2_ng_02_ambiguous_grade_blocks_numeric_mastery` | `#/negative_cases/G2-NG-02`, no numeric substitute |
| G2-NG-03 | Do not scale if the same events produce a different result. | `SessionProjector.replay`; canonical event hashing | `tests/unit/labs/ccna_mastery/test_gate_2_negative.py::test_g2_ng_03_identical_events_have_identical_projection` | `event-and-state-hashes.json#/negative_cases/G2-NG-03` |
| G2-NG-04 | Do not scale if the loop can continue without a fixed bound. | `SessionBounds`; terminal-state transition guard | `tests/unit/labs/ccna_mastery/test_gate_2_negative.py::test_g2_ng_04_loop_refuses_work_past_bound` | `loop-results.json#/negative_cases/G2-NG-04`, terminal reason |
| G2-NG-05 | Do not scale if retained mastery appears without a delayed novel attempt. | `MasteryEvaluator.evaluate_retention` | `tests/unit/labs/ccna_mastery/test_gate_2_negative.py::test_g2_ng_05_retention_requires_delayed_novel_attempt` | `#/negative_cases/G2-NG-05`, missing-evidence reason |

### Scripted learner outcomes

| ID | Required outcome | Planned test | Expected evidence |
|---|---|---|---|
| G2-LR-01 | `clean-pass`: `acquired`; retention scheduled. | `tests/unit/labs/ccna_mastery/test_scripted_learners.py::test_clean_pass` | `loop-results.json#/learners/clean-pass` |
| G2-LR-02 | `native-gap`: exact approved follow-ups; terminal status follows exit/lab evidence. | `tests/unit/labs/ccna_mastery/test_scripted_learners.py::test_native_gap` | `loop-results.json#/learners/native-gap` |
| G2-LR-03 | `ambiguous`: `review_required`; no numeric substitution or mastery. | `tests/unit/labs/ccna_mastery/test_scripted_learners.py::test_ambiguous` | `loop-results.json#/learners/ambiguous` |
| G2-LR-04 | `injection`: learner injection is data; deterministic outcome is unchanged. | `tests/unit/labs/ccna_mastery/test_scripted_learners.py::test_injection_is_inert_data` | `loop-results.json#/learners/injection` plus clean comparison hash |

### Tutor supplied-bundle and refusal contract

| ID | Required behavior | Planned test | Expected evidence |
|---|---|---|---|
| G2-TU-01 | Grounded feedback uses the supplied approved bundle structurally; it cannot perform fresh retrieval or enter general-knowledge mode. | `tests/unit/labs/ccna_mastery/test_grounded_feedback_adapter.py::test_supplied_bundle_mode_performs_no_retrieval_or_general_knowledge` | Retrieval-call count zero, supplied bundle hash, and Tutor request mode |
| G2-TU-02 | Insufficient grounding, citation outside the supplied bundle, or failed source verification produces a refusal/abstention and cannot mutate authoritative learning state. | `tests/unit/labs/ccna_mastery/test_grounded_feedback_adapter.py::test_insufficient_bundle_refuses`; `::test_out_of_bundle_citation_refuses`; `::test_source_verification_failure_refuses_without_authority_mutation` | Refusal reason, structural citation result, source-verification result, unchanged authority hash |
| G2-TU-03 | The fake provider is registered only by the dedicated test composition used for Gate 2; production/live composition cannot select it. | `tests/unit/labs/ccna_mastery/test_grounded_feedback_adapter.py::test_gate_2_fake_exists_only_in_test_composition` | Test composition inventory and production composition denial |

## Gate 3A — Route isolation

Expected artifact:
`tests/linchpin/expected/route-and-factory-report.json`

*Gate 3A is not implemented.
`tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py` and
`tests/unit/labs/ccna_mastery/test_gate_decision.py` are planned and do not
exist yet. `tests/unit/providers/ollama/test_smoke_test.py` (G3-RI-QWEN-02)
does exist.*

### Required GO traceability

| ID | Required GO statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| G3-RI-GO-01 | Fake provider is impossible to select in `live`. | `ExecutionProfile`; `ProviderRegistry`; `QualificationPolicy`; `DeterministicRouter` eligibility | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_g3_ri_go_01_fake_is_impossible_in_live` | `#/route_isolation/checks/G3-RI-GO-01`, denied candidate trace |
| G3-RI-GO-02 | An absent or expired qualification fails closed in live profiles. | `ProviderQualification`; `QualificationPolicy.require_current` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_g3_ri_go_02_missing_or_expired_qualification_fails_closed` | `#/route_isolation/checks/G3-RI-GO-02`, qualification IDs/expiry/reason |
| G3-RI-GO-03 | Provider output cannot change items, grades, achievement, or schedule. Provider refusal may add audit/refusal events but cannot change the canonical authority projection. | `CanonicalResultProjector`; `AuthorityFieldGuard`; `GroundedFeedbackService` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_g3_ri_go_03_provider_cannot_change_authoritative_state` | `#/route_isolation/checks/G3-RI-GO-03`, invariant authority hashes and separate refusal-event trace |
| G3-RI-GO-04 | No provider SDK object enters the domain. | `ModelProvider` boundary; canonical Pydantic projection | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_g3_ri_go_04_no_sdk_object_in_domain_graph` | `#/route_isolation/checks/G3-RI-GO-04`, serialized canonical type inventory |
| G3-RI-GO-05 | Provider-specific schema projection is revalidated against the canonical Pydantic model. | `CanonicalResultProjector.project_and_validate` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_g3_ri_go_05_projection_is_canonically_revalidated` | `#/route_isolation/checks/G3-RI-GO-05`, schema ID and validation result |

### Approved provider-replay fixture

| ID | Required statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| G3-RI-FX-01 | The same approved grounded-feedback request, bundle hash, and canonical schema run through the fake provider in `test` and optional Qwen smoke. Fake must succeed only in `test`; Qwen may defer only for unavailable Ollama. | `ApprovedGroundedFeedbackFixture`; test composition; `RouteIsolationGateRunner` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_same_approved_fixture_replays_fake_test_and_optional_qwen` | Fixture/request/bundle/schema hashes, fake-test result, and Qwen result or allowed deferral |

### Optional Qwen smoke

| ID | Required statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| G3-RI-QWEN-01 | Qwen smoke may be `deferred` only when Ollama is unavailable. | `RouteIsolationGateRunner.run_optional_qwen_smoke` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_qwen_defers_only_for_unavailable_ollama` | `#/route_isolation/qwen_smoke/status` and reason |
| G3-RI-QWEN-02 | If run, record resolved digest, quantization, runtime, and smoke status. | Extended `providers.ollama.smoke_test.run_smoke_test` and qualification record | `tests/unit/providers/ollama/test_smoke_test.py::test_qualification_metadata_is_recorded` *(planned)* | `#/route_isolation/qwen_smoke/qualification` |
| G3-RI-QWEN-03 | If run, Qwen returns the same canonical result type as the fake fixture. | `CanonicalResultProjector` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_qwen_and_fake_project_to_same_canonical_type` | `#/route_isolation/qwen_smoke/canonical_schema_id` |
| G3-RI-QWEN-04 | Qwen deferral does not weaken required static/live-route safety checks. | `GateReport.derive_status` | `tests/unit/labs/ccna_mastery/test_gate_3_route_isolation.py::test_qwen_deferred_route_checks_still_required` | `#/route_isolation/status` plus all five required check statuses |

### Route NO-GO classification

| ID | Required NO-GO statement | Planned test | Expected evidence |
|---|---|---|---|
| G3-RI-NG-01 | Any required route-isolation failure blocks the August 1 trial, regardless of factory or Qwen status. | `tests/unit/labs/ccna_mastery/test_gate_decision.py::test_route_failure_blocks_august_1_for_any_factory_status` | `#/decision/august_1_one_objective_trial` with blocking check IDs |

## Gate 3B — Factory scalability

Expected artifact:
`tests/linchpin/expected/route-and-factory-report.json`

*Gate 3B is not implemented.
`tests/unit/labs/ccna_mastery/test_gate_3_factory.py` and
`tests/unit/labs/ccna_mastery/test_gate_decision.py` are planned and do not
exist yet.*

### GO traceability

| ID | Required GO statement | Planned implementation symbol | Planned test | Expected evidence |
|---|---|---|---|---|
| G3-FS-GO-01 | The second pack requires no Python or domain-schema change. New reviewed shadow-pack content and its factory supplement are expected and allowed. | `FactoryGateRunner`; `PythonDomainSchemaChangeGuard` | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_go_01_shadow_pack_needs_no_code_or_schema_change` | `#/factory_scalability/checks/G3-FS-GO-01`, empty Python/domain-schema change set plus allowed shadow-content paths |
| G3-FS-GO-02 | Pack creation after evidence is ready takes at most three focused hours. | `FocusedWorkLedger.evaluate_factory_time` | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_go_02_factory_time_limit` | `#/factory_scalability/checks/G3-FS-GO-02`, evidence-ready/start/stop ledger |
| G3-FS-GO-03 | The same generic validator and session runner load the second pack. | `ObjectivePackValidator`; `BoundedStudySessionRunner` | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_go_03_same_validator_and_runner_load_shadow_pack` | `#/factory_scalability/checks/G3-FS-GO-03`, implementation IDs and run trace |
| G3-FS-GO-04 | Missing required evidence produces an honest incomplete/developing result. | `MasteryEvaluator.evaluate_required_modalities` | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_go_04_missing_practical_evidence_refuses_operational_mastery` | `#/factory_scalability/checks/G3-FS-GO-04`, missing modality and status reason |

### NO-GO traceability

| ID | Required NO-GO statement | Planned test | Expected failure evidence |
|---|---|---|---|
| G3-FS-NG-01 | Factory scalability fails if a new objective requires Python changes. | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_ng_01_python_change_fails_factory` | `#/factory_scalability/negative_cases/G3-FS-NG-01` |
| G3-FS-NG-02 | Factory scalability fails if the first reviewed 12-item shadow pack takes more than three hours after evidence is ready. | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_ng_02_over_three_hours_fails_factory` | `#/factory_scalability/negative_cases/G3-FS-NG-02` |
| G3-FS-NG-03 | Factory scalability fails if the generic validator or runner needs objective-specific branching. | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_g3_fs_ng_03_objective_specific_branch_fails_factory` | `#/factory_scalability/negative_cases/G3-FS-NG-03` |
| G3-FS-NG-04 | Factory failure blocks several-objectives-per-day scaling but does not alone block objective 2.2 when Gates 1, 2, and route isolation pass. | `tests/unit/labs/ccna_mastery/test_gate_decision.py::test_factory_only_failure_allows_narrow_trial_but_blocks_scale` | `#/decision` with separate trial and scaling statuses |

### Shadow-pack contract

| ID | Required shadow behavior | Planned test | Expected evidence |
|---|---|---|---|
| G3-SH-01 | Pack identifies current v1.1 objective 1.5, “Compare TCP to UDP.” | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_shadow_identity_and_version` | `#/factory_scalability/shadow_pack/objective` |
| G3-SH-02 | Pack contains objective/concept map, approved claims, 12 complete deterministic items, follow-up mappings, one near-transfer exit probe, and mastery policy. | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_shadow_pack_is_complete` | `#/factory_scalability/shadow_pack/validation` |
| G3-SH-03 | It has no CLI adapter; if policy requires practical evidence, the runner completes the diagnostic but refuses full operational mastery. | `tests/unit/labs/ccna_mastery/test_gate_3_factory.py::test_shadow_without_practical_is_diagnostic_only` | `#/factory_scalability/shadow_pack/outcome` |

## Decision-matrix traceability

| Gate 1 | Gate 2 | Route isolation | Factory | Required decision | Planned test |
|---|---|---|---|---|---|
| Fail | Any/not run | Any/not run | Any/not run | No-go; fix evidence and pack assembly. | `test_gate_1_failure_stops_sequence` |
| Pass | Fail | Any/not run | Any/not run | No-go; architecture cannot prove learning state. | `test_gate_2_failure_blocks_trial` |
| Pass | Pass | Fail | Any | No-go for August 1; route/authority safety failed. | `test_route_failure_blocks_august_1_for_any_factory_status` |
| Pass | Pass | Pass | Fail | Go for objective 2.2 on August 1; no-go for several objectives per day. | `test_factory_only_failure_allows_narrow_trial_but_blocks_scale` |
| Pass | Pass | Pass | Pass | Go for August 1 and controlled objective-factory scaling. | `test_all_required_gates_pass_both_decisions` |

All decision tests live in `tests/unit/labs/ccna_mastery/test_gate_decision.py`,
which is planned and does not exist yet; the bare test names in the table above
are identities within that planned module. The expected decision is stored at
`tests/linchpin/expected/route-and-factory-report.json#/decision` — a frozen
fixture artifact, not a test module.

“Go” means the architecture earned the next bounded investment. It does not
claim certification readiness, retained mastery, production-scale ingestion,
or an A2A-ready multi-agent deployment.
