"""Typed errors for Objective Pack loading.

Every error carries a stable ``reason_code`` so a gate report can cite it
without parsing prose, mirroring the reason-code convention already used
by ``domain.objective_packs.ValidationReasonCode``.

Error messages never embed the absolute path that failed. A configured
root may itself be sensitive, and these messages reach logs and gate
reports — the same rule ``domain.source_inventory.normalize_locator``
follows for locators. Callers that legitimately need the path already
have it.
"""

from __future__ import annotations


class ObjectivePackError(Exception):
    """Base class for every Objective Pack loading failure."""

    reason_code = "objective_pack_error"


class PackRootNotConfiguredError(ObjectivePackError):
    """A loader was constructed with no configured root, or an unusable one."""

    reason_code = "pack_root_not_configured"


class PackPathEscapesRootError(ObjectivePackError):
    """A requested path resolved outside every configured root.

    Raised for traversal segments, absolute paths, and symlinks pointing
    out of the root alike — the check is on the *resolved* location, not
    on the spelling of the input.
    """

    reason_code = "pack_path_escapes_root"


class PackFileNotFoundError(ObjectivePackError):
    """A manifest-listed file does not exist under the configured root."""

    reason_code = "pack_file_not_found"


class PackFileTypeError(ObjectivePackError):
    """A path exists but is not a regular file (a directory, socket, or device)."""

    reason_code = "pack_file_type_invalid"


class PackFileTooLargeError(ObjectivePackError):
    """A file exceeds the loader's configured maximum size.

    Enforced before the bytes are read, so an oversized file is refused
    rather than buffered.
    """

    reason_code = "pack_file_too_large"


class PackHashMismatchError(ObjectivePackError):
    """A file's actual SHA-256 does not match the hash the manifest pins."""

    reason_code = "pack_hash_mismatch"


class PackManifestError(ObjectivePackError):
    """A manifest is malformed, or disagrees with the files on disk."""

    reason_code = "pack_manifest_invalid"


class PackSchemaError(ObjectivePackError):
    """A pack document failed strict schema validation."""

    reason_code = "pack_schema_invalid"


__all__ = [
    "ObjectivePackError",
    "PackFileNotFoundError",
    "PackFileTooLargeError",
    "PackFileTypeError",
    "PackHashMismatchError",
    "PackManifestError",
    "PackPathEscapesRootError",
    "PackRootNotConfiguredError",
    "PackSchemaError",
]
