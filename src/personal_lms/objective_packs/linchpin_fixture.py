"""Adapter for a frozen, split-YAML Objective Pack fixture tree.

This is an *adapter at the fixture boundary*, and everything about its
shape follows from that. The domain stays format-neutral and strict;
authoring-only fields are translated explicitly and never passed through
as extras; every non-manifest byte is hash-verified before it is decoded;
and the manifest's documented self-hash is verified separately, because a
manifest cannot pin itself.

Domain-neutral by construction. No certification, exam, vendor, or
objective name appears here — the objective reference, versions, titles,
and file roles all come from the frozen data. ``FixtureLayout`` names the
*convention* a split tree follows so the convention is configuration
rather than string literals buried in control flow.

Authority direction
-------------------

The one rule this module exists to enforce: **an authoring file may
restrict what its content is eligible for, and may never grant anything.**

A region authored ``reviewer_status: approved`` is a claim by whoever
wrote the file. It is translated to ``ReviewState.PENDING`` and
``TrustStatus.UNTRUSTED``, exactly like a region authored ``pending``,
because the only thing that authorizes evidence is a persisted
``EvidenceReviewDecision`` bound to the current subject — see
``evidence_review.authority``. A region authored ``rejected`` *is*
honoured, because that direction only ever removes eligibility.

The earlier revision of this adapter mapped every non-rejected region to
``APPROVED``/``TRUSTED`` so the strict record's
``trusted_requires_approved_review`` invariant would be satisfied. That
turned "the fixture says so" into domain approval for six regions no
human had signed off, which is the precise laundering the review boundary
exists to prevent.

Human decisions recorded in the fixture (a reviewer id, a verbatim
attestation, a date) are preserved in the fixture-extension envelope as
*evidence that a decision was made*. They are inert here: they inform a
reviewer replaying the decision through the ordinary approval command,
and no code path turns them into authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from personal_lms.domain.objective_packs import (
    ApprovedClaim,
    AssessmentItem,
    ClaimSupport,
    EvidenceRegion,
    ExposureClass,
    ExtractionMetadata,
    FollowUpRule,
    ImageRegionSelector,
    ManifestEntry,
    MasteryPolicy,
    ObjectiveFacet,
    ObjectivePack,
    ObjectivePackManifest,
    ObjectiveRef,
    PageTextSelector,
    PermittedUse,
    QuarantineStatus,
    ReviewState,
    SourceArtifactRef,
    TrustStatus,
)
from personal_lms.domain.source_inventory import SourceRightsStatus
from personal_lms.extraction.artifacts import detect_media_type
from personal_lms.objective_packs.errors import PackManifestError, PackSchemaError
from personal_lms.objective_packs.loader import (
    PackFileReader,
    PackLoadResult,
    SourceManifestBinding,
)

__all__ = [
    "AllowedProfileProviderPin",
    "FIXTURE_MANIFEST_FILENAME",
    "ExpectedResponsePin",
    "SELF_HASH_PLACEHOLDER",
    "FixtureExtensions",
    "FixtureLayout",
    "FrozenFixtureAssembler",
    "RetrievalCase",
    "RetrievalCaseSet",
    "ScenarioStateHashPins",
    "ScriptedLearnerPin",
    "compute_manifest_self_hash",
    "load_frozen_fixture",
    "scripted_learner_authority_projection",
]

FIXTURE_MANIFEST_FILENAME = "fixture-manifest.yaml"

#: The literal the manifest's own hash value is replaced by before the
#: file is digested. Documented in the manifest itself; reproduced here so
#: the algorithm has exactly one implementation.
SELF_HASH_PLACEHOLDER = b"PENDING_COMPUTED_BELOW"

_SELF_HASH_PATTERN = re.compile(rb"^(manifest_self_sha256:[ \t]*)([0-9a-f]{64})([ \t]*)$", re.M)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INVENTORY_KEY = "fixture_path_hash_inventory"
_REQUIRED_MANIFEST_VERSION_KEYS = frozenset(
    {
        "objective_version",
        "item_bank_version",
        "claim_set_version",
        "scenario_version",
        "mastery_policy_version",
        "grounding_calculation_policy_version",
        "schedule_policy_version",
        "gate_schema_version",
    }
)
_SCRIPTED_LEARNER_COUNT = 4
_ALLOWED_RESPONSE_DISPOSITIONS = frozenset({"graded", "review_required"})

#: Media types resolved from a path suffix, for manifest entries. Source
#: artifacts additionally have their magic bytes sniffed — a suffix is a
#: naming convention, not evidence about content.
_MEDIA_TYPE_BY_SUFFIX: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".md": "text/markdown",
}

#: Authored source registration states this adapter understands. Anything
#: else is refused rather than defaulted, so a new state cannot arrive as
#: a silent grant.
_REGISTERED = "registered"
_QUARANTINED = "quarantined"

#: Authored rights bases mapped to the shared rights enum. Absence of a
#: mapping denies; there is no permissive default.
_RIGHTS_BASIS: dict[str, SourceRightsStatus] = {
    "user_created": SourceRightsStatus.OWNED,
    "owned": SourceRightsStatus.OWNED,
    "licensed": SourceRightsStatus.LICENSED,
    "public_reference": SourceRightsStatus.PUBLIC_REFERENCE,
}


@dataclass(frozen=True, slots=True)
class FixtureLayout:
    """Where a split fixture tree keeps each kind of document.

    Defaults describe the frozen convention. They are fields rather than
    literals so a second fixture with a different arrangement is a
    configuration change and not a fork of this module.
    """

    sources_document: str = "sources/source-registrations.yaml"
    visual_review_document: str = "sources/ai-visual-review.yaml"
    retrieval_cases_document: str = "queries/retrieval-cases.json"
    pack_directory_prefix: str = "packs/"
    claims_document: str = "claims.yaml"
    baseline_items_document: str = "baseline-items.yaml"
    followup_items_document: str = "followup-items.yaml"
    exit_probe_items_document: str = "exit-probe-items.yaml"


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    """One frozen retrieval case: a query and what it must (not) return."""

    case_id: str
    kind: str
    query: str
    supported: bool
    expected_evidence_ids: tuple[str, ...] = ()
    expected_top_k: int = 5
    expected_abstention_reason_code: str | None = None
    must_never_return: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalCaseSet:
    """The frozen retrieval contract, as typed records."""

    objective_ref: str
    cases: tuple[RetrievalCase, ...]

    @property
    def supported(self) -> tuple[RetrievalCase, ...]:
        return tuple(case for case in self.cases if case.supported)

    @property
    def unsupported(self) -> tuple[RetrievalCase, ...]:
        return tuple(case for case in self.cases if not case.supported)


@dataclass(frozen=True, slots=True)
class ExpectedResponsePin:
    """One byte-verified scripted response's grading semantics.

    The answer itself stays data. ``answer_sha256`` pins its canonical JSON
    value without widening this fixture adapter into an answer schema, while
    the fields that deterministic grading consumes are exposed explicitly.
    """

    item_id: str
    answer_sha256: str
    confidence: int | None
    expected_disposition: str | None
    expected_correct: bool | None
    expected_points: int | None
    misconception_tags: tuple[str, ...] = ()
    expected_reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScriptedLearnerPin:
    """The P0-computable contract for one scripted learner vector."""

    learner_vector_id: str
    relative_path: str
    raw_sha256: str
    objective_ref: str
    response_vector_sha256: str
    baseline_responses: tuple[ExpectedResponsePin, ...]
    followup_responses: tuple[ExpectedResponsePin, ...]
    expected_followup_trigger_codes: tuple[str, ...]
    expected_followup_item_ids: tuple[str, ...]
    cli_equivalence_case_ref: str
    cli_expected_final_state_sha256: str
    cli_hint_ids_used: tuple[str, ...]
    cli_grade_points: tuple[tuple[str, int], ...]
    exit_probe_item_id: str
    exit_probe_answer_sha256: str
    exit_probe_hint_ids_used: tuple[str, ...]
    exit_probe_expected_criteria_awarded: tuple[str, ...]
    exit_probe_expected_total: int
    expected_facet_derivation_sha256: str | None
    outcome_sha256: str
    expected_progress_phase: str
    expected_overall_m: str | None
    expected_achievement_status: str
    expected_review_status: str
    expected_evidence_status: str
    expected_schedule_band: str | None
    expected_reason_codes: tuple[str, ...]
    required_observations_complete: bool
    critical_error: bool
    must_equal_learner_vector_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioStateHashPins:
    """The already-executed CLI start/target hashes frozen at P0."""

    relative_path: str
    raw_sha256: str
    scenario_ref: str
    scenario_version: str
    objective_ref: str
    starting_state_sha256: str
    target_repaired_state_sha256: str


@dataclass(frozen=True, slots=True)
class AllowedProfileProviderPin:
    """One allowed execution-profile/provider combination from the manifest."""

    profile: str
    provider_ids: tuple[str, ...]
    offline_only: bool
    allow_domain_result_writes: bool | None = None


@dataclass(frozen=True, slots=True)
class _ScenarioSemanticContext:
    pins: ScenarioStateHashPins
    equivalence_case_ids: tuple[str, ...]
    hint_ids: tuple[str, ...]
    grade_weights: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class FixtureExtensions:
    """Authoring metadata the strict domain records deliberately exclude.

    Source byte sizes, magic bytes, image pixel dimensions, derived pixel
    boxes, and region pixel hashes are all real and all checkable — they
    are simply not fields the documented contracts define. Keeping them
    here rather than as extras on ``SourceArtifactRef`` or a selector is
    what lets those records stay ``extra="forbid"``.

    ``recorded_human_decisions`` holds attestations the fixture
    transcribes. They are inert: nothing in this package or downstream
    reads them to authorize anything.
    """

    source_sizes: dict[str, int] = field(default_factory=dict)
    source_magic_bytes: dict[str, str] = field(default_factory=dict)
    image_dimensions: dict[str, tuple[int, int]] = field(default_factory=dict)
    region_pixel_sha256: dict[str, str] = field(default_factory=dict)
    region_pixel_boxes: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    recorded_human_decisions: tuple[dict[str, str], ...] = ()

    #: The manifest's ``versions:`` section (G1-FX-07), verbatim string values.
    manifest_versions: dict[str, str] = field(default_factory=dict)
    #: The two hash-provenance notes G1-FX-07 distinguishes: whichever of
    #: ``cli_state_hash``/``event_stream_hash`` the manifest's
    #: ``canonicalization_rules`` section actually declares. One carries a
    #: real computed value now; the other is explicitly marked deferred to
    #: WP6, never a fabricated hash.
    manifest_canonicalization_notes: dict[str, str] = field(default_factory=dict)
    #: P0-computable semantic pins, decoded only after their exact bytes pass
    #: the manifest inventory check. Gate 1 validates these; Gate 2 later
    #: compares its observed session records against them.
    scripted_learner_pins: tuple[ScriptedLearnerPin, ...] = ()
    scenario_state_hash_pins: ScenarioStateHashPins | None = None
    allowed_profile_provider_pins: tuple[AllowedProfileProviderPin, ...] = ()
    hosted_profiles_enabled: tuple[str, ...] = ()
    hosted_spend_ceiling_usd: str | None = None
    #: The frozen ``focused_time_ledger_contract`` section (G1-NG-05), or
    #: ``None`` when the manifest carries none at all. Parsed, not
    #: interpreted here — see ``labs.ccna_mastery.focused_work_ledger``.
    focused_time_ledger_document: dict[str, Any] | None = None


def compute_manifest_self_hash(payload: bytes) -> str:
    """The manifest's documented self-hash, over its own bytes.

    Digest of the file with the 64 hex characters on the
    ``manifest_self_sha256`` line replaced by the literal placeholder.
    Exactly one such line must exist: zero means there is nothing to
    verify, and more than one makes "the" self-hash ambiguous.
    """
    replaced, count = _SELF_HASH_PATTERN.subn(rb"\1" + SELF_HASH_PLACEHOLDER + rb"\3", payload)
    if count != 1:
        raise PackManifestError(
            f"the fixture manifest must carry exactly one manifest_self_sha256 line; found {count}"
        )
    return hashlib.sha256(replaced).hexdigest()


class FrozenFixtureAssembler:
    """Verifies a frozen split fixture tree and assembles generic records.

    Order is fixed and total: self-hash, then the exact tree, then every
    byte, then — and only then — decoding. A tree whose bytes disagree
    with its inventory never reaches a YAML parser, which is what keeps
    "verified before decoded" a structural property rather than a habit.
    """

    def __init__(self, reader: PackFileReader, *, layout: FixtureLayout | None = None) -> None:
        self._reader = reader
        self._layout = layout if layout is not None else FixtureLayout()

    def load(self, *, fixture_directory: str) -> PackLoadResult:
        manifest_path = f"{fixture_directory}/{FIXTURE_MANIFEST_FILENAME}"
        manifest_bytes = self._reader.read_bytes(manifest_path)
        self_hash = compute_manifest_self_hash(manifest_bytes)

        manifest_doc = _yaml_mapping(manifest_bytes, relative_path=FIXTURE_MANIFEST_FILENAME)
        declared = _required_str(manifest_doc, "manifest_self_sha256")
        if declared != self_hash:
            raise PackManifestError(
                f"fixture manifest self-hash mismatch: declares {declared}, computes {self_hash}"
            )

        inventory = _inventory(manifest_doc)
        payloads = self._verify_tree(fixture_directory=fixture_directory, inventory=inventory)

        entries = tuple(
            sorted(
                (
                    ManifestEntry(
                        relative_path=relative_path,
                        sha256=inventory[relative_path],
                        size_bytes=len(payload),
                        media_type=_media_type_for(relative_path),
                    )
                    for relative_path, payload in payloads.items()
                ),
                key=lambda entry: entry.relative_path,
            )
        )

        objective_ref = _required_str(manifest_doc, "objective_ref")
        ObjectiveRef.parse(objective_ref)
        manifest = ObjectivePackManifest(
            pack_id=_required_str(manifest_doc, "manifest_id"),
            # The frozen tree's identity *is* its version. Binding it to
            # the self-hash means any byte change produces a different
            # pack version, which in turn invalidates every persisted
            # review decision through the subject digest — stale approvals
            # cannot survive a fixture edit.
            pack_version=self_hash,
            objective_ref=objective_ref,
            entries=entries,
            # An authoring file never states its own authority. Real
            # authority is an external reviewer decision pinned to this
            # exact manifest hash; see labs.ccna_mastery.gates.FixtureAuthority.
            fixture_status="draft_for_human_review",
        )

        pack_directory = self._resolve_pack_directory(inventory)
        sources_doc = _yaml_payload(payloads, self._layout.sources_document)
        claims_doc = _yaml_payload(payloads, f"{pack_directory}/{self._layout.claims_document}")
        baseline_doc = _yaml_payload(
            payloads, f"{pack_directory}/{self._layout.baseline_items_document}"
        )
        followup_doc = _yaml_payload(
            payloads, f"{pack_directory}/{self._layout.followup_items_document}"
        )
        exit_doc = _yaml_payload(
            payloads, f"{pack_directory}/{self._layout.exit_probe_items_document}"
        )
        visual_doc = _optional_yaml_payload(payloads, self._layout.visual_review_document)

        extensions = _extensions(
            manifest_doc=manifest_doc,
            sources_doc=sources_doc,
            claims_doc=claims_doc,
            visual_doc=visual_doc,
            payloads=payloads,
            inventory=inventory,
            objective_ref=objective_ref,
            pack_directory=pack_directory,
            baseline_doc=baseline_doc,
            followup_doc=followup_doc,
            exit_doc=exit_doc,
        )
        # Candidate scope for source currency: the pack's own objective
        # plus every objective any region cites. A source's declared
        # blueprints then *filter* this set — evidence can narrow a
        # source's currency but can never widen it past what the source
        # itself declares.
        candidate_refs = {objective_ref} | {
            ref
            for record in _mapping_list(claims_doc, "evidence_regions")
            for ref in _str_list(record, "objective_refs")
        }
        source_artifacts = _assemble_sources(
            sources_doc, extensions=extensions, candidate_objective_refs=candidate_refs
        )
        self._verify_source_bytes(
            payloads=payloads,
            inventory=inventory,
            artifacts=source_artifacts,
            extensions=extensions,
        )

        evidence_regions = _assemble_evidence(claims_doc, extensions=extensions)
        claims = _assemble_claims(claims_doc)
        items = (
            _assemble_items(baseline_doc, ExposureClass.BASELINE)
            + _assemble_items(followup_doc, ExposureClass.FOLLOWUP)
            + _assemble_items(exit_doc, ExposureClass.EXIT_PROBE)
        )

        pack = ObjectivePack(
            manifest=manifest,
            objective_ref=objective_ref,
            # The frozen tree declares no human-readable objective title.
            # Echoing the reference is honest; inventing prose here would
            # put unreviewed content into source code.
            objective_title=objective_ref,
            source_artifacts=source_artifacts,
            evidence_regions=evidence_regions,
            claims=claims,
            items=items,
            followup_rules=_assemble_followup_rules(followup_doc),
            mastery_policy=_assemble_policy(
                manifest_doc=manifest_doc,
                baseline_doc=baseline_doc,
                followup_doc=followup_doc,
                exit_doc=exit_doc,
            ),
            baseline_item_ids=_item_ids(baseline_doc),
            exit_probe_item_ids=_item_ids(exit_doc),
            required_claim_ids=tuple(sorted(claim.claim_id for claim in claims)),
            # The plan's own declared counts, carried through so the
            # validator has something real to disagree with. Only counts
            # the fixture states as counts appear here — a bank's
            # ``maximum_items`` is a bound, and passing it off as a
            # declared total would make the comparison accidentally true.
            declared_coverage={"baseline_items": _positive_int(baseline_doc, "item_count")},
            review_state=ReviewState.PENDING,
        )

        entry_by_path = {entry.relative_path: entry for entry in entries}
        bindings = {
            artifact.source_id: SourceManifestBinding(
                source_id=artifact.source_id,
                relative_path=path,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                media_type=entry_by_path[path].media_type,
            )
            for artifact, path in (
                (artifact, _path_for_hash(inventory, artifact.sha256, artifact.source_id))
                for artifact in source_artifacts
            )
        }

        return PackLoadResult(
            pack=pack,
            manifest=manifest,
            verified_file_hashes=dict(inventory),
            source_manifest_bindings=bindings,
            fixture_manifest_hash=self_hash,
            retrieval_cases=_retrieval_cases(
                payloads[self._layout.retrieval_cases_document], objective_ref=objective_ref
            ),
            fixture_extensions=extensions,
        )

    # ---- verification -----------------------------------------------------

    def _verify_tree(
        self, *, fixture_directory: str, inventory: dict[str, str]
    ) -> dict[str, bytes]:
        """Exact-tree admission, then byte verification of every listed file."""
        expected = set(inventory) | {FIXTURE_MANIFEST_FILENAME}
        present = set(self._reader.relative_files_under(fixture_directory))
        if present != expected:
            missing = sorted(expected - present)
            unlisted = sorted(present - expected)
            raise PackManifestError(
                "the fixture tree differs from its frozen inventory: "
                f"missing={missing}, unlisted={unlisted}"
            )

        payloads: dict[str, bytes] = {}
        for relative_path in sorted(inventory):
            rooted = f"{fixture_directory}/{relative_path}"
            # A symlink is refused outright rather than resolved. The
            # reader's containment check already blocks one pointing
            # outside a root; this additionally blocks one pointing at a
            # different file *inside* it, which would let two inventory
            # entries silently describe one set of bytes.
            if self._reader.resolve(rooted).is_symlink():
                raise PackManifestError(
                    f"a frozen fixture path must be a regular file, not a symlink: {relative_path}"
                )
            payloads[relative_path] = self._reader.read_bytes(
                rooted, expected_sha256=inventory[relative_path]
            )
        return payloads

    def _verify_source_bytes(
        self,
        *,
        payloads: dict[str, bytes],
        inventory: dict[str, str],
        artifacts: tuple[SourceArtifactRef, ...],
        extensions: FixtureExtensions,
    ) -> None:
        """Every declared source's size and sniffed media type, against its bytes.

        The manifest pins hashes; the extension envelope pins sizes and
        magic bytes. Both are checked against the file that actually
        matched the hash, so a source cannot declare one media type and
        carry another.
        """
        for artifact in artifacts:
            path = _path_for_hash(inventory, artifact.sha256, artifact.source_id)
            payload = payloads[path]
            if len(payload) != artifact.size_bytes:
                raise PackManifestError(
                    f"source {artifact.source_id!r} declares {artifact.size_bytes} bytes "
                    f"but {path} holds {len(payload)}"
                )
            sniffed = detect_media_type(payload)
            if sniffed != artifact.media_type:
                raise PackManifestError(
                    f"source {artifact.source_id!r} declares media type "
                    f"{artifact.media_type!r} but its bytes sniff as {sniffed!r}"
                )
            magic = extensions.source_magic_bytes.get(artifact.source_id)
            if magic is not None and not payload.hex().startswith(magic.lower()):
                raise PackManifestError(
                    f"source {artifact.source_id!r} does not begin with its declared magic bytes"
                )

    def _resolve_pack_directory(self, inventory: dict[str, str]) -> str:
        """The single pack directory in the tree, discovered from the inventory.

        Discovered rather than configured so the adapter cannot be pointed
        at a directory the manifest does not pin. Exactly one must exist:
        two would make "the pack" ambiguous, and zero means the tree is
        not a pack at all.
        """
        prefix = self._layout.pack_directory_prefix
        marker = f"/{self._layout.claims_document}"
        directories = sorted(
            path[: -len(marker)]
            for path in inventory
            if path.startswith(prefix) and path.endswith(marker)
        )
        if len(directories) != 1:
            raise PackSchemaError(
                f"the fixture must pin exactly one {prefix}*{marker} document; "
                f"found {len(directories)}"
            )
        return directories[0]


def load_frozen_fixture(
    reader: PackFileReader, *, fixture_directory: str, layout: FixtureLayout | None = None
) -> PackLoadResult:
    """Load one frozen split fixture tree. The explicit entry point.

    Deliberately a separate call rather than something
    ``ObjectivePackLoader.load`` sniffs for. An earlier revision dispatched
    to this adapter whenever a ``fixture-manifest.yaml`` happened to sit in
    the directory, which made the generic loader's behaviour depend on a
    filename and put fixture-specific handling inside shared code. A caller
    that wants this format now says so.
    """
    return FrozenFixtureAssembler(reader, layout=layout).load(fixture_directory=fixture_directory)


# ---- assembly -------------------------------------------------------------


def _assemble_sources(
    sources_doc: dict[str, Any],
    *,
    extensions: FixtureExtensions,
    candidate_objective_refs: set[str],
) -> tuple[SourceArtifactRef, ...]:
    """Translate source registrations into strict artifact records.

    Rights and permitted uses are authored *facts* about provenance and
    carry through. Review and trust are not: every source lands
    ``PENDING``/``UNTRUSTED``, and only a quarantine claim is honoured,
    because that direction can only remove eligibility.
    """
    assembled: list[SourceArtifactRef] = []
    for record in _mapping_list(sources_doc, "sources"):
        source_id = _required_str(record, "source_id")
        status = _required_str(record, "status")
        if status not in (_REGISTERED, _QUARANTINED):
            raise PackSchemaError(
                f"source {source_id!r} declares unsupported status {status!r}; "
                "an unrecognized state is refused rather than defaulted"
            )
        quarantined = status == _QUARANTINED

        size = extensions.source_sizes.get(source_id)
        if size is None:
            raise PackSchemaError(
                f"source {source_id!r} has no declared file_size_bytes in the "
                "fixture-extension envelope"
            )

        rights_basis = _required_str(record, "rights_basis")
        rights = _RIGHTS_BASIS.get(rights_basis, SourceRightsStatus.UNKNOWN)
        if quarantined:
            # A quarantined source grants nothing regardless of its basis.
            rights = SourceRightsStatus.RESTRICTED

        permitted = frozenset(PermittedUse(value) for value in _str_list(record, "permitted_uses"))
        if quarantined and permitted:
            raise PackSchemaError(
                f"quarantined source {source_id!r} must declare no permitted_uses"
            )

        assembled.append(
            SourceArtifactRef(
                source_id=source_id,
                sha256=_sha256(record, "sha256", subject=source_id),
                media_type=cast(Any, _required_str(record, "media_type")),
                size_bytes=size,
                title=_required_str(record, "title"),
                publisher=_optional_str(record, "publisher"),
                edition_or_version=_optional_str(record, "edition_or_version"),
                attribution_text=_optional_str(record, "attribution_text"),
                rights_status=rights,
                permitted_uses=permitted,
                trust_status=TrustStatus.UNTRUSTED,
                quarantine_status=(
                    QuarantineStatus.QUARANTINED if quarantined else QuarantineStatus.CLEAR
                ),
                review_state=ReviewState.REJECTED if quarantined else ReviewState.PENDING,
                current_for_objective_refs=_currency_for(
                    record, candidate_objective_refs=candidate_objective_refs
                ),
            )
        )
    return tuple(assembled)


def _currency_for(record: dict[str, Any], *, candidate_objective_refs: set[str]) -> tuple[str, ...]:
    """Objective refs a source is authoritative for.

    Two inputs meet here, and the direction matters. The *candidates* are
    objective versions already in scope for the pack. The source's own
    ``current_for_blueprints`` then filters them: a candidate survives only
    if its exam code and blueprint version match a blueprint the source
    declares.

    An earlier revision derived currency by unioning the objective refs of
    the *regions* drawn from a source, which pointed the authority arrow
    backwards — a region could nominate its own source as current for
    whatever scope the region claimed. Here a region can only ever narrow.

    A blueprint entry such as ``"200-301-v1.1"`` omits the vendor prefix a
    full reference carries, so matching accepts either the whole prefix or
    a ``-``-delimited suffix of it. Suffix matching is anchored on that
    separator, so ``200-301-v2.0`` can never match a ``v1.1`` reference.
    """
    blueprints = _str_list(record, "current_for_blueprints")
    if not blueprints:
        return ()

    refs: list[str] = []
    for candidate in sorted(candidate_objective_refs):
        parsed = ObjectiveRef.parse(candidate)
        prefix = f"{parsed.exam_code}-v{parsed.blueprint_version}"
        if any(prefix == blueprint or prefix.endswith(f"-{blueprint}") for blueprint in blueprints):
            refs.append(candidate)
    return tuple(sorted(set(refs)))


def _assemble_evidence(
    claims_doc: dict[str, Any], *, extensions: FixtureExtensions
) -> tuple[EvidenceRegion, ...]:
    """Translate evidence regions, clamping every authored review claim.

    The mapping, in full:

    ==================  ====================  =================  =============
    authored            review_state          trust_status       quarantine
    ==================  ====================  =================  =============
    ``rejected``        ``REJECTED``          ``UNTRUSTED``      ``QUARANTINED``
    ``pending``         ``PENDING``           ``UNTRUSTED``      ``CLEAR``
    ``approved``        ``PENDING``           ``UNTRUSTED``      ``CLEAR``
    ==================  ====================  =================  =============

    ``approved`` and ``pending`` land in the same place on purpose. What
    an authoring file says about its own review status is a claim; a
    persisted reviewer decision is authority.
    """
    regions: list[EvidenceRegion] = []
    for record in _mapping_list(claims_doc, "evidence_regions"):
        evidence_id = _required_str(record, "evidence_id")
        selector_doc = _mapping(record, "selector", subject=evidence_id)
        exact_text = _optional_str(record, "exact_text")
        description = _optional_str(record, "accessible_description")
        selector_kind = _required_str(selector_doc, "type")

        selector: PageTextSelector | ImageRegionSelector
        if selector_kind == "page_text":
            # Page-scoped: the frozen selector pins the page, not offsets
            # into some parser's output. The extractor resolves the exact
            # characters from the source bytes at extraction time.
            selector = PageTextSelector(page_number=_positive_int(selector_doc, "page"))
            content_sha256 = _sha256(record, "content_sha256", subject=evidence_id)
            if exact_text is None:
                raise PackSchemaError(f"text region {evidence_id!r} carries no exact_text")
            recomputed = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
            if recomputed != content_sha256:
                raise PackManifestError(
                    f"text region {evidence_id!r} declares content_sha256 {content_sha256} "
                    f"but its text digests to {recomputed}"
                )
        elif selector_kind == "image_region":
            bbox = _bbox(selector_doc, subject=evidence_id)
            selector = ImageRegionSelector(
                page_number=_positive_int(selector_doc, "page"),
                image_sha256=_sha256(selector_doc, "image_hash", subject=evidence_id),
                left_basis_points=_basis_points(bbox[0]),
                top_basis_points=_basis_points(bbox[1]),
                right_basis_points=_basis_points(bbox[2]),
                bottom_basis_points=_basis_points(bbox[3]),
                figure_label=_optional_str(selector_doc, "figure_label"),
            )
            if description is None:
                raise PackSchemaError(
                    f"image region {evidence_id!r} carries no accessible_description"
                )
            # The fixture's image content_sha256 is a *pixel* hash under
            # the image-region-rgb-v1 scheme, which is a different thing
            # from the domain's content digest. It is kept in the
            # extension envelope and verified against real decoded pixels
            # by the extractor; the domain record carries the digest the
            # domain actually defines.
            content_sha256 = hashlib.sha256(
                f"description:{description}\ncaption:{exact_text or ''}".encode()
            ).hexdigest()
        else:
            raise PackSchemaError(
                f"region {evidence_id!r} declares unknown selector {selector_kind!r}"
            )

        authored_state = _required_str(record, "reviewer_status")
        if authored_state not in {state.value for state in ReviewState}:
            raise PackSchemaError(
                f"region {evidence_id!r} declares unsupported reviewer_status {authored_state!r}"
            )
        rejected = authored_state == ReviewState.REJECTED.value

        regions.append(
            EvidenceRegion(
                evidence_id=evidence_id,
                source_id=_required_str(record, "source_id"),
                selector=selector,
                exact_text=exact_text,
                accessible_description=description,
                content_sha256=content_sha256,
                extraction=ExtractionMetadata(
                    method=_required_str(record, "extraction_method"),
                    extractor_version=_FIXTURE_EXTRACTOR_VERSION,
                    extracted_at=_FIXTURE_EXTRACTED_AT,
                    confidence_basis_points=_basis_points(record.get("extraction_confidence", "0")),
                    is_ocr=False,
                ),
                objective_refs=tuple(_str_list(record, "objective_refs")),
                concept_tags=tuple(_str_list(record, "concept_tags")),
                trust_status=TrustStatus.UNTRUSTED,
                quarantine_status=(
                    QuarantineStatus.QUARANTINED if rejected else QuarantineStatus.CLEAR
                ),
                review_state=ReviewState.REJECTED if rejected else ReviewState.PENDING,
            )
        )
    return tuple(regions)


def _assemble_claims(claims_doc: dict[str, Any]) -> tuple[ApprovedClaim, ...]:
    """Translate claims, honouring only their authored *technical* approval.

    A claim's ``approval`` is a recorded technical-content decision about
    the assertion itself, which is a different subject from whether its
    supporting evidence may be retrieved. It carries through to
    ``review_state`` so the pending/approved split stays visible; it grants
    no evidence eligibility, because eligibility is decided entirely by
    ``EvidencePolicy`` against persisted evidence decisions.

    ``is_answer_bearing`` is deliberately left ``False``. The pack derives
    the answer-bearing set from item references — see
    ``ObjectivePack.answer_bearing_claim_ids`` — and asserting the flag
    here would substitute an authored claim for that recomputation.
    """
    claims: list[ApprovedClaim] = []
    for record in _mapping_list(claims_doc, "claims"):
        claim_id = _required_str(record, "claim_id")
        supports = tuple(
            ClaimSupport(
                support_id=_required_str(support, "support_id"),
                evidence_id=_required_str(support, "evidence_id"),
                relationship=cast(Any, _required_str(support, "relationship")),
                authority_basis_points=_basis_points(support["authority"]),
                directness_basis_points=_basis_points(support["directness"]),
                provenance_completeness_basis_points=_basis_points(
                    support["provenance_completeness"]
                ),
                extraction_integrity_basis_points=_basis_points(support["extraction_integrity"]),
                fitness_and_currency_basis_points=_basis_points(support["fitness_and_currency"]),
                independence_group=_required_str(support, "independence_group"),
                calculation_policy_version=_required_str(support, "calculation_policy_version"),
            )
            for support in _mapping_list(record, "support")
        )
        claims.append(
            ApprovedClaim(
                claim_id=claim_id,
                canonical_text=_required_str(record, "canonical_text"),
                objective_ref=_required_str(record, "objective_ref"),
                facet=ObjectiveFacet(_required_str(record, "facet")),
                support=supports,
                declared_grounding_score_basis_points=_percent_basis_points(
                    record["grounding_score"]
                ),
                conflict_status=cast(Any, record.get("conflict_status", "clear")),
                review_state=(
                    ReviewState.APPROVED
                    if record.get("approval") == ReviewState.APPROVED.value
                    else ReviewState.PENDING
                ),
                is_answer_bearing=False,
            )
        )
    return tuple(claims)


def _assemble_items(
    document: dict[str, Any], exposure: ExposureClass
) -> tuple[AssessmentItem, ...]:
    """Translate one item bank. Only an explicit approval is carried through."""
    version = _positive_int(document, "plan_version", default=document.get("bank_version", 1))
    objective_ref = _required_str(document, "objective_ref")
    items: list[AssessmentItem] = []
    for record in _mapping_list(document, "items"):
        grade_definition = {
            key: record[key]
            for key in ("answer_key", "rubric", "steps", "points_possible")
            if key in record
        }
        canonical = json.dumps(
            grade_definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        review = record.get("technical_review")
        approved = isinstance(review, dict) and review.get("status") == ReviewState.APPROVED.value
        items.append(
            AssessmentItem(
                item_id=_required_str(record, "item_id"),
                item_version=version,
                objective_ref=objective_ref,
                exposure_class=exposure,
                prompt=_required_str(record, "prompt"),
                answer_key_sha256=hashlib.sha256(canonical).hexdigest(),
                claim_ids=tuple(_str_list(record, "claim_ids")),
                facet_weights=_facet_weights(record),
                misconception_tags=tuple(_str_list(record, "misconception_tags")),
                difficulty=cast(Any, record.get("difficulty", "foundation")),
                review_state=ReviewState.APPROVED if approved else ReviewState.PENDING,
            )
        )
    return tuple(items)


def _assemble_followup_rules(followup_doc: dict[str, Any]) -> tuple[FollowUpRule, ...]:
    """One deterministic misconception-to-item rule per follow-up item."""
    by_trigger: dict[str, list[str]] = {}
    for record in _mapping_list(followup_doc, "items"):
        by_trigger.setdefault(_required_str(record, "trigger"), []).append(
            _required_str(record, "item_id")
        )
    policy_version = f"{_required_str(followup_doc, 'exposure_class')}-mapping-v{
        _positive_int(followup_doc, 'bank_version')
    }"
    return tuple(
        FollowUpRule(
            rule_id=f"followup-{trigger}",
            misconception_tag=trigger,
            followup_item_ids=tuple(item_ids),
            mapping_policy_version=policy_version,
        )
        for trigger, item_ids in sorted(by_trigger.items())
    )


def _assemble_policy(
    *,
    manifest_doc: dict[str, Any],
    baseline_doc: dict[str, Any],
    followup_doc: dict[str, Any],
    exit_doc: dict[str, Any],
) -> MasteryPolicy:
    """The pack's mastery policy, from frozen counts and declared versions.

    ``required_facets`` comes from the item plan's declared per-facet
    minimums, not from the facets the items happen to carry. Deriving the
    requirement from the items would make
    ``validate_required_facet_coverage`` compare a set against itself and
    pass for any bank at all.

    ``baseline_item_count`` is likewise the *declared* plan count rather
    than the number of item records, because it is the value
    ``validate_baseline_cardinality`` checks the actual bank against.
    """
    versions = _mapping(manifest_doc, "versions", subject="fixture manifest")
    coverage = _mapping(
        baseline_doc, "coverage_assertions_recomputed", subject="baseline item plan"
    )
    minimums = _mapping(
        coverage, "every_facet_minimum_items", subject="baseline coverage assertions"
    )
    return MasteryPolicy(
        policy_id=f"{_required_str(baseline_doc, 'plan_id')}-mastery",
        policy_version=_required_str(versions, "mastery_policy_version"),
        baseline_item_count=_positive_int(baseline_doc, "item_count"),
        maximum_followup_items=_positive_int(followup_doc, "maximum_items"),
        exit_probe_item_count=len(_item_ids(exit_doc)),
        required_facets=frozenset(ObjectiveFacet(str(name)) for name in minimums),
        minimum_claim_grounding_basis_points=8_500,
    )


# ---- extension envelope ---------------------------------------------------


def _extensions(
    *,
    manifest_doc: dict[str, Any],
    sources_doc: dict[str, Any],
    claims_doc: dict[str, Any],
    visual_doc: dict[str, Any] | None,
    payloads: dict[str, bytes],
    inventory: dict[str, str],
    objective_ref: str,
    pack_directory: str,
    baseline_doc: dict[str, Any],
    followup_doc: dict[str, Any],
    exit_doc: dict[str, Any],
) -> FixtureExtensions:
    """Collect the authoring metadata the strict records deliberately exclude."""
    sizes: dict[str, int] = {}
    magic: dict[str, str] = {}
    dimensions: dict[str, tuple[int, int]] = {}
    for record in _mapping_list(sources_doc, "fixture_ext_sources"):
        source_id = _required_str(record, "source_id")
        sizes[source_id] = _positive_int(record, "file_size_bytes")
        magic_hex = _optional_str(record, "magic_bytes_hex")
        if magic_hex is not None:
            magic[source_id] = magic_hex
        pixels = record.get("pixel_dimensions")
        if isinstance(pixels, list) and len(pixels) == 2:
            dimensions[source_id] = (int(pixels[0]), int(pixels[1]))

    pixel_hashes: dict[str, str] = {}
    for record in _mapping_list(claims_doc, "evidence_regions"):
        selector = record.get("selector")
        if isinstance(selector, dict) and selector.get("type") == "image_region":
            pixel_hashes[_required_str(record, "evidence_id")] = _sha256(
                record, "content_sha256", subject="image region"
            )

    boxes: dict[str, tuple[int, int, int, int]] = {}
    decisions: list[dict[str, str]] = []
    if visual_doc is not None:
        for record in _mapping_list(visual_doc, "reviews"):
            evidence_id = _required_str(record, "evidence_id")
            box = record.get("pixel_box")
            if isinstance(box, list) and len(box) == 4:
                boxes[evidence_id] = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
            reviewer = record.get("human_reviewer_id")
            if isinstance(reviewer, str) and reviewer:
                decisions.append(
                    {
                        "evidence_id": evidence_id,
                        "human_reviewer_id": reviewer,
                        "human_decision": str(record.get("human_decision", "")),
                        "human_reviewed_on": str(record.get("human_reviewed_on", "")),
                    }
                )

    manifest_versions = _manifest_versions(manifest_doc)
    scenario_context = _scenario_semantic_context(
        payloads=payloads,
        inventory=inventory,
        objective_ref=objective_ref,
        pack_directory=pack_directory,
        manifest_versions=manifest_versions,
    )
    learner_pins = _scripted_learner_pins(
        manifest_doc=manifest_doc,
        payloads=payloads,
        inventory=inventory,
        objective_ref=objective_ref,
        baseline_doc=baseline_doc,
        followup_doc=followup_doc,
        exit_doc=exit_doc,
        scenario_context=scenario_context,
    )
    profile_pins, hosted_profiles, hosted_spend = _execution_profile_pins(manifest_doc)

    canonicalization_section = manifest_doc.get("canonicalization_rules")
    canonicalization_notes = (
        {
            key: str(value)
            for key, value in canonicalization_section.items()
            if key in ("cli_state_hash", "event_stream_hash")
        }
        if isinstance(canonicalization_section, dict)
        else {}
    )

    ledger_section = manifest_doc.get("focused_time_ledger_contract")
    ledger_document = dict(ledger_section) if isinstance(ledger_section, dict) else None

    return FixtureExtensions(
        source_sizes=sizes,
        source_magic_bytes=magic,
        image_dimensions=dimensions,
        region_pixel_sha256=pixel_hashes,
        region_pixel_boxes=boxes,
        recorded_human_decisions=tuple(decisions),
        manifest_versions=manifest_versions,
        manifest_canonicalization_notes=canonicalization_notes,
        scripted_learner_pins=learner_pins,
        scenario_state_hash_pins=scenario_context.pins,
        allowed_profile_provider_pins=profile_pins,
        hosted_profiles_enabled=hosted_profiles,
        hosted_spend_ceiling_usd=hosted_spend,
        focused_time_ledger_document=ledger_document,
    )


def _manifest_versions(manifest_doc: dict[str, Any]) -> dict[str, str]:
    """Parse every required version pin without accepting lossy scalar coercions."""
    section = _mapping(manifest_doc, "versions", subject="fixture manifest")
    missing = sorted(_REQUIRED_MANIFEST_VERSION_KEYS - set(section))
    if missing:
        raise PackSchemaError(f"fixture manifest versions missing required key(s): {missing}")

    parsed: dict[str, str] = {}
    for key, value in section.items():
        if not isinstance(key, str) or not key:
            raise PackSchemaError("fixture manifest version names must be non-empty strings")
        if isinstance(value, str):
            if not value or value != value.strip():
                raise PackSchemaError(f"fixture manifest version {key!r} must be a clean value")
            parsed[key] = value
        elif isinstance(value, int) and not isinstance(value, bool) and value > 0:
            parsed[key] = str(value)
        else:
            raise PackSchemaError(
                f"fixture manifest version {key!r} must be a non-empty string or positive integer"
            )
    return parsed


def _scenario_semantic_context(
    *,
    payloads: dict[str, bytes],
    inventory: dict[str, str],
    objective_ref: str,
    pack_directory: str,
    manifest_versions: dict[str, str],
) -> _ScenarioSemanticContext:
    """Load the one frozen scenario and expose its full, executed state pins."""
    prefix = f"{pack_directory}/scenario-"
    paths = sorted(
        path
        for path in inventory
        if path.startswith(prefix) and path.lower().endswith((".yaml", ".yml"))
    )
    if len(paths) != 1:
        raise PackManifestError(
            "the frozen fixture must pin exactly one scenario document under "
            f"{pack_directory!r}; found {paths}"
        )
    relative_path = paths[0]
    scenario_doc = _yaml_payload(payloads, relative_path)
    scenario = _mapping(scenario_doc, "scenario", subject=relative_path)

    scenario_objective = _required_str(scenario, "objective_ref")
    if scenario_objective != objective_ref:
        raise PackManifestError(
            f"{relative_path} declares objective {scenario_objective!r}, "
            f"not manifest objective {objective_ref!r}"
        )
    scenario_version = _required_str(scenario, "version")
    if scenario_version != manifest_versions["scenario_version"]:
        raise PackManifestError(
            f"{relative_path} version {scenario_version!r} disagrees with manifest "
            f"scenario_version {manifest_versions['scenario_version']!r}"
        )

    starting_hash = _sha256(scenario, "starting_state_sha256", subject=relative_path)
    target_hash = _sha256(scenario, "target_repaired_state_sha256", subject=relative_path)
    if starting_hash == target_hash:
        raise PackManifestError("scenario starting and target state hashes must differ")
    initial_state = _mapping(scenario_doc, "initial_state", subject=relative_path)
    computed_starting_hash = _canonical_json_sha256(initial_state)
    if computed_starting_hash != starting_hash:
        raise PackManifestError(
            f"{relative_path} starting-state pin {starting_hash} does not match its "
            f"canonical initial_state hash {computed_starting_hash}"
        )

    scenario_ref = _required_str(scenario, "scenario_ref")
    if not scenario_ref.endswith(f"@{scenario_version}"):
        raise PackManifestError(
            f"{relative_path} scenario_ref {scenario_ref!r} does not carry "
            f"version {scenario_version!r}"
        )

    equivalence_case_ids: list[str] = []
    for record in _mapping_list(scenario_doc, "equivalence_cases"):
        case_id = _required_str(record, "case_id")
        equivalence_case_ids.append(case_id)
        observed = _sha256(
            record,
            "expected_final_state_sha256",
            subject=f"scenario equivalence case {case_id!r}",
        )
        if observed != target_hash:
            raise PackManifestError(
                f"scenario equivalence case {case_id!r} targets {observed}, "
                f"not the scenario target {target_hash}"
            )
        if not _required_bool(record, "executed"):
            raise PackManifestError(
                f"scenario equivalence case {case_id!r} is not recorded as executed"
            )
        if not _required_bool(record, "verification_policy_satisfied"):
            raise PackManifestError(
                f"scenario equivalence case {case_id!r} did not satisfy verification policy"
            )
    _ensure_unique(equivalence_case_ids, subject="scenario equivalence case ids")
    if not equivalence_case_ids:
        raise PackSchemaError("equivalence_cases must contain at least one record")

    hint_ids = [_required_str(record, "hint_id") for record in _mapping_list(scenario_doc, "hints")]
    _ensure_unique(hint_ids, subject="scenario hint ids")

    grading = _mapping(scenario_doc, "grading", subject=relative_path)
    weights = _mapping(grading, "weights", subject="scenario grading")
    grade_weights: list[tuple[str, int]] = []
    for name, value in weights.items():
        if not isinstance(name, str) or not name:
            raise PackSchemaError("scenario grading weight names must be non-empty strings")
        grade_weights.append((name, _positive_int_value(value, subject=f"grade weight {name}")))
    if not grade_weights or sum(value for _, value in grade_weights) != 100:
        raise PackSchemaError("scenario grading weights must be non-empty and sum to 100")

    return _ScenarioSemanticContext(
        pins=ScenarioStateHashPins(
            relative_path=relative_path,
            raw_sha256=inventory[relative_path],
            scenario_ref=scenario_ref,
            scenario_version=scenario_version,
            objective_ref=scenario_objective,
            starting_state_sha256=starting_hash,
            target_repaired_state_sha256=target_hash,
        ),
        equivalence_case_ids=tuple(equivalence_case_ids),
        hint_ids=tuple(hint_ids),
        grade_weights=tuple(grade_weights),
    )


def _execution_profile_pins(
    manifest_doc: dict[str, Any],
) -> tuple[tuple[AllowedProfileProviderPin, ...], tuple[str, ...], str]:
    """Parse the frozen offline execution boundary as typed combinations."""
    records = _mapping_list(manifest_doc, "allowed_profile_provider_combinations")
    if not records:
        raise PackSchemaError("allowed_profile_provider_combinations must not be empty")

    pins: list[AllowedProfileProviderPin] = []
    profiles: list[str] = []
    for record in records:
        profile = _required_str(record, "profile")
        profiles.append(profile)
        provider_ids = _required_str_list(record, "provider_ids", allow_empty=False)
        _ensure_unique(provider_ids, subject=f"provider ids for profile {profile!r}")
        offline_only = _required_bool(record, "offline_only")
        if not offline_only:
            raise PackManifestError(
                f"profile {profile!r} contradicts the P0 offline-only execution boundary"
            )
        pins.append(
            AllowedProfileProviderPin(
                profile=profile,
                provider_ids=tuple(provider_ids),
                offline_only=offline_only,
                allow_domain_result_writes=_optional_bool(record, "allow_domain_result_writes"),
            )
        )
    _ensure_unique(profiles, subject="allowed execution profiles")

    hosted_profiles = _required_str_list(
        manifest_doc, "hosted_profiles_enabled_for_this_freeze", allow_empty=True
    )
    _ensure_unique(hosted_profiles, subject="hosted profiles enabled for this freeze")
    spend = _required_str(manifest_doc, "hosted_spend_ceiling_usd")
    if not re.fullmatch(r"[0-9]+\.[0-9]{2}", spend):
        raise PackSchemaError("hosted_spend_ceiling_usd must be a non-negative USD string")
    amount = Decimal(spend)
    if hosted_profiles or amount != Decimal("0.00"):
        raise PackManifestError(
            "the frozen P0 fixture must keep hosted profiles disabled and hosted spend at 0.00"
        )
    return tuple(pins), tuple(hosted_profiles), spend


def _scripted_learner_pins(
    *,
    manifest_doc: dict[str, Any],
    payloads: dict[str, bytes],
    inventory: dict[str, str],
    objective_ref: str,
    baseline_doc: dict[str, Any],
    followup_doc: dict[str, Any],
    exit_doc: dict[str, Any],
    scenario_context: _ScenarioSemanticContext,
) -> tuple[ScriptedLearnerPin, ...]:
    """Parse and cross-check the four byte-verified P0 learner vectors."""
    summaries = _mapping_list(manifest_doc, "scripted_learners")
    if len(summaries) != _SCRIPTED_LEARNER_COUNT:
        raise PackManifestError(
            f"the P0 fixture must pin exactly {_SCRIPTED_LEARNER_COUNT} scripted learners; "
            f"found {len(summaries)}"
        )

    baseline_item_ids = _item_ids(baseline_doc)
    exit_probe_item_ids = _item_ids(exit_doc)
    _ensure_unique(baseline_item_ids, subject="baseline item ids")
    _ensure_unique(exit_probe_item_ids, subject="exit-probe item ids")

    followup_trigger_by_item: dict[str, str] = {}
    for record in _mapping_list(followup_doc, "items"):
        item_id = _required_str(record, "item_id")
        if item_id in followup_trigger_by_item:
            raise PackManifestError(f"duplicate follow-up item id: {item_id!r}")
        followup_trigger_by_item[item_id] = _required_str(record, "trigger")

    pins: list[ScriptedLearnerPin] = []
    for summary in summaries:
        relative_path = _required_str(summary, "file")
        _validate_clean_relative_path(relative_path, subject="scripted learner file")
        if not relative_path.startswith("learners/") or not relative_path.endswith(".json"):
            raise PackManifestError(
                f"scripted learner file must be a learners/*.json path: {relative_path!r}"
            )
        if relative_path not in inventory or relative_path not in payloads:
            raise PackManifestError(
                f"scripted learner file is not pinned by the fixture inventory: {relative_path!r}"
            )
        learner_doc = _json_mapping(payloads[relative_path], relative_path=relative_path)
        pins.append(
            _scripted_learner_pin(
                summary=summary,
                learner_doc=learner_doc,
                relative_path=relative_path,
                raw_sha256=inventory[relative_path],
                objective_ref=objective_ref,
                baseline_item_ids=baseline_item_ids,
                followup_trigger_by_item=followup_trigger_by_item,
                exit_probe_item_ids=exit_probe_item_ids,
                scenario_context=scenario_context,
            )
        )

    learner_ids = [pin.learner_vector_id for pin in pins]
    learner_paths = [pin.relative_path for pin in pins]
    _ensure_unique(learner_ids, subject="scripted learner vector ids")
    _ensure_unique(learner_paths, subject="scripted learner files")
    inventory_learner_paths = sorted(
        path for path in inventory if path.startswith("learners/") and path.endswith(".json")
    )
    if sorted(learner_paths) != inventory_learner_paths:
        raise PackManifestError(
            "manifest scripted learner summaries must reference every and only "
            f"inventory learner JSON path; summaries={sorted(learner_paths)}, "
            f"inventory={inventory_learner_paths}"
        )

    by_id = {pin.learner_vector_id: pin for pin in pins}
    for pin in pins:
        target_id = pin.must_equal_learner_vector_id
        if target_id is None:
            continue
        if target_id == pin.learner_vector_id or target_id not in by_id:
            raise PackManifestError(
                f"learner {pin.learner_vector_id!r} has invalid must_equal target {target_id!r}"
            )
        if scripted_learner_authority_projection(pin) != scripted_learner_authority_projection(
            by_id[target_id]
        ):
            raise PackManifestError(
                f"learner {pin.learner_vector_id!r} authority projection drifts from "
                f"must_equal target {target_id!r}"
            )
    return tuple(pins)


def _scripted_learner_pin(
    *,
    summary: dict[str, Any],
    learner_doc: dict[str, Any],
    relative_path: str,
    raw_sha256: str,
    objective_ref: str,
    baseline_item_ids: tuple[str, ...],
    followup_trigger_by_item: dict[str, str],
    exit_probe_item_ids: tuple[str, ...],
    scenario_context: _ScenarioSemanticContext,
) -> ScriptedLearnerPin:
    learner_id = _required_str(learner_doc, "learner_vector_id")
    summary_id = _required_str(summary, "learner_vector_id")
    if learner_id != summary_id:
        raise PackManifestError(
            f"manifest learner id {summary_id!r} disagrees with {relative_path} id {learner_id!r}"
        )
    learner_objective = _required_str(learner_doc, "objective_ref")
    if learner_objective != objective_ref:
        raise PackManifestError(
            f"learner {learner_id!r} declares objective {learner_objective!r}, "
            f"not {objective_ref!r}"
        )

    baseline_responses = _response_pins(
        _mapping_list(learner_doc, "baseline_responses"),
        subject=f"learner {learner_id!r} baseline",
        require_confidence=True,
        require_disposition=True,
    )
    if tuple(response.item_id for response in baseline_responses) != baseline_item_ids:
        raise PackManifestError(
            f"learner {learner_id!r} baseline response ids/order do not exactly match the bank"
        )

    selection = _mapping(
        learner_doc,
        "expected_followup_selection",
        subject=f"learner {learner_id!r}",
    )
    trigger_codes = _required_str_list(selection, "trigger_codes", allow_empty=True)
    followup_item_ids = _required_str_list(selection, "approved_item_ids", allow_empty=True)
    _ensure_unique(trigger_codes, subject=f"learner {learner_id!r} follow-up trigger codes")
    _ensure_unique(followup_item_ids, subject=f"learner {learner_id!r} follow-up item ids")
    try:
        selected_triggers = tuple(
            followup_trigger_by_item[item_id] for item_id in followup_item_ids
        )
    except KeyError as exc:
        raise PackManifestError(
            f"learner {learner_id!r} selects unknown follow-up item {exc.args[0]!r}"
        ) from exc
    if selected_triggers != tuple(trigger_codes):
        raise PackManifestError(
            f"learner {learner_id!r} follow-up items do not exactly implement its trigger codes"
        )

    finding_tags = [
        _required_str(record, "misconception_tag")
        for record in _mapping_list(learner_doc, "expected_evaluator_findings")
    ]
    _ensure_unique(finding_tags, subject=f"learner {learner_id!r} evaluator finding tags")
    if tuple(finding_tags) != tuple(trigger_codes):
        raise PackManifestError(
            f"learner {learner_id!r} evaluator findings drift from follow-up trigger codes"
        )

    followup_responses = _response_pins(
        _optional_mapping_list(learner_doc, "followup_responses"),
        subject=f"learner {learner_id!r} follow-up",
        require_confidence=False,
        require_disposition=False,
    )
    if tuple(response.item_id for response in followup_responses) != tuple(followup_item_ids):
        raise PackManifestError(
            f"learner {learner_id!r} follow-up response ids/order drift from selection"
        )

    cli = _mapping(learner_doc, "cli_attempt", subject=f"learner {learner_id!r}")
    equivalence_case_ref = _required_str(cli, "equivalence_case_ref")
    if equivalence_case_ref not in scenario_context.equivalence_case_ids:
        raise PackManifestError(
            f"learner {learner_id!r} names unknown CLI equivalence case {equivalence_case_ref!r}"
        )
    cli_hint_ids = _required_str_list(cli, "hint_ids_used", allow_empty=True)
    _ensure_unique(cli_hint_ids, subject=f"learner {learner_id!r} CLI hint ids")
    _require_known_values(
        cli_hint_ids,
        allowed=scenario_context.hint_ids,
        subject=f"learner {learner_id!r} CLI hint ids",
    )
    cli_final_hash = _sha256(
        cli,
        "expected_final_state_sha256",
        subject=f"learner {learner_id!r} CLI attempt",
    )
    if cli_final_hash != scenario_context.pins.target_repaired_state_sha256:
        raise PackManifestError(
            f"learner {learner_id!r} CLI final state drifts from the scenario target"
        )
    cli_grade_points = _cli_grade_points(
        _mapping(cli, "expected_lab_grade", subject=f"learner {learner_id!r} CLI grade"),
        scenario_context.grade_weights,
        learner_id=learner_id,
    )

    exit_response = _mapping(
        learner_doc,
        "exit_probe_response",
        subject=f"learner {learner_id!r}",
    )
    exit_item_id = _required_str(exit_response, "item_id")
    if exit_item_id not in exit_probe_item_ids:
        raise PackManifestError(
            f"learner {learner_id!r} names unknown exit-probe item {exit_item_id!r}"
        )
    if "answer" not in exit_response:
        raise PackSchemaError(f"learner {learner_id!r} exit probe: answer is required")
    exit_hint_ids = _required_str_list(exit_response, "hint_ids_used", allow_empty=True)
    _ensure_unique(exit_hint_ids, subject=f"learner {learner_id!r} exit-probe hint ids")
    _require_known_values(
        exit_hint_ids,
        allowed=scenario_context.hint_ids,
        subject=f"learner {learner_id!r} exit-probe hint ids",
    )
    exit_criteria = _required_str_list(
        exit_response, "expected_criteria_awarded", allow_empty=False
    )
    _ensure_unique(exit_criteria, subject=f"learner {learner_id!r} exit-probe criteria")
    exit_total = _nonnegative_int(exit_response, "expected_total")
    if exit_total > 100:
        raise PackSchemaError(f"learner {learner_id!r} exit-probe total cannot exceed 100")

    outcome = _mapping(learner_doc, "expected_outcome", subject=f"learner {learner_id!r}")
    progress_phase = _required_str(outcome, "progress_phase")
    achievement_status = _required_str(outcome, "achievement_status")
    review_status = _required_str(outcome, "review_status")
    evidence_status = _required_str(outcome, "evidence_status")
    required_complete = _required_bool(outcome, "required_observations_complete")
    critical_error = _required_bool(outcome, "critical_error")
    schedule_band = _optional_str(outcome, "schedule_band")
    reason_codes = _required_str_list(outcome, "reason_codes", allow_empty=False)
    _ensure_unique(reason_codes, subject=f"learner {learner_id!r} outcome reason codes")

    expected_overall_m = _learner_overall_m(learner_doc, learner_id=learner_id)
    facet_derivation = learner_doc.get("expected_facet_derivation")
    facet_derivation_sha256 = (
        _canonical_json_sha256(facet_derivation) if isinstance(facet_derivation, dict) else None
    )
    summary_overall_m = _summary_overall_m(summary, learner_id=learner_id)
    if expected_overall_m != summary_overall_m:
        raise PackManifestError(
            f"learner {learner_id!r} overall score {expected_overall_m!r} disagrees "
            f"with manifest summary {summary_overall_m!r}"
        )
    summary_statuses = (
        _required_str(summary, "expected_achievement_status"),
        _required_str(summary, "expected_review_status"),
        _required_str(summary, "expected_evidence_status"),
    )
    body_statuses = (achievement_status, review_status, evidence_status)
    if body_statuses != summary_statuses:
        raise PackManifestError(
            f"learner {learner_id!r} outcome statuses disagree with the manifest summary"
        )

    must_equal = _must_equal_target(summary, outcome, learner_id=learner_id)
    response_projection = {
        "baseline_responses": [_response_pin_projection(pin) for pin in baseline_responses],
        "followup_trigger_codes": trigger_codes,
        "followup_item_ids": followup_item_ids,
        "followup_responses": [_response_pin_projection(pin) for pin in followup_responses],
        "cli": {
            "equivalence_case_ref": equivalence_case_ref,
            "expected_final_state_sha256": cli_final_hash,
            "hint_ids_used": cli_hint_ids,
            "grade_points": cli_grade_points,
        },
        "exit_probe": {
            "item_id": exit_item_id,
            "answer_sha256": _canonical_json_sha256(exit_response["answer"]),
            "hint_ids_used": exit_hint_ids,
            "criteria_awarded": exit_criteria,
            "expected_total": exit_total,
        },
    }
    outcome_projection = {
        "progress_phase": progress_phase,
        "overall_m": expected_overall_m,
        "achievement_status": achievement_status,
        "review_status": review_status,
        "evidence_status": evidence_status,
        "schedule_band": schedule_band,
        "reason_codes": reason_codes,
        "required_observations_complete": required_complete,
        "critical_error": critical_error,
    }
    return ScriptedLearnerPin(
        learner_vector_id=learner_id,
        relative_path=relative_path,
        raw_sha256=raw_sha256,
        objective_ref=learner_objective,
        response_vector_sha256=_canonical_json_sha256(response_projection),
        baseline_responses=baseline_responses,
        followup_responses=followup_responses,
        expected_followup_trigger_codes=tuple(trigger_codes),
        expected_followup_item_ids=tuple(followup_item_ids),
        cli_equivalence_case_ref=equivalence_case_ref,
        cli_expected_final_state_sha256=cli_final_hash,
        cli_hint_ids_used=tuple(cli_hint_ids),
        cli_grade_points=cli_grade_points,
        exit_probe_item_id=exit_item_id,
        exit_probe_answer_sha256=_canonical_json_sha256(exit_response["answer"]),
        exit_probe_hint_ids_used=tuple(exit_hint_ids),
        exit_probe_expected_criteria_awarded=tuple(exit_criteria),
        exit_probe_expected_total=exit_total,
        expected_facet_derivation_sha256=facet_derivation_sha256,
        outcome_sha256=_canonical_json_sha256(outcome_projection),
        expected_progress_phase=progress_phase,
        expected_overall_m=expected_overall_m,
        expected_achievement_status=achievement_status,
        expected_review_status=review_status,
        expected_evidence_status=evidence_status,
        expected_schedule_band=schedule_band,
        expected_reason_codes=tuple(reason_codes),
        required_observations_complete=required_complete,
        critical_error=critical_error,
        must_equal_learner_vector_id=must_equal,
    )


def _response_pins(
    records: list[dict[str, Any]],
    *,
    subject: str,
    require_confidence: bool,
    require_disposition: bool,
) -> tuple[ExpectedResponsePin, ...]:
    pins = tuple(
        _response_pin(
            record,
            subject=f"{subject} response {index}",
            require_confidence=require_confidence,
            require_disposition=require_disposition,
        )
        for index, record in enumerate(records, start=1)
    )
    _ensure_unique(
        [pin.item_id for pin in pins],
        subject=f"{subject} response item ids",
    )
    return pins


def _response_pin(
    record: dict[str, Any],
    *,
    subject: str,
    require_confidence: bool,
    require_disposition: bool,
) -> ExpectedResponsePin:
    item_id = _required_str(record, "item_id")
    if "answer" not in record:
        raise PackSchemaError(f"{subject}: answer is required")

    confidence = (
        _bounded_int(record, "confidence", minimum=1, maximum=5)
        if require_confidence
        else _optional_bounded_int(record, "confidence", minimum=1, maximum=5)
    )
    disposition = (
        _required_str(record, "expected_disposition")
        if require_disposition
        else _optional_str(record, "expected_disposition")
    )
    if disposition is not None and disposition not in _ALLOWED_RESPONSE_DISPOSITIONS:
        raise PackSchemaError(
            f"{subject}: expected_disposition must be one of "
            f"{sorted(_ALLOWED_RESPONSE_DISPOSITIONS)}"
        )

    if "expected_correct" not in record:
        raise PackSchemaError(f"{subject}: expected_correct is required")
    correct = record["expected_correct"]
    if correct is not None and not isinstance(correct, bool):
        raise PackSchemaError(f"{subject}: expected_correct must be a boolean or null")
    if disposition == "graded" and not isinstance(correct, bool):
        raise PackSchemaError(f"{subject}: a graded response requires boolean expected_correct")
    if disposition == "review_required" and correct is not None:
        raise PackSchemaError(f"{subject}: review_required must not carry a correctness verdict")
    if not require_disposition and not isinstance(correct, bool):
        raise PackSchemaError(f"{subject}: follow-up expected_correct must be a boolean")

    tags = _misconception_tags(record, subject=subject)
    reason_codes = _optional_str_list(record, "expected_reason_codes")
    _ensure_unique(reason_codes, subject=f"{subject} expected reason codes")
    return ExpectedResponsePin(
        item_id=item_id,
        answer_sha256=_canonical_json_sha256(record["answer"]),
        confidence=confidence,
        expected_disposition=disposition,
        expected_correct=correct,
        expected_points=_optional_nonnegative_int(record, "expected_points"),
        misconception_tags=tuple(tags),
        expected_reason_codes=tuple(reason_codes),
    )


def _misconception_tags(record: dict[str, Any], *, subject: str) -> list[str]:
    value = record.get("misconception_tag")
    if value is None:
        return []
    if isinstance(value, str):
        tags = [value] if value else []
    elif isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        tags = cast(list[str], value)
    else:
        raise PackSchemaError(f"{subject}: misconception_tag must be a string or string list")
    if not tags:
        raise PackSchemaError(f"{subject}: misconception_tag must not be empty")
    _ensure_unique(tags, subject=f"{subject} misconception tags")
    return tags


def _cli_grade_points(
    grade: dict[str, Any],
    grade_weights: tuple[tuple[str, int], ...],
    *,
    learner_id: str,
) -> tuple[tuple[str, int], ...]:
    component_names = tuple(name for name, _ in grade_weights)
    expected_keys = set(component_names) | {"total"}
    if set(grade) != expected_keys:
        raise PackSchemaError(
            f"learner {learner_id!r} CLI grade keys must be exactly {sorted(expected_keys)}"
        )

    values: list[tuple[str, int]] = []
    for name, maximum in grade_weights:
        value = _nonnegative_int(grade, name)
        if value > maximum:
            raise PackSchemaError(
                f"learner {learner_id!r} CLI grade {name!r} exceeds weight {maximum}"
            )
        values.append((name, value))
    total = _nonnegative_int(grade, "total")
    if total != sum(value for _, value in values):
        raise PackManifestError(
            f"learner {learner_id!r} CLI grade total does not equal its component sum"
        )
    return tuple(values + [("total", total)])


def _learner_overall_m(learner_doc: dict[str, Any], *, learner_id: str) -> str | None:
    derivation = learner_doc.get("expected_facet_derivation")
    if derivation is None:
        return None
    if not isinstance(derivation, dict):
        raise PackSchemaError(
            f"learner {learner_id!r}: expected_facet_derivation must be a mapping"
        )
    displayed = derivation.get("displayed_2dp_OUTPUT_ONLY")
    if displayed is not None:
        if not isinstance(displayed, dict):
            raise PackSchemaError(
                f"learner {learner_id!r}: displayed_2dp_OUTPUT_ONLY must be a mapping"
            )
        if "overall_M" not in displayed:
            raise PackSchemaError(
                f"learner {learner_id!r}: displayed_2dp_OUTPUT_ONLY lacks overall_M"
            )
        return _normalized_score(displayed["overall_M"], subject=f"learner {learner_id!r}")
    if "overall_M" not in derivation:
        raise PackSchemaError(f"learner {learner_id!r}: expected_facet_derivation lacks overall_M")
    return _normalized_score(derivation["overall_M"], subject=f"learner {learner_id!r}")


def _summary_overall_m(summary: dict[str, Any], *, learner_id: str) -> str | None:
    if "expected_overall_M" not in summary:
        raise PackSchemaError(
            f"manifest summary for learner {learner_id!r} lacks expected_overall_M"
        )
    value = summary["expected_overall_M"]
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{2}", value):
        raise PackSchemaError(
            f"manifest summary for learner {learner_id!r} must pin overall_M at 2dp"
        )
    normalized = _normalized_score(value, subject=f"manifest learner {learner_id!r}")
    if normalized != value:
        raise PackSchemaError(
            f"manifest summary for learner {learner_id!r} is not a canonical 2dp score"
        )
    return value


def _normalized_score(value: object, *, subject: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PackSchemaError(f"{subject}: overall_M must be numeric")
    try:
        score = Decimal(str(value))
    except Exception as exc:
        raise PackSchemaError(f"{subject}: overall_M must be numeric") from exc
    if not score.is_finite() or score < 0 or score > 100:
        raise PackSchemaError(f"{subject}: overall_M must be between 0 and 100")
    return format(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def _must_equal_target(
    summary: dict[str, Any], outcome: dict[str, Any], *, learner_id: str
) -> str | None:
    summary_target = _optional_str(summary, "must_equal")
    comparison = outcome.get("clean_comparison")
    if summary_target is None:
        if comparison is not None:
            raise PackManifestError(
                f"learner {learner_id!r} has an outcome comparison absent from its summary"
            )
        return None
    if not isinstance(comparison, dict):
        raise PackManifestError(
            f"learner {learner_id!r} must_equal requires a clean_comparison mapping"
        )
    comparison = cast(dict[str, Any], comparison)
    body_target = _required_str(comparison, "compared_against_learner_vector_id")
    if body_target != summary_target:
        raise PackManifestError(
            f"learner {learner_id!r} comparison target disagrees with manifest must_equal"
        )
    if _required_str(comparison, "expected_relationship") != (
        "identical_canonical_authority_projection"
    ):
        raise PackManifestError(
            f"learner {learner_id!r} must_equal relationship is not authority-projection equality"
        )
    must_match = _required_str_list(comparison, "must_match_fields", allow_empty=False)
    may_differ = _required_str_list(comparison, "may_differ_fields", allow_empty=False)
    _ensure_unique(must_match, subject=f"learner {learner_id!r} must-match fields")
    _ensure_unique(may_differ, subject=f"learner {learner_id!r} may-differ fields")
    return summary_target


def _response_pin_projection(pin: ExpectedResponsePin) -> dict[str, Any]:
    return {
        "item_id": pin.item_id,
        "answer_sha256": pin.answer_sha256,
        "confidence": pin.confidence,
        "expected_disposition": pin.expected_disposition,
        "expected_correct": pin.expected_correct,
        "expected_points": pin.expected_points,
        "misconception_tags": pin.misconception_tags,
        "expected_reason_codes": pin.expected_reason_codes,
    }


def scripted_learner_authority_projection(pin: ScriptedLearnerPin) -> dict[str, Any]:
    """Fields the injection vector promises cannot be changed by learner text.

    The fixture loader and Gate 1 runner intentionally share this projection.
    Audit-only reason codes may differ when an embedded instruction is ignored;
    grades, derived authority, state, and scheduling outcomes may not.
    """
    return {
        "baseline_grades": [
            (
                response.item_id,
                response.expected_disposition,
                response.expected_correct,
                response.expected_points,
            )
            for response in pin.baseline_responses
        ],
        "followup_grades": [
            (response.item_id, response.expected_correct, response.expected_points)
            for response in pin.followup_responses
        ],
        "followup_trigger_codes": pin.expected_followup_trigger_codes,
        "followup_item_ids": pin.expected_followup_item_ids,
        "cli_final_state_sha256": pin.cli_expected_final_state_sha256,
        "cli_hint_ids_used": pin.cli_hint_ids_used,
        "cli_grade_points": pin.cli_grade_points,
        "exit_probe_item_id": pin.exit_probe_item_id,
        "exit_probe_hint_ids_used": pin.exit_probe_hint_ids_used,
        "exit_probe_expected_criteria_awarded": pin.exit_probe_expected_criteria_awarded,
        "exit_probe_expected_total": pin.exit_probe_expected_total,
        "facet_derivation_sha256": pin.expected_facet_derivation_sha256,
        "progress_phase": pin.expected_progress_phase,
        "overall_m": pin.expected_overall_m,
        "achievement_status": pin.expected_achievement_status,
        "review_status": pin.expected_review_status,
        "evidence_status": pin.expected_evidence_status,
        "schedule_band": pin.expected_schedule_band,
        "required_observations_complete": pin.required_observations_complete,
        "critical_error": pin.critical_error,
    }


def _canonical_json_sha256(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PackSchemaError("semantic pin contains a value that is not canonical JSON") from exc
    return hashlib.sha256(payload).hexdigest()


def _require_known_values(values: list[str], *, allowed: tuple[str, ...], subject: str) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise PackManifestError(f"{subject} contain unknown value(s): {unknown}")


def _retrieval_cases(payload: bytes, *, objective_ref: str) -> RetrievalCaseSet:
    """Parse the frozen retrieval contract into typed records."""
    document = _json_mapping(payload, relative_path="retrieval-cases document")
    declared = document.get("objective_ref")
    if declared != objective_ref:
        raise PackSchemaError(
            f"retrieval cases declare objective {declared!r} but the fixture pins {objective_ref!r}"
        )

    cases: list[RetrievalCase] = []
    for record in _mapping_list(document, "supported_cases"):
        cases.append(
            RetrievalCase(
                case_id=_required_str(record, "case_id"),
                kind=_required_str(record, "kind"),
                query=_required_str(record, "query"),
                supported=True,
                expected_evidence_ids=tuple(_str_list(record, "expected_evidence_ids")),
                expected_top_k=_positive_int(record, "expected_top_k", default=5),
            )
        )
    for record in _mapping_list(document, "unsupported_cases"):
        cases.append(
            RetrievalCase(
                case_id=_required_str(record, "case_id"),
                kind=_required_str(record, "kind"),
                query=_required_str(record, "query"),
                supported=False,
                expected_abstention_reason_code=_required_str(
                    record, "expected_abstention_reason_code"
                ),
                must_never_return=tuple(_str_list(record, "must_never_return")),
            )
        )

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise PackSchemaError("retrieval case ids must be unique")
    return RetrievalCaseSet(objective_ref=objective_ref, cases=tuple(cases))


# ---- decoding helpers -----------------------------------------------------

#: Recorded on every translated region so a reader can tell which adapter
#: revision produced the record. Not a claim about when the underlying
#: source was authored.
_FIXTURE_EXTRACTOR_VERSION = "frozen-fixture-adapter-1.0"
_FIXTURE_EXTRACTED_AT = datetime(2026, 7, 27, tzinfo=UTC)


def _yaml_payload(payloads: dict[str, bytes], relative_path: str) -> dict[str, Any]:
    if relative_path not in payloads:
        raise PackSchemaError(f"the fixture inventory does not pin {relative_path}")
    return _yaml_mapping(payloads[relative_path], relative_path=relative_path)


def _optional_yaml_payload(payloads: dict[str, bytes], relative_path: str) -> dict[str, Any] | None:
    if relative_path not in payloads:
        return None
    return _yaml_mapping(payloads[relative_path], relative_path=relative_path)


def _json_mapping(payload: bytes, *, relative_path: str) -> dict[str, Any]:
    """Decode strict JSON only after the caller has verified the raw bytes."""

    def reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value!r}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_object,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackManifestError(f"{relative_path} is not strict, well-formed JSON") from exc
    if not isinstance(value, dict):
        raise PackSchemaError(f"{relative_path} must hold a JSON object")
    return cast(dict[str, Any], value)


def _yaml_mapping(payload: bytes, *, relative_path: str) -> dict[str, Any]:
    """Safe-load one verified YAML document.

    ``yaml.safe_load`` constructs only plain scalars, sequences, and
    mappings — it cannot instantiate arbitrary Python objects, which is
    the whole reason the unsafe loaders are never reachable from here.
    The import is local and its absence is a typed, actionable error:
    PyYAML belongs to the optional extraction extra, and the lightweight
    core install must not acquire it.
    """
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the extra-less env
        raise PackSchemaError(
            "reading a YAML fixture needs the optional extraction extra: uv sync --extra ccna-lab"
        ) from exc

    try:
        value = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PackManifestError(f"{relative_path} is not safe, well-formed YAML") from exc
    if not isinstance(value, dict):
        raise PackSchemaError(f"{relative_path} must hold a YAML mapping")
    return cast(dict[str, Any], value)


def _inventory(document: dict[str, Any]) -> dict[str, str]:
    """The manifest's path/hash inventory, with every path admitted.

    Path admission happens here rather than at read time so a malformed
    manifest is rejected before it can describe anything: an absolute
    path, a drive letter, a traversal segment, a backslash separator, an
    empty segment, or a repeated path all fail closed.
    """
    records = document.get(_INVENTORY_KEY)
    if not isinstance(records, list) or not records:
        raise PackSchemaError(f"{_INVENTORY_KEY} must be a non-empty list")

    inventory: dict[str, str] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise PackSchemaError(f"{_INVENTORY_KEY} entries must be mappings")
        record = cast(dict[str, Any], raw)
        path = _required_str(record, "path")
        if path != path.strip() or "\\" in path:
            raise PackManifestError(f"inventory path is not a clean POSIX path: {path!r}")
        if path.startswith("/") or re.fullmatch(r"[a-zA-Z]:.*", path):
            raise PackManifestError(f"inventory path must be relative: {path!r}")
        segments = path.split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise PackManifestError(f"inventory path has an unusable segment: {path!r}")
        if path in inventory:
            raise PackManifestError(f"duplicate inventory path: {path!r}")
        inventory[path] = _sha256(record, "sha256", subject=path)
    return inventory


def _path_for_hash(inventory: dict[str, str], sha256: str, subject: str) -> str:
    """The single inventory path pinning ``sha256``, or a manifest error."""
    matches = sorted(path for path, digest in inventory.items() if digest == sha256)
    if not matches:
        raise PackManifestError(
            f"{subject!r} names bytes the fixture inventory does not pin; an unpinned "
            "source can never be byte-verified"
        )
    if len(matches) > 1:
        raise PackManifestError(
            f"{subject!r} matches more than one inventory path, so its provenance is ambiguous"
        )
    return matches[0]


def _media_type_for(relative_path: str) -> str:
    suffix = relative_path[relative_path.rfind(".") :] if "." in relative_path else ""
    return _MEDIA_TYPE_BY_SUFFIX.get(suffix.lower(), "application/octet-stream")


def _mapping(document: dict[str, Any], key: str, *, subject: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise PackSchemaError(f"{subject}: {key} must be a mapping")
    return cast(dict[str, Any], value)


def _mapping_list(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PackSchemaError(f"{key} must be a list of mappings")
    return cast(list[dict[str, Any]], value)


def _optional_mapping_list(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PackSchemaError(f"{key} must be a list of mappings when present")
    return cast(list[dict[str, Any]], value)


def _required_str(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise PackSchemaError(f"{key} must be a non-empty string")
    return value


def _optional_str(document: dict[str, Any], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PackSchemaError(f"{key} must be a non-empty string when present")
    return value


def _str_list(document: dict[str, Any], key: str) -> list[str]:
    value = document.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PackSchemaError(f"{key} must be a list of strings")
    return cast(list[str], value)


def _required_str_list(document: dict[str, Any], key: str, *, allow_empty: bool) -> list[str]:
    if key not in document:
        raise PackSchemaError(f"{key} is required")
    value = document[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PackSchemaError(f"{key} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise PackSchemaError(f"{key} must not be empty")
    return cast(list[str], value)


def _optional_str_list(document: dict[str, Any], key: str) -> list[str]:
    if key not in document:
        return []
    value = document[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PackSchemaError(f"{key} must be a list of non-empty strings when present")
    return cast(list[str], value)


def _required_bool(document: dict[str, Any], key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise PackSchemaError(f"{key} must be a boolean")
    return value


def _optional_bool(document: dict[str, Any], key: str) -> bool | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise PackSchemaError(f"{key} must be a boolean when present")
    return value


def _bounded_int(document: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise PackSchemaError(f"{key} must be an integer from {minimum} through {maximum}")
    return value


def _optional_bounded_int(
    document: dict[str, Any], key: str, *, minimum: int, maximum: int
) -> int | None:
    if key not in document:
        return None
    return _bounded_int(document, key, minimum=minimum, maximum=maximum)


def _nonnegative_int(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PackSchemaError(f"{key} must be a non-negative integer")
    return value


def _optional_nonnegative_int(document: dict[str, Any], key: str) -> int | None:
    if key not in document:
        return None
    return _nonnegative_int(document, key)


def _positive_int_value(value: object, *, subject: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PackSchemaError(f"{subject} must be a positive integer")
    return value


def _ensure_unique(values: list[str] | tuple[str, ...], *, subject: str) -> None:
    if len(values) != len(set(values)):
        raise PackManifestError(f"{subject} must be unique")


def _validate_clean_relative_path(path: str, *, subject: str) -> None:
    if path != path.strip() or "\\" in path:
        raise PackManifestError(f"{subject} is not a clean POSIX path: {path!r}")
    if path.startswith("/") or re.fullmatch(r"[a-zA-Z]:.*", path):
        raise PackManifestError(f"{subject} must be relative: {path!r}")
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise PackManifestError(f"{subject} has an unusable segment: {path!r}")


def _sha256(document: dict[str, Any], key: str, *, subject: str) -> str:
    value = _required_str(document, key)
    if not _SHA256_PATTERN.fullmatch(value):
        raise PackSchemaError(f"{subject}: {key} must be 64 lowercase hex characters")
    return value


def _positive_int(document: dict[str, Any], key: str, *, default: object = None) -> int:
    value = document.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PackSchemaError(f"{key} must be a positive integer")
    return value


def _bbox(document: dict[str, Any], *, subject: str) -> list[object]:
    value = document.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        raise PackSchemaError(f"{subject}: bbox must hold exactly four values")
    return cast(list[object], value)


def _item_ids(document: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_required_str(record, "item_id") for record in _mapping_list(document, "items"))


def _basis_points(value: object) -> int:
    """A 0..1 authored ratio as exact basis points.

    ``Decimal(str(value))`` rather than ``float`` so "0.85" is exactly
    0.85 and not its nearest binary approximation; ``ROUND_HALF_UP`` at
    the single conversion point, matching the frozen arithmetic contract.
    """
    return int((Decimal(str(value)) * 10_000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _percent_basis_points(value: object) -> int:
    """An authored 0..100 percentage as basis points."""
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _facet_weights(record: dict[str, Any]) -> dict[ObjectiveFacet, int]:
    """Facet weights in basis points, from a single facet or a rubric mapping.

    Rubric-derived weights are computed from exact rational arithmetic over
    the criterion points and rounded once, at the end, so no intermediate
    display value feeds a later computation.
    """
    facet = record.get("facet")
    if isinstance(facet, str):
        return {ObjectiveFacet(facet): 10_000}

    mapping = record.get("facet_score_mapping")
    rubric = record.get("rubric")
    if not isinstance(mapping, dict) or not isinstance(rubric, list):
        raise PackSchemaError("an item must declare a facet or a facet_score_mapping with a rubric")

    points_by_criterion = {
        _required_str(cast(dict[str, Any], entry), "criterion_id"): int(
            cast(dict[str, Any], entry)["points"]
        )
        for entry in rubric
        if isinstance(entry, dict)
    }
    total = int(record.get("points_possible", sum(points_by_criterion.values())))
    if total <= 0:
        raise PackSchemaError("an item's points_possible must be positive")

    weights: dict[ObjectiveFacet, int] = {}
    for facet_name, criterion_ids in cast(dict[str, list[str]], mapping).items():
        earned = sum(points_by_criterion[str(criterion)] for criterion in criterion_ids)
        weights[ObjectiveFacet(str(facet_name))] = int(
            (Decimal(earned) * 10_000 / Decimal(total)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
    return weights
