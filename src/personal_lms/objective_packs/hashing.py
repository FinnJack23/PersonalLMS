"""Canonical logical-record hashing.

Two rules drive this module.

**A logical hash is computed from canonical logical records, never from
storage bytes.** Hashing a SQLite file, a WAL segment, or a pickle would
make an evidence hash depend on page layout, vacuum state, insertion
order, and library version — none of which are part of the content being
attested. See ``docs/plans/ccna-mastery-micro-lab/ARCHITECTURE_DELTA.md``.

**Canonicalization happens in Python mode, before any JSON encoder sees
the value.** The earlier version of this module called
``model_dump(mode="json")`` first, which converted a ``frozenset`` field
into a list whose order came from Python's per-process string hash
randomization. The result was a "canonical" hash that differed under every
``PYTHONHASHSEED``, silently invalidating every reproducibility claim
built on it. Dumping in Python mode keeps sets as sets so this module can
sort them by their own canonical form.

Every value kind the domain actually uses is normalized explicitly: enums
to their values, aware datetimes to ISO-8601, UUIDs to their canonical
string, ``Decimal`` to a lossless string, mappings to sorted key order,
sequences to preserved order, and sets to sorted canonical order. Anything
else raises rather than falling back to ``repr`` — hashing an object's
memory address would produce a digest that means nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID

from pydantic import BaseModel

__all__ = [
    "canonical_bytes",
    "canonical_json",
    "hash_record",
    "hash_records",
]


class UnhashableValueError(TypeError):
    """A value has no defined canonical form.

    Raised instead of guessing. A silent fallback would produce a stable
    digest for an unstable value, which is worse than failing.
    """


def _canonicalize(value: Any) -> Any:
    """Reduce ``value`` to JSON-safe, order-stable primitives.

    Ordering of the checks matters: ``bool`` is a subclass of ``int`` and
    ``Enum`` members are frequently ``str`` subclasses (``StrEnum``), so
    the more specific cases are tested first.
    """
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, BaseModel):
        # Python mode, deliberately: json mode would pre-flatten sets into
        # nondeterministically ordered lists before this function could
        # sort them. See the module docstring.
        return _canonicalize(value.model_dump(mode="python"))

    if isinstance(value, Enum):
        return _canonicalize(value.value)

    if isinstance(value, (int, str)):
        return value

    if isinstance(value, float):
        # Floats have no canonical decimal form; a caller wanting an exact
        # value should use Decimal or basis-point integers, which is what
        # every score in this codebase already does.
        raise UnhashableValueError(
            "float values have no canonical form; use Decimal or integer basis points"
        )

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise UnhashableValueError("non-finite Decimal values have no canonical form")
        return str(value)

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise UnhashableValueError(
                "naive datetimes have no canonical form; use a timezone-aware value"
            )
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, (bytes, bytearray)):
        # Content-addressed by digest rather than embedded, so a canonical
        # form never carries raw payload bytes.
        return "sha256:" + hashlib.sha256(bytes(value)).hexdigest()

    if isinstance(value, PurePath):
        return value.as_posix()

    if isinstance(value, Mapping):
        canonical_items = [
            (_canonical_key(key), _canonicalize(item)) for key, item in value.items()
        ]
        canonical_items.sort(key=lambda pair: pair[0])
        _reject_duplicate_keys(canonical_items)
        return dict(canonical_items)

    if isinstance(value, (set, frozenset)):
        # Sort by canonical *serialized* form so ordering never depends on
        # the members' runtime hashes.
        return sorted((_canonicalize(item) for item in value), key=_stable_sort_key)

    if isinstance(value, Sequence):
        # Sequence order is meaningful and is preserved exactly.
        return [_canonicalize(item) for item in value]

    if isinstance(value, Iterable):
        raise UnhashableValueError(
            f"{type(value).__name__} is iterable but has no defined canonical order; "
            "convert it to a list or a set first"
        )

    raise UnhashableValueError(f"{type(value).__name__} has no canonical form")


def _canonical_key(key: Any) -> str:
    """A mapping key's canonical string form.

    Keys become strings because JSON object keys are strings. An enum key
    canonicalizes to its value, so ``{ObjectiveFacet.CONCEPT: 1}`` and
    ``{"concept": 1}`` agree — which is what makes a ``dict`` field
    round-trip stably.
    """
    canonical = _canonicalize(key)
    if isinstance(canonical, str):
        return canonical
    if isinstance(canonical, (int, bool)):
        return str(canonical)
    raise UnhashableValueError(f"mapping keys must canonicalize to a scalar, got {key!r}")


def _reject_duplicate_keys(items: list[tuple[str, Any]]) -> None:
    """Two distinct keys must not collapse to one canonical key.

    ``{1: "a", "1": "b"}`` would otherwise silently lose a member and
    produce the same digest as a different record.
    """
    seen: set[str] = set()
    for key, _ in items:
        if key in seen:
            raise UnhashableValueError(
                f"two mapping keys canonicalize to the same string {key!r}; "
                "the record has no unambiguous canonical form"
            )
        seen.add(key)


def _stable_sort_key(canonical_value: Any) -> str:
    """Order set members by their own canonical serialization."""
    return json.dumps(canonical_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json(value: Any) -> str:
    """The canonical JSON text for ``value``.

    Deterministic across processes, runs, and hash seeds: keys sorted, set
    members sorted by canonical form, separators tight, no trailing
    whitespace.
    """
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 encoding of ``canonical_json(value)``."""
    return canonical_json(value).encode("utf-8")


def hash_record(value: Any) -> str:
    """SHA-256 (lowercase hex) of one record's canonical form."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def hash_records(values: Iterable[Any], *, sort: bool = True) -> str:
    """SHA-256 over a collection of records.

    ``sort=True`` (the default) hashes the collection as a *set*: canonical
    forms are sorted first, so the same records in a different order
    produce the same digest. Pass ``sort=False`` when sequence order is
    itself part of what is being attested (an ordered item list, an event
    stream).
    """
    canonical = [canonical_json(value) for value in values]
    if sort:
        canonical.sort()
    digest = hashlib.sha256()
    for entry in canonical:
        # Length-prefix each entry so concatenation is unambiguous:
        # ["ab", "c"] and ["a", "bc"] must not collide.
        encoded = entry.encode("utf-8")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b":")
        digest.update(encoded)
    return digest.hexdigest()
