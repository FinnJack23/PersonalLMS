"""Synthetic Objective Pack builders for tests.

Every fixture these produce is **synthetic and marked
``draft_for_human_review``**. Nothing here is authoritative, approved, or
golden, and none of it derives from WGU material, private course content,
a copyrighted question bank, a real vault, or any personal data. The
"technical claims" are deliberately generic placeholder statements about
an invented protocol so the fixtures cannot be mistaken for real exam
content.

Builders take keyword overrides so a test can express exactly the one
thing it cares about — a wrong hash, a quarantined region, a duplicated
item — against an otherwise valid pack.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_lms.domain.objective_packs import (
    ApprovedClaim,
    AssessmentItem,
    ClaimSupport,
    EvidenceRegion,
    ExposureClass,
    ExtractionMetadata,
    ImageRegionSelector,
    ManifestEntry,
    MasteryPolicy,
    ObjectiveFacet,
    ObjectivePack,
    ObjectivePackManifest,
    PageTextSelector,
    PermittedUse,
    ReviewState,
    SourceArtifactRef,
    TrustStatus,
)
from personal_lms.domain.source_inventory import SourceRightsStatus
from personal_lms.objective_packs.scoring import CLAIM_SCORE_POLICY_VERSION

OBJECTIVE_REF = "synthetic-exam-v1.1:2.2"
OTHER_OBJECTIVE_REF = "synthetic-exam-v2.0:2.2"
DECIDED_AT = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    """One length-prefixed, CRC-terminated PNG chunk."""
    return (
        len(payload).to_bytes(4, "big")
        + chunk_type
        + payload
        + zlib.crc32(chunk_type + payload).to_bytes(4, "big")
    )


def make_png(width: int = 64, height: int = 48) -> bytes:
    """A genuinely valid, structurally complete greyscale PNG.

    Built with the standard library only — no image dependency. A real
    file rather than a header stub, because the extractor now rejects
    header-only pseudo-images: a 24-byte stub carries no pixels and must
    not be able to stand in for an infographic a human is meant to review.
    """
    header = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 0, 0, 0, 0))  # 8-bit greyscale, no interlace
    )
    # One filter byte plus one sample per pixel, per scanline.
    raw = b"".join(b"\x00" + bytes(width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


#: A valid 64x48 PNG used across the fixtures.
PNG_BYTES = make_png()

#: A signature plus a well-formed IHDR and nothing else. Structurally
#: incomplete: no IDAT, no IEND, no pixels. Kept as an explicit negative
#: fixture so the "header-only stub is refused" test has a real subject.
PNG_HEADER_ONLY_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + (13).to_bytes(4, "big")
    + b"IHDR"
    + (64).to_bytes(4, "big")
    + (48).to_bytes(4, "big")
    + b"\x08\x06\x00\x00\x00"
    + b"\x00\x00\x00\x00"
)

PDF_BYTES = b"%PDF-1.7\n% synthetic draft_for_human_review fixture\n"


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_source(
    *,
    source_id: str = "src-pdf",
    payload: bytes = PDF_BYTES,
    media_type: str = "application/pdf",
    rights_status: SourceRightsStatus = SourceRightsStatus.OWNED,
    permitted_uses: frozenset[PermittedUse] | None = None,
    trust_status: TrustStatus = TrustStatus.TRUSTED,
    review_state: ReviewState = ReviewState.APPROVED,
    current_for_objective_refs: tuple[str, ...] = (OBJECTIVE_REF,),
    **overrides: Any,
) -> SourceArtifactRef:
    return SourceArtifactRef(
        source_id=source_id,
        sha256=sha256_of(payload),
        media_type=media_type,  # type: ignore[arg-type]
        size_bytes=len(payload),
        title="Synthetic draft fixture",
        rights_status=rights_status,
        permitted_uses=(
            permitted_uses
            if permitted_uses is not None
            else frozenset({PermittedUse.LOCAL_EXTRACT, PermittedUse.LOCAL_TEACH})
        ),
        trust_status=trust_status,
        review_state=review_state,
        current_for_objective_refs=current_for_objective_refs,
        **overrides,
    )


def make_extraction(**overrides: Any) -> ExtractionMetadata:
    defaults: dict[str, Any] = {
        "method": "local-fixture",
        "extractor_version": "1.0",
        "extracted_at": DECIDED_AT,
        "confidence_basis_points": 10_000,
        "is_ocr": False,
    }
    return ExtractionMetadata(**{**defaults, **overrides})


def make_text_region(
    *,
    evidence_id: str = "ev-text-1",
    source_id: str = "src-pdf",
    text: str = "A synthetic placeholder statement about an invented protocol.",
    trust_status: TrustStatus = TrustStatus.TRUSTED,
    review_state: ReviewState = ReviewState.APPROVED,
    objective_refs: tuple[str, ...] = (OBJECTIVE_REF,),
    **overrides: Any,
) -> EvidenceRegion:
    return EvidenceRegion(
        evidence_id=evidence_id,
        source_id=source_id,
        selector=PageTextSelector(page_number=1, start_offset=0, end_offset=len(text)),
        exact_text=text,
        content_sha256=text_hash(text),
        extraction=make_extraction(),
        objective_refs=objective_refs,
        trust_status=trust_status,
        review_state=review_state,
        **overrides,
    )


def make_image_region(
    *,
    evidence_id: str = "ev-image-1",
    source_id: str = "src-png",
    image_payload: bytes = PNG_BYTES,
    description: str = "Reviewer-authored description of a synthetic diagram.",
    trust_status: TrustStatus = TrustStatus.TRUSTED,
    review_state: ReviewState = ReviewState.APPROVED,
    **overrides: Any,
) -> EvidenceRegion:
    return EvidenceRegion(
        evidence_id=evidence_id,
        source_id=source_id,
        selector=ImageRegionSelector(
            image_sha256=sha256_of(image_payload),
            left_basis_points=1_000,
            top_basis_points=1_000,
            right_basis_points=9_000,
            bottom_basis_points=9_000,
        ),
        accessible_description=description,
        content_sha256=text_hash(description),
        extraction=make_extraction(method="local-fixture-visual"),
        objective_refs=(OBJECTIVE_REF,),
        trust_status=trust_status,
        review_state=review_state,
        **overrides,
    )


def make_support(
    *,
    support_id: str = "sup-1",
    evidence_id: str = "ev-text-1",
    relationship: str = "direct",
    strength: int = 10_000,
    independence_group: str | None = None,
    calculation_policy_version: str = CLAIM_SCORE_POLICY_VERSION,
) -> ClaimSupport:
    """One support edge.

    ``independence_group`` defaults to the evidence id, which is the
    realistic case: distinct evidence is independent unless a reviewer
    explicitly asserts otherwise. A shared literal default would have made
    every fixture's evidence look correlated.
    """
    return ClaimSupport(
        support_id=support_id,
        evidence_id=evidence_id,
        relationship=relationship,  # type: ignore[arg-type]
        authority_basis_points=strength,
        directness_basis_points=strength,
        provenance_completeness_basis_points=strength,
        extraction_integrity_basis_points=strength,
        fitness_and_currency_basis_points=strength,
        independence_group=(independence_group if independence_group is not None else evidence_id),
        calculation_policy_version=calculation_policy_version,
    )


def make_claim(
    *,
    claim_id: str = "claim-1",
    objective_ref: str = OBJECTIVE_REF,
    support: tuple[ClaimSupport, ...] | None = None,
    is_answer_bearing: bool = True,
    review_state: ReviewState = ReviewState.APPROVED,
    facet: ObjectiveFacet = ObjectiveFacet.CONCEPT,
    **overrides: Any,
) -> ApprovedClaim:
    return ApprovedClaim(
        claim_id=claim_id,
        canonical_text="A synthetic placeholder claim for fixture purposes.",
        objective_ref=objective_ref,
        facet=facet,
        support=support if support is not None else (make_support(),),
        review_state=review_state,
        is_answer_bearing=is_answer_bearing,
        **overrides,
    )


def make_item(
    *,
    item_id: str,
    exposure_class: ExposureClass = ExposureClass.BASELINE,
    objective_ref: str = OBJECTIVE_REF,
    claim_ids: tuple[str, ...] = ("claim-1",),
    misconception_tags: tuple[str, ...] = (),
    review_state: ReviewState = ReviewState.APPROVED,
    facet_weights: dict[ObjectiveFacet, int] | None = None,
    **overrides: Any,
) -> AssessmentItem:
    return AssessmentItem(
        item_id=item_id,
        item_version=1,
        objective_ref=objective_ref,
        exposure_class=exposure_class,
        prompt=f"Synthetic placeholder prompt for {item_id}.",
        answer_key_sha256=text_hash(f"answer-key:{item_id}"),
        claim_ids=claim_ids,
        facet_weights=(
            facet_weights if facet_weights is not None else {ObjectiveFacet.CONCEPT: 10_000}
        ),
        misconception_tags=misconception_tags,
        review_state=review_state,
        **overrides,
    )


def make_mastery_policy(*, baseline_item_count: int = 3, **overrides: Any) -> MasteryPolicy:
    defaults: dict[str, Any] = {
        "policy_id": "synthetic-mastery",
        "policy_version": "1.0",
        "baseline_item_count": baseline_item_count,
        "maximum_followup_items": 6,
        "exit_probe_item_count": 1,
        "required_facets": frozenset({ObjectiveFacet.CONCEPT}),
        "minimum_claim_grounding_basis_points": 8_500,
    }
    return MasteryPolicy(**{**defaults, **overrides})


def make_manifest(
    *,
    entries: tuple[ManifestEntry, ...] = (),
    objective_ref: str = OBJECTIVE_REF,
    fixture_status: str = "draft_for_human_review",
    pack_id: str = "synthetic-pack",
    pack_version: str = "1.0",
    **overrides: Any,
) -> ObjectivePackManifest:
    return ObjectivePackManifest(
        pack_id=pack_id,
        pack_version=pack_version,
        objective_ref=objective_ref,
        entries=entries,
        fixture_status=fixture_status,  # type: ignore[arg-type]
        **overrides,
    )


def make_pack(
    *,
    manifest: ObjectivePackManifest | None = None,
    sources: tuple[SourceArtifactRef, ...] | None = None,
    regions: tuple[EvidenceRegion, ...] | None = None,
    claims: tuple[ApprovedClaim, ...] | None = None,
    items: tuple[AssessmentItem, ...] | None = None,
    baseline_item_ids: tuple[str, ...] | None = None,
    exit_probe_item_ids: tuple[str, ...] = ("item-exit",),
    objective_ref: str = OBJECTIVE_REF,
    mastery_policy: MasteryPolicy | None = None,
    required_claim_ids: tuple[str, ...] = ("claim-1",),
    **overrides: Any,
) -> ObjectivePack:
    """A small pack that validates cleanly unless a test perturbs it."""
    resolved_items = (
        items
        if items is not None
        else (
            make_item(item_id="item-1"),
            make_item(item_id="item-2"),
            make_item(item_id="item-3"),
            make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
        )
    )
    return ObjectivePack(
        manifest=manifest if manifest is not None else make_manifest(objective_ref=objective_ref),
        objective_ref=objective_ref,
        objective_title="Synthetic objective for fixture purposes",
        source_artifacts=sources if sources is not None else (make_source(),),
        evidence_regions=regions if regions is not None else (make_text_region(),),
        claims=claims if claims is not None else (make_claim(),),
        items=resolved_items,
        mastery_policy=mastery_policy if mastery_policy is not None else make_mastery_policy(),
        baseline_item_ids=(
            baseline_item_ids if baseline_item_ids is not None else ("item-1", "item-2", "item-3")
        ),
        exit_probe_item_ids=exit_probe_item_ids,
        required_claim_ids=required_claim_ids,
        **overrides,
    )


def write_pack_directory(
    root: Path,
    *,
    pack: ObjectivePack | None = None,
    directory_name: str = "pack-a",
    extra_files: dict[str, bytes] | None = None,
    corrupt_pack_hash: bool = False,
) -> tuple[str, ObjectivePack]:
    """Write a loadable pack directory under ``root``.

    Builds the manifest *from the bytes actually written*, so a fixture is
    internally consistent by construction. ``corrupt_pack_hash`` flips
    that deliberately, which is how the hash-mismatch tests get a pack
    whose manifest lies about its contents.
    """
    pack_directory = root / directory_name
    pack_directory.mkdir(parents=True, exist_ok=True)

    source_files = {"sources/synthetic.pdf": PDF_BYTES, "sources/synthetic.png": PNG_BYTES}
    for relative, payload in {**source_files, **(extra_files or {})}.items():
        destination = pack_directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    base_pack = pack if pack is not None else make_pack()

    # The pack document embeds its own manifest, so the entry list has to
    # be built before the document is serialized: write once with a
    # placeholder hash, then rewrite with the real one.
    entries = [
        ManifestEntry(
            relative_path=relative,
            sha256=sha256_of(payload),
            size_bytes=len(payload),
        )
        for relative, payload in sorted({**source_files, **(extra_files or {})}.items())
    ]

    pack_json = _pack_json_with_entries(base_pack, tuple(entries))
    pack_bytes = pack_json.encode("utf-8")
    (pack_directory / "pack.json").write_bytes(pack_bytes)

    entries.append(
        ManifestEntry(
            relative_path="pack.json",
            sha256="0" * 64 if corrupt_pack_hash else sha256_of(pack_bytes),
            size_bytes=len(pack_bytes),
        )
    )

    manifest = base_pack.manifest.model_copy(
        update={"entries": tuple(sorted(entries, key=lambda entry: entry.relative_path))}
    )
    (pack_directory / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")

    return directory_name, ObjectivePack.model_validate_json(pack_bytes)


def _pack_json_with_entries(pack: ObjectivePack, entries: tuple[ManifestEntry, ...]) -> str:
    """Serialize ``pack`` with its embedded manifest carrying ``entries``."""
    payload: dict[str, Any] = json.loads(pack.model_dump_json())
    payload["manifest"]["entries"] = [json.loads(entry.model_dump_json()) for entry in entries]
    return json.dumps(payload)
