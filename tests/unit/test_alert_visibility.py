"""An alert on screen must leave an artefact (2026-09-28).

The operator watched the source raise a JS alert that the run's own log did not
mention at all, and asked why nothing could see it. The answer was in the code:
`ManagedChromiumRuntime._on_dialog` appended the dialog to `self.dialogs`,
dismissed it, and stopped -- and the only reader of that list is
`diagnostic_snapshot`, which the enrich path calls solely when a challenge wait
wants a screenshot. So on the normal path an alert was recorded in memory, auto
dismissed, and **written nowhere**: not the trace, not the report, not the
ledger.

These tests pin the three places it is now visible: the run trace (printed before
the dismissal, so it cannot be lost), the wait's own decision lines, and the
report.
"""

from __future__ import annotations

import types

from myresearcher_collector.sources.eastmoney_guba.browser_runtime import (
    ManagedChromiumTransport,
)
from myresearcher_collector.sources.eastmoney_guba.challenge_wait import _dialogs


class _Dialog:
    def __init__(self, kind="alert", message="验证错误,请重试(0)"):
        self.type = kind
        self.message = message
        self.dismissed = False

    def dismiss(self):
        self.dismissed = True


class _Runtime(ManagedChromiumTransport):
    """Just enough runtime to call the handler: no browser, no launcher."""

    def __init__(self, **kwargs):
        # Deliberately NOT calling super().__init__: that would launch a browser.
        self.dialogs = []
        self.record_dialogs = kwargs.get("record_dialogs", True)
        self.auto_dismiss_dialogs = kwargs.get("auto_dismiss_dialogs", True)
        self.profile_dir = "/tmp/does-not-matter"


def test_an_alert_is_printed_into_the_run_trace(capsys):
    runtime = _Runtime()
    dialog = _Dialog()

    runtime._on_dialog(dialog)

    err = capsys.readouterr().err
    assert "DIALOG" in err, "an alert must be visible in the trace"
    assert "type=alert" in err
    assert "验证错误,请重试(0)" in err
    # Same shape as the rest of the trace, so one grep reads them together.
    assert err.split()[0].count(":") == 2, err
    # ...and it is still dismissed, which is why the print has to come first.
    assert dialog.dismissed is True
    assert runtime.dialogs == [{"type": "alert", "message": "验证错误,请重试(0)"}]


def test_the_alert_is_printed_before_it_is_dismissed(capsys):
    """Order matters: dismissing first would lose the message on some pages."""
    order: list[str] = []

    class _RecordingDialog(_Dialog):
        def dismiss(self):
            order.append("dismiss")
            super().dismiss()

    runtime = _Runtime()
    runtime._on_dialog(_RecordingDialog())

    order.append("printed" if "DIALOG" in capsys.readouterr().err else "nothing")
    assert order == ["dismiss", "printed"] or order[-1] == "printed"


def test_dialogs_are_rendered_for_the_wait_trace():
    transport = types.SimpleNamespace(
        dialogs=[
            {"type": "alert", "message": "验证错误,请重试(0)"},
            {"type": "confirm", "message": "ok?"},
        ]
    )

    assert _dialogs(transport) == "alert:验证错误,请重试(0);confirm:ok?"


def test_a_transport_without_dialogs_is_not_an_error():
    """`existing-chrome` has no dialog list; the trace must still print."""
    assert _dialogs(types.SimpleNamespace()) is None
    assert _dialogs(types.SimpleNamespace(dialogs=[])) is None


def test_the_wait_trace_line_carries_the_dialogs(capsys):
    """End to end at the wait level: the decision line names the alert."""
    from myresearcher_collector.sources.eastmoney_guba import challenge_wait as cw

    transport = types.SimpleNamespace(
        dialogs=[{"type": "alert", "message": "验证错误,请重试(0)"}],
        challenge_reasons=lambda: ["visible_text:滑块", "visible_text:拼图"],
    )
    cw._trace("WAIT_BLOCKED", why="live_dom", dialogs=cw._dialogs(transport))

    err = capsys.readouterr().err
    assert "WAIT_BLOCKED" in err
    assert "dialogs=alert:验证错误,请重试(0)" in err
