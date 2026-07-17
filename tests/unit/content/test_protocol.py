from __future__ import annotations

from pathlib import Path

from personal_lms.content import ContentRepository, SQLiteContentRepository


def test_sqlite_content_repository_satisfies_protocol(tmp_path: Path) -> None:
    store = SQLiteContentRepository.open(tmp_path / "content.sqlite3")
    try:
        assert isinstance(store, ContentRepository)
    finally:
        store.close()


def test_object_missing_search_does_not_satisfy_protocol() -> None:
    class _NotARepository:
        def initialize_schema(self) -> None: ...
        def upsert_document(self, document: object) -> None: ...
        def get_document(self, document_id: str) -> object | None: ...
        def list_documents(self, *, source_id: str | None = None) -> tuple[object, ...]: ...
        def upsert_chunk(self, chunk: object) -> None: ...
        def get_chunk(self, chunk_id: str) -> object | None: ...
        def list_chunks(self, *, filters: object | None = None) -> tuple[object, ...]: ...
        def close(self) -> None: ...

    assert not isinstance(_NotARepository(), ContentRepository)
