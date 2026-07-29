"""Frozen-schema view of a gate report, plus a stdlib subset validator.

Two jobs, both narrow.

**The primary and sidecar.** ``GateReport`` is the internal record and is deliberately
richer than the frozen ``tests/linchpin/schemas/gate-report.schema.json``:
it distinguishes ``blocked`` from ``not_run`` and carries a run id, a
fixture-authority state, and per-check detail. The frozen schema admits
three statuses (``pass``/``fail``/``deferred``), forbids unlisted
properties, and requires a non-null ``expected_ref`` on every check.
``frozen_schema_view`` projects the internal report onto that shape. The
canonical projection is the primary observed artifact; the richer internal
record is retained in a separately immutable sidecar bound to the primary's
exact SHA-256.

**The validator.** Validating against the frozen schema needs a JSON
Schema implementation, and adding one is a dependency decision that is not
this run's to make. ``validate_against_frozen_schema`` implements exactly
the keyword subset the frozen document uses and **fails closed on any
keyword it does not implement**, so it can never silently approve a
schema it did not fully understand. It is a checker for one known
document, not a general JSON Schema library.

The projection under-claims, always
-----------------------------------

Only an internal ``passed`` becomes ``pass``. Every other status —
``failed``, ``blocked``, ``not_run`` — becomes ``fail``, because the
frozen enum has no way to say "this check could not run" and reporting a
non-pass as a pass would be a lie in the direction that matters. The
information is not lost: the internal report keeps the exact status, and
``STATUS_PROJECTION`` records the mapping so a reviewer can see precisely
what was collapsed.

The frozen schema cannot
express ``blocked`` or ``not_run``, has no slot for ``run_id``, and
requires ``expected_ref`` on checks that have no approved expectation to
point at. Reconciling that is a content/contract decision, not an
engineering one. Publication therefore preserves both forms without widening
the frozen schema or allowing the richer form to masquerade as the primary.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from personal_lms.labs.ccna_mastery.gates import GateCheckStatus, GateReport, GateStatus
from personal_lms.objective_packs.hashing import canonical_json

__all__ = [
    "FROZEN_DEFERRABLE_CHECK_IDS",
    "PROVENANCE_ARTIFACT_KIND",
    "STATUS_PROJECTION",
    "ReportProjectionError",
    "ReportProvenanceError",
    "SchemaViolation",
    "canonical_frozen_schema_bytes",
    "frozen_schema_view",
    "provenance_sidecar_bytes",
    "report_from_bound_provenance",
    "validate_against_frozen_schema",
]

#: How an internal status is rendered in the frozen three-value enum.
#: Only ``passed`` maps to ``pass``; everything else collapses to
#: ``fail`` — including ``blocked`` and ``not_run``, which the frozen
#: enum cannot express. ``deferred`` survives only for the two check ids
#: the frozen schema allowlists, and is never used to stand in for a
#: blocked check, a pending approval, or one that never ran.
STATUS_PROJECTION: dict[str, str] = {
    GateStatus.PASSED.value: "pass",
    GateStatus.FAILED.value: "fail",
    GateStatus.BLOCKED.value: "fail",
    GateStatus.NOT_RUN.value: "fail",
    GateStatus.UNAPPROVED_AUTHORITY.value: "fail",
    GateCheckStatus.PASSED.value: "pass",
    GateCheckStatus.FAILED.value: "fail",
    GateCheckStatus.BLOCKED.value: "fail",
    GateCheckStatus.NOT_RUN.value: "fail",
    GateCheckStatus.DEFERRED.value: "deferred",
}

#: The only check ids the frozen schema permits to report ``deferred``,
#: read straight from that schema's own enum. Deferral is not a way to
#: describe a blocked check, a pending approval, a failure, or a required
#: check that never ran — every one of those projects to ``fail``.
FROZEN_DEFERRABLE_CHECK_IDS: frozenset[str] = frozenset(
    {"G3-RI-QWEN-01", "week-scale-retest-bank-comparison"}
)


class ReportProjectionError(ValueError):
    """A report could not be projected onto the frozen schema.

    Raised rather than papered over. Every case that reaches it is one
    where producing a document would mean inventing a value the report
    does not have, and a frozen-schema artifact containing an invented
    field is worse than no artifact.
    """


class ReportProvenanceError(ReportProjectionError):
    """The rich report sidecar is missing, malformed, or not bound to its primary."""


#: Stable discriminator for the immutable rich-report sidecar.
PROVENANCE_ARTIFACT_KIND = "personal-lms.gate-report-provenance"


def frozen_schema_view(report: GateReport) -> dict[str, Any]:
    """``report`` projected onto the frozen gate-report schema's shape.

    Fails closed on a check with no ``expected_ref``. The frozen schema
    requires a non-empty reference on every check, and an earlier revision
    supplied the literal ``"(no approved expectation)"`` when one was
    missing — a string that satisfies the schema while pointing at
    nothing, in a document whose entire purpose is to be comparable
    against something. References now come from the trusted gate
    definition at report-assembly time, so their absence is a contract gap
    to surface, not a hole to fill.

    The manifest hash in the projected view is always
    ``report.fixture_manifest_hash`` — the value actually bound to this
    report — with no caller override. An earlier revision accepted an
    optional ``manifest_sha256`` keyword argument that, when supplied,
    silently replaced the report's own hash in the rendered document. No
    call site ever used it, and its only effect was a manifest-hash
    substitution primitive: a caller could make a report about one
    fixture tree render as though it cited another's hash.
    """
    checks: list[dict[str, Any]] = []
    for check in report.checks:
        if not check.expected_ref:
            raise ReportProjectionError(
                f"check {check.check_id!r} carries no expectation reference; the frozen "
                "schema requires one on every check, and this projection will not "
                "invent a value that points at nothing"
            )
        if (
            check.status is GateCheckStatus.DEFERRED
            and check.check_id not in FROZEN_DEFERRABLE_CHECK_IDS
        ):
            raise ReportProjectionError(
                f"check {check.check_id!r} reports 'deferred', which the frozen schema "
                f"permits only for {sorted(FROZEN_DEFERRABLE_CHECK_IDS)}"
            )
        checks.append(
            {
                "check_id": check.check_id,
                "required": check.required,
                "status": STATUS_PROJECTION[check.status.value],
                "expected_ref": check.expected_ref,
                "observed_hash": check.observed_hash,
                "reason_code": check.reason_code,
            }
        )

    return {
        "schema_version": report.schema_version,
        "gate_id": report.gate_id.value,
        "status": STATUS_PROJECTION[report.status.value],
        "fixture_manifest_sha256": report.fixture_manifest_hash,
        "code_revision": report.code_revision,
        "started_at": report.started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": report.finished_at.isoformat().replace("+00:00", "Z"),
        "elapsed_seconds": report.elapsed_milliseconds // 1_000,
        "checks": checks,
    }


def canonical_frozen_schema_bytes(report: GateReport) -> bytes:
    """The literal canonical bytes published as the primary gate report."""
    return canonical_json(frozen_schema_view(report)).encode("utf-8")


def provenance_sidecar_bytes(report: GateReport, *, primary_bytes: bytes) -> bytes:
    """Canonical rich provenance bound to the exact primary-report bytes."""
    payload = {
        "artifact_kind": PROVENANCE_ARTIFACT_KIND,
        "schema_version": "1.0",
        "primary_sha256": hashlib.sha256(primary_bytes).hexdigest(),
        "report": json.loads(report.to_canonical_json()),
    }
    return canonical_json(payload).encode("utf-8")


def report_from_bound_provenance(*, primary_bytes: bytes, provenance_bytes: bytes) -> GateReport:
    """Restore and verify the rich report bound to ``primary_bytes``.

    The sidecar binds the exact primary hash, round-trips through the rich
    model without discarded fields, carries only trusted expectation refs,
    and must reproduce the primary's exact canonical frozen-schema bytes.
    """
    try:
        payload = json.loads(provenance_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportProvenanceError(f"the provenance sidecar is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportProvenanceError("the provenance sidecar must be a JSON object")

    required_fields = {"artifact_kind", "schema_version", "primary_sha256", "report"}
    if set(payload) != required_fields:
        missing = sorted(required_fields - set(payload))
        extra = sorted(set(payload) - required_fields)
        raise ReportProvenanceError(
            f"the provenance sidecar has the wrong fields; missing={missing}, extra={extra}"
        )
    if payload["artifact_kind"] != PROVENANCE_ARTIFACT_KIND:
        raise ReportProvenanceError(
            f"the provenance sidecar has unknown artifact_kind {payload['artifact_kind']!r}"
        )
    if payload["schema_version"] != "1.0":
        raise ReportProvenanceError(
            f"the provenance sidecar has unsupported schema_version {payload['schema_version']!r}"
        )

    expected_primary_hash = hashlib.sha256(primary_bytes).hexdigest()
    if payload["primary_sha256"] != expected_primary_hash:
        raise ReportProvenanceError(
            "the provenance sidecar does not bind the exact primary report: "
            f"expected {expected_primary_hash}, found {payload['primary_sha256']!r}"
        )

    raw_report = payload["report"]
    if not isinstance(raw_report, dict):
        raise ReportProvenanceError("the provenance sidecar's report must be a JSON object")
    model_payload = {
        key: value for key, value in raw_report.items() if key in GateReport.model_fields
    }
    try:
        report = GateReport.model_validate(model_payload)
    except ValidationError as exc:
        raise ReportProvenanceError(
            f"the provenance sidecar does not contain a well-formed gate report: {exc}"
        ) from exc

    canonical_report = json.loads(report.to_canonical_json())
    if raw_report != canonical_report:
        raise ReportProvenanceError(
            "the provenance sidecar's rich report is not its exact canonical form"
        )

    for check in report.checks:
        try:
            trusted_ref = report.definition.expectation_ref(check.check_id)
        except KeyError as exc:
            raise ReportProvenanceError(
                f"the provenance sidecar contains unknown check {check.check_id!r}"
            ) from exc
        if check.expected_ref != trusted_ref:
            raise ReportProvenanceError(
                f"the provenance sidecar's check {check.check_id!r} carries expectation "
                f"reference {check.expected_ref!r}, not the trusted {trusted_ref!r}"
            )

    try:
        expected_primary = canonical_frozen_schema_bytes(report)
    except ReportProjectionError as exc:
        raise ReportProvenanceError(
            f"the provenance sidecar cannot reproduce a frozen-schema primary: {exc}"
        ) from exc
    if primary_bytes != expected_primary:
        raise ReportProvenanceError(
            "the primary report is not the exact canonical frozen-schema view of "
            "the bound provenance"
        )
    return report


@dataclass(frozen=True, slots=True)
class SchemaViolation:
    """One place an instance failed the frozen schema."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


#: Every keyword this checker implements. A schema using anything else is
#: refused rather than partially validated — an unimplemented keyword
#: silently ignored is how a validator ends up approving what it never
#: checked.
_SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "pattern",
        "format",
        "minLength",
        "minimum",
        "minItems",
        "required",
        "properties",
        "items",
        "additionalProperties",
        "unevaluatedProperties",
        "allOf",
        "oneOf",
        "not",
        "if",
        "then",
    }
)

#: Every ``format`` *value* this checker actually enforces. The keyword
#: itself is declared supported above, but a keyword whose value this
#: module does not implement is exactly the "silently ignored" failure
#: mode ``_assert_supported`` exists to prevent — an earlier revision
#: declared ``format`` supported without checking any format value at
#: all, so a schema using it validated nothing while looking checked.
_SUPPORTED_FORMATS = frozenset({"date-time"})


def _is_rfc3339_date_time(value: str) -> bool:
    """Whether ``value`` is a real, calendar-valid RFC3339 date-time.

    Stricter than the frozen schema's own regex ``pattern`` sibling on the
    same fields: a regex confirms the shape (four digits, a dash, two
    digits, ...) but not that the result names a real instant —
    ``"2026-13-45T99:99:99Z"`` matches the shape. Delegating to
    ``datetime.fromisoformat`` gets genuine calendar validity for free.
    """
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def validate_against_frozen_schema(
    instance: object, *, schema_bytes: bytes
) -> tuple[SchemaViolation, ...]:
    """Validate ``instance`` against the frozen schema. Empty means valid.

    Raises ``ValueError`` when the schema uses a keyword this checker does
    not implement, so an unrecognized construct can never be mistaken for
    a satisfied one.
    """
    schema = json.loads(schema_bytes.decode("utf-8"))
    if not isinstance(schema, dict):
        raise ValueError("a JSON Schema document must be an object")
    _assert_supported(schema)
    return tuple(_validate(instance, schema, root=schema, path="#"))


#: Keywords whose value is itself a schema.
_SUBSCHEMA_KEYWORDS = frozenset(
    {"items", "not", "if", "then", "additionalProperties", "unevaluatedProperties"}
)
#: Keywords whose value is a *map of names* to schemas. Their keys are
#: property names, not keywords, so they are never keyword-checked — a
#: property legitimately named ``required`` or ``status`` is not a keyword
#: appearing in the wrong place.
_NAME_MAP_KEYWORDS = frozenset({"properties", "$defs"})
#: Keywords whose value is a list of schemas.
_SCHEMA_LIST_KEYWORDS = frozenset({"allOf", "oneOf"})


def _assert_supported(schema: object) -> None:
    """Walk the schema structurally, refusing any keyword not implemented.

    Position-aware rather than a blind recursive scan: it knows which
    keywords hold subschemas, which hold name-to-schema maps, and which
    hold literal values, so it checks keyword names only where keywords
    can actually appear.
    """
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError("a JSON Schema node must be an object or a boolean")

    unsupported = sorted(set(schema) - _SUPPORTED_KEYWORDS)
    if unsupported:
        raise ValueError(f"unsupported JSON Schema keyword(s): {unsupported}")

    format_value = schema.get("format")
    if format_value is not None and format_value not in _SUPPORTED_FORMATS:
        raise ValueError(f"unsupported JSON Schema format {format_value!r}")

    for keyword, value in schema.items():
        if keyword in _NAME_MAP_KEYWORDS and isinstance(value, dict):
            for subschema in value.values():
                _assert_supported(subschema)
        elif keyword in _SCHEMA_LIST_KEYWORDS and isinstance(value, list):
            for subschema in value:
                _assert_supported(subschema)
        elif keyword in _SUBSCHEMA_KEYWORDS and isinstance(value, dict | bool):
            _assert_supported(value)


def _validate(  # noqa: C901 - one dispatch per keyword; splitting hides the shape
    instance: object, schema: Any, *, root: dict[str, Any], path: str
) -> list[SchemaViolation]:
    if isinstance(schema, bool):
        return [] if schema else [SchemaViolation(path, "schema forbids any value here")]

    violations: list[SchemaViolation] = []

    if "$ref" in schema:
        return _validate(instance, _resolve(schema["$ref"], root), root=root, path=path)

    if "const" in schema and instance != schema["const"]:
        violations.append(SchemaViolation(path, f"must equal {schema['const']!r}"))
    if "enum" in schema and instance not in schema["enum"]:
        violations.append(SchemaViolation(path, f"must be one of {schema['enum']!r}"))

    expected_type = schema.get("type")
    if expected_type is not None and not _has_type(instance, expected_type):
        violations.append(SchemaViolation(path, f"must be of type {expected_type!r}"))
        return violations

    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(pattern, instance):
            violations.append(SchemaViolation(path, f"must match {pattern!r}"))
        minimum_length = schema.get("minLength")
        if minimum_length is not None and len(instance) < minimum_length:
            violations.append(
                SchemaViolation(path, f"must be at least {minimum_length} characters")
            )
        format_name = schema.get("format")
        if format_name == "date-time" and not _is_rfc3339_date_time(instance):
            violations.append(SchemaViolation(path, "must be a valid RFC3339 date-time"))

    if isinstance(instance, int) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            violations.append(SchemaViolation(path, f"must be >= {minimum}"))

    if isinstance(instance, list):
        minimum_items = schema.get("minItems")
        if minimum_items is not None and len(instance) < minimum_items:
            violations.append(SchemaViolation(path, f"must hold at least {minimum_items} item(s)"))
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                violations.extend(_validate(item, item_schema, root=root, path=f"{path}/{index}"))

    if isinstance(instance, dict):
        violations.extend(_validate_object(instance, schema, root=root, path=path))

    for subschema in schema.get("allOf", []):
        violations.extend(_validate(instance, subschema, root=root, path=path))

    if "oneOf" in schema:
        matches = sum(
            1
            for subschema in schema["oneOf"]
            if not _validate(instance, subschema, root=root, path=path)
        )
        if matches != 1:
            violations.append(
                SchemaViolation(path, f"must match exactly one branch, matched {matches}")
            )

    if "not" in schema and not _validate(instance, schema["not"], root=root, path=path):
        violations.append(SchemaViolation(path, "must not match the forbidden schema"))

    if "if" in schema:
        branch = "then" if not _validate(instance, schema["if"], root=root, path=path) else None
        if branch is not None and branch in schema:
            violations.extend(_validate(instance, schema[branch], root=root, path=path))

    return violations


def _validate_object(
    instance: dict[str, Any], schema: dict[str, Any], *, root: dict[str, Any], path: str
) -> list[SchemaViolation]:
    violations: list[SchemaViolation] = []
    for name in schema.get("required", []):
        if name not in instance:
            violations.append(SchemaViolation(path, f"missing required property {name!r}"))

    properties = schema.get("properties", {})
    for name, subschema in properties.items():
        if name in instance:
            violations.extend(
                _validate(instance[name], subschema, root=root, path=f"{path}/{name}")
            )

    # ``additionalProperties: false`` and ``unevaluatedProperties: false``
    # are treated the same way here, which is exact for this schema: its
    # only unevaluated-property use sits on an object whose properties are
    # all declared locally.
    for keyword in ("additionalProperties", "unevaluatedProperties"):
        if schema.get(keyword) is False:
            for name in sorted(set(instance) - set(properties)):
                violations.append(SchemaViolation(path, f"property {name!r} is not permitted"))
    return violations


def _resolve(reference: str, root: dict[str, Any]) -> Any:
    if not reference.startswith("#/"):
        raise ValueError(f"only local $ref pointers are supported, got {reference!r}")
    node: Any = root
    for segment in reference[2:].split("/"):
        node = node[segment]
    return node


def _has_type(instance: object, expected: str | list[str]) -> bool:
    names = [expected] if isinstance(expected, str) else expected
    return any(_is_type(instance, name) for name in names)


def _is_type(instance: object, name: str) -> bool:
    match name:
        case "object":
            return isinstance(instance, dict)
        case "array":
            return isinstance(instance, list)
        case "string":
            return isinstance(instance, str)
        case "boolean":
            return isinstance(instance, bool)
        case "integer":
            return isinstance(instance, int) and not isinstance(instance, bool)
        case "number":
            return isinstance(instance, int | float) and not isinstance(instance, bool)
        case "null":
            return instance is None
        case _:
            raise ValueError(f"unsupported JSON Schema type {name!r}")
