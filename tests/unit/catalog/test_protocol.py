from __future__ import annotations

from pathlib import Path

from personal_lms.catalog import SourceCatalog
from personal_lms.catalog.sqlite import SQLiteSourceCatalog


def test_sqlite_source_catalog_satisfies_protocol(tmp_path: Path) -> None:
    store = SQLiteSourceCatalog.open(tmp_path / "catalog.sqlite3")
    try:
        assert isinstance(store, SourceCatalog)
    finally:
        store.close()


def test_object_missing_search_does_not_satisfy_protocol() -> None:
    class _NotACatalog:
        def initialize_schema(self) -> None: ...
        def upsert_source(self, record: object) -> None: ...
        def get_source(self, source_id: str) -> object | None: ...
        def list_sources(self, *, filters: object | None = None) -> tuple[object, ...]: ...
        def add_relationship(self, relationship: object) -> None: ...
        def list_relationships(self, source_id: str) -> tuple[object, ...]: ...
        def close(self) -> None: ...

    assert not isinstance(_NotACatalog(), SourceCatalog)
