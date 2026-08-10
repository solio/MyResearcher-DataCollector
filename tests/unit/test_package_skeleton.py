"""Phase 0 structural smoke test; this is not a source behavior test."""

import importlib


def test_approved_package_skeleton_is_importable() -> None:
    modules = (
        "myresearcher_collector",
        "myresearcher_collector.core",
        "myresearcher_collector.sources",
        "myresearcher_collector.models",
        "myresearcher_collector.storage",
        "myresearcher_collector.cli",
    )
    for module in modules:
        assert importlib.import_module(module) is not None
