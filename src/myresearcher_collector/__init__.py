"""MyResearcher DataCollector source-isolated package."""

from .integration import PersistentCollection, execute_and_persist_collection

__all__ = ["PersistentCollection", "execute_and_persist_collection"]
