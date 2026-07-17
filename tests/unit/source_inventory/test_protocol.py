from __future__ import annotations

from personal_lms.source_inventory import SourceInventoryCatalog, SQLiteSourceInventory


def test_sqlite_implementation_satisfies_the_protocol() -> None:
    store = SQLiteSourceInventory.open(":memory:")
    assert isinstance(store, SourceInventoryCatalog)
    store.close()


def test_incomplete_implementation_fails_runtime_protocol_check() -> None:
    class _NotACatalog:
        def initialize_schema(self) -> None: ...
        def add_source(self, source: object) -> object: ...

    assert not isinstance(_NotACatalog(), SourceInventoryCatalog)
