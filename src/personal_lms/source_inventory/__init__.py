"""Source Inventory: domain-neutral, persistence-neutral raw-archive inventory.

Records identity, location, provenance, lifecycle, privacy, approval
state, and version metadata for source material. Performs no content
extraction, no URL fetching, no Obsidian access, no FTS indexing, no
embeddings, and no LLM calls — inventory and governance infrastructure
only. See ``personal_lms.domain.source_inventory`` for the full
architectural note distinguishing this from the existing, unrelated
``personal_lms.domain.catalog``/``personal_lms.catalog`` contract.
"""

from personal_lms.source_inventory.errors import (
    SourceAlreadyExistsError,
    SourceInventoryContractError,
    SourceInventoryError,
    SourceInventoryStorageError,
    SourceLocationConflictError,
    SourceNotFoundError,
    SourceVersionAlreadyExistsError,
)
from personal_lms.source_inventory.protocol import SourceInventoryCatalog, SourceInventoryFilter
from personal_lms.source_inventory.sqlite import SQLiteSourceInventory

__all__ = [
    "SQLiteSourceInventory",
    "SourceAlreadyExistsError",
    "SourceInventoryCatalog",
    "SourceInventoryContractError",
    "SourceInventoryError",
    "SourceInventoryFilter",
    "SourceInventoryStorageError",
    "SourceLocationConflictError",
    "SourceNotFoundError",
    "SourceVersionAlreadyExistsError",
]
