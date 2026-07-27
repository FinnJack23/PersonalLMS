"""Loader tests: path admission, byte verification, and manifest agreement.

The negative cases are the point of this file. A loader that only works
on well-formed input is not a security boundary, so every refusal path —
traversal, root escape, symlink escape, missing file, wrong hash, wrong
size, oversized file, unlisted file, malformed JSON, objective mismatch —
gets an explicit test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_lms.domain.objective_packs import (
    ManifestEntry,
    ObjectivePackManifest,
    ValidationReasonCode,
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
from personal_lms.objective_packs.loader import (
    LoaderLimits,
    ObjectivePackLoader,
    PackFileReader,
)

from ._helpers import OTHER_OBJECTIVE_REF, make_manifest, make_pack, write_pack_directory


@pytest.fixture
def reader(tmp_path: Path) -> PackFileReader:
    return PackFileReader(roots=[tmp_path])


@pytest.fixture
def loader(reader: PackFileReader) -> ObjectivePackLoader:
    return ObjectivePackLoader(reader)


class TestRootConfiguration:
    def test_no_configured_root_is_refused(self) -> None:
        with pytest.raises(PackRootNotConfiguredError):
            PackFileReader(roots=[])

    def test_missing_root_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PackRootNotConfiguredError):
            PackFileReader(roots=[tmp_path / "does-not-exist"])

    def test_file_as_root_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "a-file"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(PackRootNotConfiguredError):
            PackFileReader(roots=[target])


class TestPathAdmission:
    @pytest.mark.parametrize(
        "candidate",
        [
            "../escape.json",
            "pack/../../escape.json",
            "/etc/passwd",
            "",
            "   ",
        ],
    )
    def test_traversal_and_absolute_paths_are_refused(
        self, reader: PackFileReader, candidate: str
    ) -> None:
        with pytest.raises(PackPathEscapesRootError):
            reader.resolve(candidate)

    def test_backslash_traversal_is_refused(self, reader: PackFileReader) -> None:
        with pytest.raises(PackPathEscapesRootError):
            reader.resolve(r"pack\..\..\escape.json")

    def test_symlink_pointing_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (root / "link.txt").symlink_to(outside)

        reader = PackFileReader(roots=[root])
        with pytest.raises(PackPathEscapesRootError):
            reader.resolve("link.txt")

    def test_a_contained_path_resolves(self, tmp_path: Path, reader: PackFileReader) -> None:
        (tmp_path / "inside.json").write_text("{}", encoding="utf-8")
        assert reader.resolve("inside.json") == (tmp_path / "inside.json").resolve()


class TestFileAdmission:
    def test_missing_file_is_refused(self, reader: PackFileReader) -> None:
        with pytest.raises(PackFileNotFoundError):
            reader.read_bytes("absent.json")

    def test_directory_is_not_a_readable_file(self, tmp_path: Path, reader: PackFileReader) -> None:
        (tmp_path / "a-directory").mkdir()
        with pytest.raises(PackFileTypeError):
            reader.read_bytes("a-directory")

    def test_oversized_file_is_refused_by_the_configured_ceiling(self, tmp_path: Path) -> None:
        (tmp_path / "big.bin").write_bytes(b"x" * 64)
        reader = PackFileReader(roots=[tmp_path], limits=LoaderLimits(maximum_file_bytes=16))
        with pytest.raises(PackFileTooLargeError):
            reader.read_bytes("big.bin")

    def test_incorrect_byte_hash_is_refused(self, tmp_path: Path, reader: PackFileReader) -> None:
        (tmp_path / "payload.bin").write_bytes(b"actual bytes")
        with pytest.raises(PackHashMismatchError):
            reader.read_bytes("payload.bin", expected_sha256="0" * 64)

    def test_correct_byte_hash_is_accepted(self, tmp_path: Path, reader: PackFileReader) -> None:
        payload = b"actual bytes"
        (tmp_path / "payload.bin").write_bytes(payload)
        from ._helpers import sha256_of

        assert reader.read_bytes("payload.bin", expected_sha256=sha256_of(payload)) == payload


class TestManifestEntryValidation:
    @pytest.mark.parametrize(
        "path",
        ["/absolute.json", "../escape.json", "nested/../../escape.json", "C:/windows.json"],
    )
    def test_a_manifest_cannot_even_describe_a_path_outside_its_pack(self, path: str) -> None:
        with pytest.raises(ValueError, match="relative_path"):
            ManifestEntry(relative_path=path, sha256="0" * 64, size_bytes=0)

    def test_duplicate_manifest_paths_are_refused(self) -> None:
        entry = ManifestEntry(relative_path="a.json", sha256="0" * 64, size_bytes=0)
        with pytest.raises(ValueError, match="repeat a relative_path"):
            make_manifest(entries=(entry, entry))


class TestLoading:
    def test_a_well_formed_pack_loads(self, tmp_path: Path, loader: ObjectivePackLoader) -> None:
        directory, expected = write_pack_directory(tmp_path)

        result = loader.load(pack_directory=directory)

        assert result.pack == expected
        assert result.findings == ()
        assert "pack.json" in result.verified_file_hashes

    def test_loading_is_deterministic_across_repeated_runs(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        directory, _ = write_pack_directory(tmp_path)

        first = loader.load(pack_directory=directory)
        second = loader.load(pack_directory=directory)

        assert first.pack == second.pack
        assert first.verified_file_hashes == second.verified_file_hashes

    def test_a_tampered_pack_document_fails_its_pinned_hash(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        directory, _ = write_pack_directory(tmp_path, corrupt_pack_hash=True)

        with pytest.raises(PackHashMismatchError):
            loader.load(pack_directory=directory)

    def test_a_removed_source_file_is_reported_as_missing(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        directory, _ = write_pack_directory(tmp_path)
        (tmp_path / directory / "sources" / "synthetic.png").unlink()

        with pytest.raises(PackFileNotFoundError):
            loader.load(pack_directory=directory)

    def test_a_smuggled_in_file_is_reported_as_unlisted(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        directory, _ = write_pack_directory(tmp_path)
        (tmp_path / directory / "sources" / "extra.txt").write_text("late", encoding="utf-8")

        result = loader.load(pack_directory=directory)

        assert result.has_errors
        assert [finding.reason_code for finding in result.findings] == [
            ValidationReasonCode.MANIFEST_ENTRY_UNLISTED
        ]
        assert result.findings[0].subject_id == "sources/extra.txt"

    def test_a_size_that_disagrees_with_the_manifest_is_refused(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        """Every hash stays correct, so only the size check can catch this."""
        directory, _ = write_pack_directory(tmp_path)
        manifest_path = tmp_path / directory / "manifest.json"
        manifest = ObjectivePackManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )

        inflated = tuple(
            entry.model_copy(update={"size_bytes": entry.size_bytes + 1})
            if entry.relative_path == "sources/synthetic.pdf"
            else entry
            for entry in manifest.entries
        )
        manifest_path.write_text(
            manifest.model_copy(update={"entries": inflated}).model_dump_json(),
            encoding="utf-8",
        )

        with pytest.raises(PackManifestError, match="size mismatch"):
            loader.load(pack_directory=directory)

    def test_malformed_json_is_refused(self, tmp_path: Path, loader: ObjectivePackLoader) -> None:
        directory, _ = write_pack_directory(tmp_path)
        (tmp_path / directory / "manifest.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(PackManifestError):
            loader.load(pack_directory=directory)

    def test_a_manifest_that_is_not_a_manifest_is_refused(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        directory, _ = write_pack_directory(tmp_path)
        (tmp_path / directory / "manifest.json").write_text(
            '{"unexpected": true}', encoding="utf-8"
        )

        with pytest.raises(PackSchemaError):
            loader.load(pack_directory=directory)

    def test_objective_version_mismatch_between_manifest_and_pack_is_refused(
        self, tmp_path: Path, loader: ObjectivePackLoader
    ) -> None:
        pack = make_pack()
        directory, _ = write_pack_directory(tmp_path, pack=pack)

        manifest_path = tmp_path / directory / "manifest.json"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                pack.objective_ref, OTHER_OBJECTIVE_REF
            ),
            encoding="utf-8",
        )

        with pytest.raises(PackManifestError, match="objective_ref"):
            loader.load(pack_directory=directory)

    def test_a_pack_spanning_two_objective_versions_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="never spans two objective versions"):
            make_pack(manifest=make_manifest(objective_ref=OTHER_OBJECTIVE_REF))
