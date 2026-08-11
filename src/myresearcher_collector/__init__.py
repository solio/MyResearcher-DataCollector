"""MyResearcher DataCollector source-isolated package."""

from .backfill import BackfillConfigError, BackfillRange, resolve_backfill_range
from .integration import (
    PersistentBackfillCollection,
    PersistentCollection,
    execute_and_persist_backfill_collection,
    execute_and_persist_collection,
)
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
    "PersistentCollection", "PersistentBackfillCollection",
    "execute_and_persist_collection", "execute_and_persist_backfill_collection",
    "BackfillConfigError", "BackfillRange", "resolve_backfill_range",
    "BatchConfigError", "BatchRunner", "BatchSummary", "BatchTargets",
    "SingleStockOutcome", "execute_batch_collection", "load_targets",
    "make_batch_plan", "validate_targets",
]
