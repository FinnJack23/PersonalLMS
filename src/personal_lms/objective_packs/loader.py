"""Deterministic Objective Pack loader.

Performs exactly four kinds of work, in this order, and nothing else:

1. **Path admission.** Every path is resolved against an explicitly
   configured root and refused if it lands outside one. Traversal
   segments, absolute paths, drive letters, and symlinks that escape are
   all rejected by the same containment check on the *resolved* location,
   never by pattern-matching the input's spelling.
2. **File admission.** Existence, regular-file type, and a size ceiling
   are checked before any byte is read.
3. **Byte verification.** The file's actual SHA-256 must equal the hash
   the manifest pins. A file whose bytes changed is a different file, and
   loading stops.
4. **Structural loading.** Verified bytes are parsed as JSON and
   validated by the strict Pydantic models in
   ``domain.objective_packs``, then checked for manifest/schema and
   objective/version consistency.

No LLM call, provider call, network access, subprocess, or write of any
kind happens here — the loader only reads configured files. This is a
Tier 0 deterministic service in the sense required by ``AGENTS.md``:
running it twice over unchanged bytes produces byte-identical results.

**Authoring format.** Pack documents are JSON in this pass, not YAML. The
planning documents describe YAML envelopes, but adding a YAML parser is a
dependency decision reserved for the human operator (see AD-01's
neighbouring parser decisions), and the core install deliberately carries
only Pydantic. JSON needs no dependency and round-trips the same strict
models, so the format choice is isolated to
``_parse_json_document`` — swapping in YAML later touches one function.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from personal_lms.domain.objective_packs import (
    ManifestEntry,
    ObjectivePack,
    ObjectivePackManifest,
    ValidationFinding,
    ValidationReasonCode,
    ValidationSeverity,
)
from personal_lms.objective_packs.errors import (
    PackFileNotFoundError,
    PackFileTooLargeError,
    PackFileTypeError,
    PackHashMismatchError,
    PackManifestError,
    PackPathEscapesRootError,
    PackRootNotConfiguredError,
    PackSchemaError,
)

__all__ = [
    "DEFAULT_MANIFEST_FILENAME",
    "DEFAULT_PACK_FILENAME",
    "LoaderLimits",
    "ObjectivePackLoader",
    "PackFileReader",
    "PackLoadResult",
    "SourceManifestBinding",
]

DEFAULT_MANIFEST_FILENAME = "manifest.json"
DEFAULT_PACK_FILENAME = "pack.json"

# Read in fixed-size blocks so hashing a large permitted file never
# materializes it twice in memory.
_HASH_BLOCK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class LoaderLimits:
    """Hard ceilings applied before any file is read.

    ``maximum_file_bytes`` is deliberately modest: an Objective Pack is
    authored text and small frozen fixtures, not a media archive. A file
    over the ceiling is refused rather than truncated, so a caller always
    knows it did not silently get a partial pack.
    """

    maximum_file_bytes: int = 8 * 1024 * 1024
    maximum_files: int = 512


class PackFileReader:
    """Reads files under explicitly configured roots, and nowhere else.

    The containment rule is checked against ``Path.resolve()`` output, so
    a symlink inside a root that points outside it is refused exactly like
    a ``../`` segment would be. Roots themselves are resolved once at
    construction; a root that does not exist or is not a directory is a
    configuration error, raised immediately rather than at first use.
    """

    def __init__(self, *, roots: Sequence[Path | str], limits: LoaderLimits | None = None) -> None:
        if not roots:
            raise PackRootNotConfiguredError(
                "at least one configured root is required; this reader never "
                "falls back to the current working directory"
            )
        resolved: list[Path] = []
        for root in roots:
            candidate = Path(root).resolve()
            if not candidate.exists():
                raise PackRootNotConfiguredError("a configured root does not exist")
            if not candidate.is_dir():
                raise PackRootNotConfiguredError("a configured root is not a directory")
            resolved.append(candidate)
        self._roots = tuple(resolved)
        self._limits = limits if limits is not None else LoaderLimits()

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    @property
    def limits(self) -> LoaderLimits:
        return self._limits

    def resolve(self, relative_path: str) -> Path:
        """The resolved location of ``relative_path`` under a configured root.

        Raises ``PackPathEscapesRootError`` when the resolved location is
        not contained by any root. The input is *also* rejected up front
        for an absolute path or a ``..`` segment — defense in depth, so a
        malformed path is refused even on a platform where resolution
        behaves unexpectedly.
        """
        normalized = relative_path.replace("\\", "/").strip()
        if not normalized:
            raise PackPathEscapesRootError("an empty path is never admissible")
        if normalized.startswith("/") or Path(normalized).is_absolute():
            raise PackPathEscapesRootError("an absolute path is never admissible")
        if any(segment == ".." for segment in normalized.split("/")):
            raise PackPathEscapesRootError("a path containing '..' is never admissible")

        for root in self._roots:
            candidate = (root / normalized).resolve()
            if candidate == root or root in candidate.parents:
                return candidate
        raise PackPathEscapesRootError("the resolved path is outside every configured root")

    def read_bytes(self, relative_path: str, *, expected_sha256: str | None = None) -> bytes:
        """Verified bytes of ``relative_path``.

        Checks admission, existence, file type, and size *before* reading,
        then verifies the hash *after*. Passing ``expected_sha256=None``
        skips only the hash comparison — every other check still applies.
        """
        path = self.resolve(relative_path)
        if not path.exists():
            raise PackFileNotFoundError(f"manifest-listed file is missing: {relative_path}")
        if not path.is_file():
            raise PackFileTypeError(f"manifest-listed path is not a regular file: {relative_path}")

        size = path.stat().st_size
        if size > self._limits.maximum_file_bytes:
            raise PackFileTooLargeError(
                f"file exceeds the {self._limits.maximum_file_bytes}-byte ceiling: {relative_path}"
            )

        payload = path.read_bytes()
        if expected_sha256 is not None:
            actual = hashlib.sha256(payload).hexdigest()
            if actual != expected_sha256:
                raise PackHashMismatchError(
                    f"bytes do not match the pinned hash for {relative_path}: "
                    f"expected {expected_sha256}, computed {actual}"
                )
        return payload

    def sha256_of(self, relative_path: str) -> str:
        """Streaming SHA-256 of an admitted file, without buffering it whole."""
        path = self.resolve(relative_path)
        if not path.exists():
            raise PackFileNotFoundError(f"file is missing: {relative_path}")
        if not path.is_file():
            raise PackFileTypeError(f"path is not a regular file: {relative_path}")

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(_HASH_BLOCK_BYTES):
                digest.update(block)
        return digest.hexdigest()

    def relative_files_under(self, relative_directory: str) -> tuple[str, ...]:
        """Every regular file beneath an admitted directory, POSIX-relative and sorted.

        Used to detect files present on disk but absent from the manifest.
        Symlinks are reported by their link path; the containment check in
        ``resolve`` still governs whether any of them can actually be read.
        """
        directory = self.resolve(relative_directory)
        if not directory.is_dir():
            raise PackFileTypeError(f"path is not a directory: {relative_directory}")

        found: list[str] = []
        for entry in sorted(directory.rglob("*")):
            if not entry.is_file():
                continue
            found.append(entry.relative_to(directory).as_posix())
            if len(found) > self._limits.maximum_files:
                raise PackFileTooLargeError(
                    f"pack directory holds more than {self._limits.maximum_files} files"
                )
        return tuple(found)


@dataclass(frozen=True, slots=True)
class SourceManifestBinding:
    """The one manifest record a source artifact is bound to.

    Every ``SourceArtifactRef`` must correspond to exactly one pinned
    manifest entry whose hash and size match the artifact's own. Without
    that binding a pack could declare a source the manifest never pinned,
    and the "every source byte is verified" claim would be vacuous for
    exactly the source someone chose not to list.
    """

    source_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class PackLoadResult:
    """A loaded pack plus every non-fatal diagnostic gathered on the way.

    A result is only ever produced when loading *succeeded*: security and
    integrity failures raise instead, because there is no meaningful
    partial pack to hand back. ``findings`` therefore holds advisory
    diagnostics — for example a file present on disk that the manifest
    does not list.
    """

    pack: ObjectivePack
    manifest: ObjectivePackManifest
    verified_file_hashes: dict[str, str] = field(default_factory=dict)
    source_manifest_bindings: dict[str, SourceManifestBinding] = field(default_factory=dict)
    findings: tuple[ValidationFinding, ...] = ()

    #: The verified self-hash of a frozen fixture manifest, when the pack
    #: came from one. Deliberately its own field and never conflated with
    #: a canonical hash of the ``ObjectivePackManifest`` model: one covers
    #: the exact authored bytes a human reviewed, the other covers a
    #: derived record. A gate report that cited whichever happened to be
    #: available would be citing two different things under one name.
    fixture_manifest_hash: str | None = None

    #: Typed frozen retrieval contract, when the source format carries one.
    retrieval_cases: object | None = None

    #: Authoring metadata the strict records exclude by design. Typed as
    #: ``object`` here so the generic loader keeps no import edge to a
    #: format adapter; callers that need it narrow the type themselves.
    fixture_extensions: object | None = None

    @property
    def has_errors(self) -> bool:
        return any(finding.severity is ValidationSeverity.ERROR for finding in self.findings)


def _parse_json_document(payload: bytes, *, relative_path: str) -> object:
    """Parse verified bytes as JSON, or raise ``PackManifestError``.

    Isolated so the authoring format is one function's concern — see the
    module docstring's note on JSON versus YAML.
    """
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PackManifestError(f"{relative_path} is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise PackManifestError(f"{relative_path} is not well-formed JSON: {exc.msg}") from exc


class ObjectivePackLoader:
    """Loads one Objective Pack from configured files, deterministically.

    Constructed with a ``PackFileReader`` rather than raw paths so the
    admission policy is injected, not hard-coded — tests supply a reader
    rooted at a temporary directory, and no code path anywhere reaches the
    real filesystem by default.
    """

    def __init__(self, reader: PackFileReader) -> None:
        self._reader = reader

    @property
    def reader(self) -> PackFileReader:
        return self._reader

    def load_manifest(self, relative_path: str) -> ObjectivePackManifest:
        """Load and strictly validate a manifest.

        The manifest is read *without* a pinned hash — it is the root of
        trust for every other file, and nothing outside it can vouch for
        it. Its own integrity is established by the caller comparing
        ``hash_manifest`` against a separately recorded value.
        """
        payload = self._reader.read_bytes(relative_path)
        document = _parse_json_document(payload, relative_path=relative_path)
        try:
            return ObjectivePackManifest.model_validate(document)
        except ValidationError as exc:
            raise PackSchemaError(f"{relative_path} is not a valid pack manifest: {exc}") from exc

    def load(
        self,
        *,
        pack_directory: str,
        manifest_filename: str = DEFAULT_MANIFEST_FILENAME,
        pack_filename: str = DEFAULT_PACK_FILENAME,
    ) -> PackLoadResult:
        """Load, verify, and structurally validate one pack directory.

        Order matters and is fixed: the manifest is loaded first, every
        listed file is byte-verified against it, and only then is the pack
        document parsed. A pack whose bytes do not match its manifest
        never reaches schema validation.
        """
        manifest_relative = f"{pack_directory}/{manifest_filename}"
        manifest = self.load_manifest(manifest_relative)

        verified: dict[str, str] = {}
        findings: list[ValidationFinding] = []

        for entry in manifest.entries:
            entry_relative = f"{pack_directory}/{entry.relative_path}"
            payload = self._reader.read_bytes(entry_relative, expected_sha256=entry.sha256)
            if len(payload) != entry.size_bytes:
                raise PackManifestError(
                    f"size mismatch for {entry.relative_path}: manifest declares "
                    f"{entry.size_bytes}, file holds {len(payload)}"
                )
            verified[entry.relative_path] = entry.sha256

        self._collect_unlisted_files(
            pack_directory=pack_directory,
            manifest=manifest,
            manifest_filename=manifest_filename,
            findings=findings,
        )

        if pack_filename not in manifest.entries_by_path:
            raise PackManifestError(
                f"the manifest does not list the pack document {pack_filename}; "
                "an unpinned pack document cannot be trusted"
            )

        pack_relative = f"{pack_directory}/{pack_filename}"
        pack_payload = self._reader.read_bytes(
            pack_relative, expected_sha256=manifest.entries_by_path[pack_filename].sha256
        )
        pack_document = _parse_json_document(pack_payload, relative_path=pack_relative)

        try:
            pack = ObjectivePack.model_validate(pack_document)
        except ValidationError as exc:
            raise PackSchemaError(f"{pack_relative} is not a valid Objective Pack: {exc}") from exc

        self._assert_manifest_agrees_with_pack(manifest=manifest, pack=pack)
        bindings = self._bind_sources_to_manifest(manifest=manifest, pack=pack)

        return PackLoadResult(
            pack=pack,
            manifest=manifest,
            verified_file_hashes=verified,
            source_manifest_bindings=bindings,
            findings=tuple(sorted(findings, key=lambda finding: finding.sort_key)),
        )

    @staticmethod
    def _bind_sources_to_manifest(
        *, manifest: ObjectivePackManifest, pack: ObjectivePack
    ) -> dict[str, SourceManifestBinding]:
        """Bind every source artifact to exactly one pinned manifest entry.

        Matching is by content hash, not by filename: the hash *is* the
        artifact's identity, so a renamed file still binds and two entries
        sharing a hash are genuinely ambiguous rather than merely
        confusing.
        """
        by_hash: dict[str, list[ManifestEntry]] = {}
        for entry in manifest.entries:
            by_hash.setdefault(entry.sha256, []).append(entry)

        bindings: dict[str, SourceManifestBinding] = {}
        for artifact in pack.source_artifacts:
            candidates = by_hash.get(artifact.sha256, [])
            if not candidates:
                raise PackManifestError(
                    f"source artifact {artifact.source_id!r} has no manifest entry pinning "
                    "its bytes; an unpinned source can never be byte-verified"
                )
            if len(candidates) > 1:
                raise PackManifestError(
                    f"source artifact {artifact.source_id!r} matches more than one manifest "
                    "entry, so its provenance is ambiguous"
                )
            entry = candidates[0]
            if entry.size_bytes != artifact.size_bytes:
                raise PackManifestError(
                    f"source artifact {artifact.source_id!r} declares {artifact.size_bytes} "
                    f"bytes but its manifest entry pins {entry.size_bytes}"
                )
            already_bound = next(
                (
                    bound
                    for bound in bindings.values()
                    if bound.relative_path == entry.relative_path
                ),
                None,
            )
            if already_bound is not None:
                raise PackManifestError(
                    f"source artifacts {already_bound.source_id!r} and "
                    f"{artifact.source_id!r} both bind to manifest entry "
                    f"{entry.relative_path!r}; two identities over one file make "
                    "their rights and trust authority ambiguous"
                )

            bindings[artifact.source_id] = SourceManifestBinding(
                source_id=artifact.source_id,
                relative_path=entry.relative_path,
                sha256=entry.sha256,
                size_bytes=entry.size_bytes,
                media_type=entry.media_type,
            )
        return bindings

    def _collect_unlisted_files(
        self,
        *,
        pack_directory: str,
        manifest: ObjectivePackManifest,
        manifest_filename: str,
        findings: list[ValidationFinding],
    ) -> None:
        """Record a finding for every on-disk file the manifest omits.

        The manifest file itself is expected to be absent from its own
        entry list (it cannot pin its own hash), so it is excluded here
        rather than reported every time.
        """
        listed = set(manifest.entries_by_path)
        listed.add(manifest_filename)
        for present in self._reader.relative_files_under(pack_directory):
            if present in listed:
                continue
            findings.append(
                ValidationFinding(
                    reason_code=ValidationReasonCode.MANIFEST_ENTRY_UNLISTED,
                    severity=ValidationSeverity.ERROR,
                    subject_id=present,
                    message=(
                        "a file is present in the pack directory but not pinned by the "
                        "manifest; an unpinned file cannot participate in a gate"
                    ),
                )
            )

    @staticmethod
    def _assert_manifest_agrees_with_pack(
        *, manifest: ObjectivePackManifest, pack: ObjectivePack
    ) -> None:
        """Objective/version consistency between the manifest and the pack.

        ``ObjectivePack`` already rejects a mismatch between its own
        ``objective_ref`` and its embedded manifest. This additionally
        checks the manifest actually read from disk, catching a pack whose
        embedded copy was edited to agree with itself while the pinned
        manifest says something else.
        """
        if manifest.objective_ref != pack.objective_ref:
            raise PackManifestError(
                "the on-disk manifest's objective_ref does not match the pack document's; "
                "a pack never spans two objective versions"
            )
        if manifest.pack_id != pack.manifest.pack_id:
            raise PackManifestError(
                "the on-disk manifest's pack_id does not match the pack document's "
                "embedded manifest"
            )
        if manifest.pack_version != pack.manifest.pack_version:
            raise PackManifestError(
                "the on-disk manifest's pack_version does not match the pack document's "
                "embedded manifest"
            )
