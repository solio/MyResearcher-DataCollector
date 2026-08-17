from __future__ import annotations

from pathlib import Path

from myresearcher_collector.sources.eastmoney_guba.browser_runtime import (
    ManagedChromiumTransport,
)


class _Dialog:
    type = "alert"
    message = "synthetic dialog"

    def __init__(self) -> None:
        self.dismiss_count = 0

    def dismiss(self) -> None:
        self.dismiss_count += 1


def test_managed_runtime_defaults_to_record_and_auto_dismiss(tmp_path: Path) -> None:
    runtime = ManagedChromiumTransport(profile_dir=tmp_path / "eastmoney")
    dialog = _Dialog()

    runtime._on_dialog(dialog)

    assert runtime.dialogs == [{"type": "alert", "message": "synthetic dialog"}]
    assert dialog.dismiss_count == 1


def test_managed_runtime_can_record_without_dismissing(tmp_path: Path) -> None:
    runtime = ManagedChromiumTransport(
        profile_dir=tmp_path / "xueqiu",
        record_dialogs=True,
        auto_dismiss_dialogs=False,
    )
    dialog = _Dialog()

    runtime._on_dialog(dialog)

    assert runtime.dialogs == [{"type": "alert", "message": "synthetic dialog"}]
    assert dialog.dismiss_count == 0


def test_managed_runtime_can_disable_dialog_recording(tmp_path: Path) -> None:
    runtime = ManagedChromiumTransport(
        profile_dir=tmp_path / "quiet",
        record_dialogs=False,
        auto_dismiss_dialogs=True,
    )
    dialog = _Dialog()

    runtime._on_dialog(dialog)

    assert runtime.dialogs == []
    assert dialog.dismiss_count == 1
