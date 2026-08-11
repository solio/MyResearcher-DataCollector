"""MyResearcher DataCollector source-isolated package."""

from .integration import PersistentCollection, execute_and_persist_collection
from .batch import (
    BatchConfigError,
    BatchRunner,
    BatchSummary,
    BatchTargets,
    SingleStockOutcome,
    execute_batch_collection,
    load_targets,
    make_batch_plan,
    validate_targets,
)

__all__ = [
    "PersistentCollection", "execute_and_persist_collection",
    "BatchConfigError", "BatchRunner", "BatchSummary", "BatchTargets",
    "SingleStockOutcome", "execute_batch_collection", "load_targets",
    "make_batch_plan", "validate_targets",
]
