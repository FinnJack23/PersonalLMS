from __future__ import annotations

import ast
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from personal_lms.domain import PrivacyClassification
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceAuthorityLevel,
    SourceInventoryProcessingStatus,
    SourceInventoryRecord,
    SourceLocation,
    SourceLocatorKind,
    SourceMediaType,
    SourceRightsStatus,
    SourceVersion,
    derive_source_id,
)
from personal_lms.source_inventory import sqlite as sqlite_module
from personal_lms.source_inventory.errors import (
    SourceAlreadyExistsError,
    SourceInventoryContractError,
    SourceLocationConflictError,
    SourceNotFoundError,
    SourceVersionAlreadyExistsError,
)
from personal_lms.source_inventory.protocol import SourceInventoryFilter
from personal_lms.source_inventory.sqlite import SQLiteSourceInventory

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _record(**overrides: object) -> SourceInventoryRecord:
    locator = overrides.pop("locator", "https://example.com/a")
    locator_kind = overrides.get("locator_kind", SourceLocatorKind.WEB_URL)
    defaults: dict[str, object] = {
        "source_id": derive_source_id(canonical_locator=str(locator)),
        "locator_kind": locator_kind,
        "locator": locator,
        "media_type": SourceMediaType.HTML,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return SourceInventoryRecord.model_validate(defaults)


def _version(**overrides: object) -> SourceVersion:
    defaults: dict[str, object] = {
        "source_id": uuid4(),
        "content_hash_sha256": "a" * 64,
        "observed_at": _NOW,
    }
    defaults.update(overrides)
    return SourceVersion.model_validate(defaults)


def _location(**overrides: object) -> SourceLocation:
    defaults: dict[str, object] = {
        "source_id": uuid4(),
        "locator_kind": SourceLocatorKind.WEB_URL,
        "locator": "https://example.com/other",
        "first_observed_at": _NOW,
        "last_observed_at": _NOW,
    }
    defaults.update(overrides)
    return SourceLocation.model_validate(defaults)


@pytest.fixture
def store() -> SQLiteSourceInventory:
    instance = SQLiteSourceInventory.open(":memory:")
    instance.initialize_schema()
    return instance


# --- migration -----------------------------------------------------------------


def test_fresh_in_memory_migration_succeeds() -> None:
    instance = SQLiteSourceInventory.open(":memory:")
    instance.initialize_schema()
    instance.close()


def test_repeated_migration_is_idempotent(store: SQLiteSourceInventory) -> None:
    store.initialize_schema()
    store.initialize_schema()


def test_schema_version_recorded(store: SQLiteSourceInventory) -> None:
    row = store._connection.execute("SELECT version FROM schema_migrations").fetchone()
    assert row["version"] == 1


def test_foreign_keys_enabled(store: SQLiteSourceInventory) -> None:
    row = store._connection.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            "INSERT INTO source_versions "
            "(version_id, source_id, content_hash_sha256, observed_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), str(uuid4()), "a" * 64, _NOW.isoformat(), "{}"),
        )


def test_failed_migration_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = SQLiteSourceInventory.open(":memory:")
    broken_statements = (*sqlite_module._SCHEMA_STATEMENTS, "THIS IS NOT VALID SQL")
    monkeypatch.setattr(sqlite_module, "_SCHEMA_STATEMENTS", broken_statements)

    with pytest.raises(sqlite_module.SourceInventoryStorageError):
        instance.initialize_schema()

    tables = {
        row["name"]
        for row in instance._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "sources" not in tables
    assert (
        "schema_migrations" not in tables
        or instance._connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    )


def test_unsupported_future_schema_version_fails_safely(store: SQLiteSourceInventory) -> None:
    store._connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (999, ?)",
        (_NOW.isoformat(),),
    )
    store._connection.commit()

    with pytest.raises(SourceInventoryContractError, match="unsupported_schema_version"):
        store.initialize_schema()


# --- CRUD ------------------------------------------------------------------------


def test_add_and_retrieve_source(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    fetched = store.get_source(record.source_id)
    assert fetched == record


def test_duplicate_source_id_rejected(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    duplicate = _record(locator="https://example.com/different", source_id=record.source_id)
    with pytest.raises(SourceAlreadyExistsError):
        store.add_source(duplicate)


def test_locator_conflict_handled_deterministically(store: SQLiteSourceInventory) -> None:
    first = _record()
    store.add_source(first)
    second = _record(source_id=derive_source_id(content_hash_sha256="b" * 64))
    with pytest.raises(SourceLocationConflictError):
        store.add_source(second)


def test_unknown_source_raises_typed_error(store: SQLiteSourceInventory) -> None:
    with pytest.raises(SourceNotFoundError):
        store.get_source(uuid4())


def test_update_round_trip(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    updated = record.model_copy(
        update={"title": "New Title", "updated_at": _NOW + timedelta(hours=1)}
    )
    store.update_source(updated)
    fetched = store.get_source(record.source_id)
    assert fetched.title == "New Title"


def test_immutable_created_at_cannot_be_changed(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    tampered = record.model_copy(
        update={"created_at": _NOW - timedelta(days=1), "updated_at": _NOW}
    )
    with pytest.raises(SourceInventoryContractError):
        store.update_source(tampered)


def test_list_order_is_deterministic(store: SQLiteSourceInventory) -> None:
    records = [_record(locator=f"https://example.com/{i}", source_id=uuid4()) for i in range(5)]
    for record in records:
        store.add_source(record)
    listed = store.list_sources()
    assert [r.source_id for r in listed] == sorted(r.source_id for r in records)


def test_filters_work_independently(store: SQLiteSourceInventory) -> None:
    a = _record(
        locator="https://example.com/a",
        source_id=uuid4(),
        media_type=SourceMediaType.HTML,
        approval_status=SourceApprovalStatus.APPROVED,
    )
    b = _record(
        locator="https://example.com/b",
        source_id=uuid4(),
        media_type=SourceMediaType.PDF,
        approval_status=SourceApprovalStatus.UNREVIEWED,
    )
    store.add_source(a)
    store.add_source(b)

    by_media = store.list_sources(filters=SourceInventoryFilter(media_type=SourceMediaType.PDF))
    assert [r.source_id for r in by_media] == [b.source_id]

    by_approval = store.list_sources(
        filters=SourceInventoryFilter(approval_status=SourceApprovalStatus.APPROVED)
    )
    assert [r.source_id for r in by_approval] == [a.source_id]


def test_filters_combine_correctly(store: SQLiteSourceInventory) -> None:
    a = _record(
        locator="https://example.com/a",
        source_id=uuid4(),
        media_type=SourceMediaType.PDF,
        knowledge_domains=("networking",),
    )
    b = _record(
        locator="https://example.com/b",
        source_id=uuid4(),
        media_type=SourceMediaType.PDF,
        knowledge_domains=("security",),
    )
    store.add_source(a)
    store.add_source(b)

    combined = store.list_sources(
        filters=SourceInventoryFilter(media_type=SourceMediaType.PDF, knowledge_domain="networking")
    )
    assert [r.source_id for r in combined] == [a.source_id]


def test_privacy_value_round_trips(store: SQLiteSourceInventory) -> None:
    record = _record(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    store.add_source(record)
    fetched = store.get_source(record.source_id)
    assert fetched.privacy_classification is PrivacyClassification.RESTRICTED_LOCAL_ONLY


def test_status_values_round_trip(store: SQLiteSourceInventory) -> None:
    record = _record(
        processing_status=SourceInventoryProcessingStatus.CLASSIFIED,
        approval_status=SourceApprovalStatus.APPROVED,
        rights_status=SourceRightsStatus.LICENSED,
        authority_level=SourceAuthorityLevel.OFFICIAL,
    )
    store.add_source(record)
    fetched = store.get_source(record.source_id)
    assert fetched.processing_status is SourceInventoryProcessingStatus.CLASSIFIED
    assert fetched.approval_status is SourceApprovalStatus.APPROVED
    assert fetched.rights_status is SourceRightsStatus.LICENSED
    assert fetched.authority_level is SourceAuthorityLevel.OFFICIAL


def test_tuple_metadata_round_trips_without_duplicates(store: SQLiteSourceInventory) -> None:
    record = _record(certifications=("CCNA", "Network+"), topics=("OSPF", "OSPF", "BGP"))
    store.add_source(record)
    fetched = store.get_source(record.source_id)
    assert fetched.certifications == ("CCNA", "Network+")
    assert fetched.topics == ("BGP", "OSPF")


# --- versions --------------------------------------------------------------------


def test_add_version(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    version = _version(source_id=record.source_id)
    store.add_version(version)
    versions = store.list_versions(record.source_id)
    assert versions == (version,)


def test_list_versions_in_deterministic_order(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    later = _version(
        source_id=record.source_id,
        content_hash_sha256="b" * 64,
        observed_at=_NOW + timedelta(hours=1),
    )
    earlier = _version(source_id=record.source_id, content_hash_sha256="a" * 64, observed_at=_NOW)
    store.add_version(later)
    store.add_version(earlier)
    versions = store.list_versions(record.source_id)
    assert [v.content_hash_sha256 for v in versions] == ["a" * 64, "b" * 64]


def test_duplicate_version_rejected(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    version = _version(source_id=record.source_id)
    store.add_version(version)
    duplicate = _version(source_id=record.source_id, content_hash_sha256="a" * 64)
    with pytest.raises(SourceVersionAlreadyExistsError):
        store.add_version(duplicate)


def test_version_for_unknown_source_rejected(store: SQLiteSourceInventory) -> None:
    with pytest.raises(SourceNotFoundError):
        store.add_version(_version(source_id=uuid4()))


def test_prior_versions_remain_immutable(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    first = _version(source_id=record.source_id, content_hash_sha256="a" * 64)
    store.add_version(first)
    second = _version(source_id=record.source_id, content_hash_sha256="b" * 64)
    store.add_version(second)

    versions = {v.content_hash_sha256: v for v in store.list_versions(record.source_id)}
    assert versions["a" * 64] == first


# --- locations ---------------------------------------------------------------------


def test_add_location(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    initial = store.list_locations(record.source_id)
    assert len(initial) == 1
    assert initial[0].is_active is True


def test_move_rename_history_retained(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    moved = record.model_copy(
        update={"locator": "https://example.com/moved", "updated_at": _NOW + timedelta(hours=1)}
    )
    store.update_source(moved)
    locations = store.list_locations(record.source_id)
    assert len(locations) == 2
    canonical_forms = {loc.canonical_locator for loc in locations}
    assert "https://example.com/a" in canonical_forms
    assert "https://example.com/moved" in canonical_forms


def test_active_location_behavior_is_deterministic(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    moved = record.model_copy(
        update={"locator": "https://example.com/moved", "updated_at": _NOW + timedelta(hours=1)}
    )
    store.update_source(moved)
    locations = store.list_locations(record.source_id)
    active = [loc for loc in locations if loc.is_active]
    assert len(active) == 1
    assert active[0].canonical_locator == "https://example.com/moved"


def test_location_conflict_raises_typed_error(store: SQLiteSourceInventory) -> None:
    record = _record()
    store.add_source(record)
    duplicate_id_location = store.list_locations(record.source_id)[0]
    with pytest.raises(SourceLocationConflictError):
        store.add_location(duplicate_id_location)


def test_full_private_path_absent_from_safe_error_text(store: SQLiteSourceInventory) -> None:
    secret_path = "/home/alan/private/very-secret-directory/document.pdf"
    record = _record(
        locator_kind=SourceLocatorKind.FILE_PATH,
        locator=secret_path,
        media_type=SourceMediaType.PDF,
        source_id=uuid4(),
    )
    store.add_source(record)
    duplicate_id_location = store.list_locations(record.source_id)[0]
    with pytest.raises(SourceLocationConflictError) as exc_info:
        store.add_location(duplicate_id_location)
    assert secret_path not in str(exc_info.value)
    assert "very-secret-directory" not in str(exc_info.value)


# --- atomicity and isolation ---------------------------------------------------------


def test_multi_table_write_rollback_works(
    store: SQLiteSourceInventory, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record(certifications=("CCNA",))

    original_replace_tags = SQLiteSourceInventory._replace_tags

    def _broken_replace_tags(self: SQLiteSourceInventory, source: SourceInventoryRecord) -> None:
        original_replace_tags(self, source)
        raise sqlite_module.sqlite3.OperationalError("simulated failure")

    monkeypatch.setattr(SQLiteSourceInventory, "_replace_tags", _broken_replace_tags)

    with pytest.raises(sqlite_module.SourceInventoryStorageError):
        store.add_source(record)

    monkeypatch.undo()
    assert store.find_by_locator(record.locator_kind, record.canonical_locator) is None


def test_sql_parameters_handle_quote_characters_safely(store: SQLiteSourceInventory) -> None:
    record = _record(
        title='It\'s a "tricky" title -- with dashes; DROP TABLE sources;--',
        knowledge_domains=("O'Reilly's Topic",),
    )
    store.add_source(record)
    fetched = store.get_source(record.source_id)
    assert fetched.title == record.title
    assert fetched.knowledge_domains == record.knowledge_domains
    # The table must still exist — no injected statement executed.
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "sources" in tables


def test_no_network_access(store: SQLiteSourceInventory, monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network access is permitted in the source inventory")

    monkeypatch.setattr(socket, "socket", _blocked)
    record = _record()
    store.add_source(record)
    store.get_source(record.source_id)


def test_no_production_filesystem_reads(tmp_path: Path) -> None:
    instance = SQLiteSourceInventory.open(":memory:")
    instance.initialize_schema()
    record = _record()
    instance.add_source(record)
    instance.close()
    assert list(tmp_path.iterdir()) == []


def test_no_environment_dependency(
    store: SQLiteSourceInventory, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("SOURCE_INVENTORY_DB_PATH", "DATABASE_URL", "SQLITE_PATH"):
        monkeypatch.delenv(var, raising=False)
    record = _record()
    store.add_source(record)
    assert store.get_source(record.source_id) == record


def test_no_system_clock_dependency_when_explicit_timestamps_supplied(
    store: SQLiteSourceInventory,
) -> None:
    record = _record()
    store.add_source(record)
    fetched_first = store.get_source(record.source_id)
    fetched_second = store.get_source(record.source_id)
    assert fetched_first == fetched_second == record


def test_no_random_behavior(store: SQLiteSourceInventory) -> None:
    records = [_record(locator=f"https://example.com/rand{i}", source_id=uuid4()) for i in range(3)]
    for record in records:
        store.add_source(record)
    first_listing = store.list_sources()
    second_listing = store.list_sources()
    assert first_listing == second_listing


def test_no_provider_or_model_imports() -> None:
    source = Path(sqlite_module.__file__).read_text()
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in ("httpx", "crewai", "tutor", "source_verification", "providers"):
        assert forbidden not in imported_roots


def test_no_raw_content_stored_in_sqlite(store: SQLiteSourceInventory) -> None:
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for forbidden in ("content", "body", "transcript", "embedding", "fts"):
        assert not any(forbidden in table for table in tables)
