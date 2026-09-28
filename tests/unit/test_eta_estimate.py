"""The dry-run ETA must state its assumptions and be arithmetic-checkable.

A predicted wall clock is only useful if you can see what it assumed. These pin
the model terms (so a silent change to the pacing maths is caught) and the failure
modes (a bad arg must not print a plausible-looking number).
"""

from __future__ import annotations

import pytest

from scripts.ops.eta_estimate import main, sleep_mean

DEFAULT_LEDGER = "runtime/logs/eastmoney-detail-enrichment.jsonl"


def _run(capsys, *args):
    code = main(list(args))
    return code, capsys.readouterr().out


def test_sleep_mean_matches_the_two_samplers():
    """uniform -> mean of the bounds; longtail -> min + Exp(mean=max-min) = max."""
    assert sleep_mean("uniform", 3.0, 5.0) == pytest.approx(4.0)
    assert sleep_mean("longtail", 3.0, 5.0) == pytest.approx(5.0)
    # A degenerate range must not go negative or divide by zero.
    assert sleep_mean("longtail", 5.0, 5.0) == pytest.approx(5.0)


def test_uniform_ignores_the_read_pause(capsys):
    _, out = _run(capsys, "--stock", "601012=100", "--pace-model", "uniform",
                  "--min-delay", "3", "--max-delay", "5", "--ledger", "/nonexistent")

    assert "read_pause=0.00s" in out


def test_longtail_amortises_the_read_pause(capsys):
    _, out = _run(capsys, "--stock", "601012=100", "--pace-model", "longtail",
                  "--min-delay", "3", "--max-delay", "5",
                  "--read-every", "20", "--read-min", "30", "--read-max", "90",
                  "--ledger", "/nonexistent")

    # (30+90)/2 / 20 = 3.00s per post
    assert "read_pause=3.00s" in out


def test_referer_mode_pays_for_the_second_navigation_and_the_dwell(capsys):
    _, out = _run(capsys, "--stock", "601012=100", "--pace-model", "uniform",
                  "--min-delay", "3", "--max-delay", "5", "--detail-referer", "1",
                  "--dwell-min", "0.4", "--dwell-max", "1.6", "--ledger", "/nonexistent")

    assert "navs=2" in out and "dwell=1.00s" in out


def test_workers_divide_the_wall_clock_and_are_flagged_as_an_idealisation(capsys):
    _, one = _run(capsys, "--stock", "601012=1000", "--pace-model", "uniform",
                  "--min-delay", "3", "--max-delay", "5", "--workers", "1",
                  "--ledger", "/nonexistent")
    _, four = _run(capsys, "--stock", "601012=1000", "--pace-model", "uniform",
                   "--min-delay", "3", "--max-delay", "5", "--workers", "4",
                   "--ledger", "/nonexistent")

    # With the ledger missing, request falls back to 0.40s, so per_post = 4.00+0.40
    # = 4.40s -> 13.6 posts/min, and four workers is exactly 4x that.
    assert "posts_per_min=13.6" in one
    assert "posts_per_min=54.5" in four
    assert "ETA_WARNING" in four             # ...but the assumption is stated
    assert "ETA_WARNING" not in one


def test_a_missing_ledger_falls_back_instead_of_pretending(capsys):
    _, out = _run(capsys, "--stock", "601012=100", "--ledger", "/nonexistent/ledger.jsonl")

    assert "calibrated_by=ledger-missing" in out
    assert "ETA_CROSSCHECK unavailable" in out


def test_bad_arguments_are_refused(capsys):
    assert main(["--stock", "not-a-code", "--ledger", "/nonexistent"]) == 2
    assert main(["--ledger", "/nonexistent"]) == 2
    assert main(["--stock", "601012=10", "--workers", "0", "--ledger", "/nonexistent"]) == 2
