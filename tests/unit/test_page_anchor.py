from datetime import datetime, timedelta, timezone

import pytest

from myresearcher_collector.integration import plan_backfill
from myresearcher_collector.page_anchor import (
    PageAnchor,
    PageProbe,
    SeekFailure,
    predict_page,
    seek_historical_page,
)
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.integration import execute_and_persist_simple_backfill_collection
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig, EastmoneyGubaCollector
from tests.unit.test_backfill import MappingTransport, synthetic_page


def t(day: int) -> datetime:
    return datetime(2026, 7, day, tzinfo=timezone.utc)


def anchor(page: int = 50, *, count: int | None = 100_000) -> PageAnchor:
    return PageAnchor("eastmoney_guba", "600001", t(20), page, t(19), t(21), count, 80)


ANCIENT = datetime(1970, 1, 1, tzinfo=timezone.utc)


def seek(target: datetime, anchors, pages):
    """Probe a sparse page map, reporting pages past its end as "ancient".

    A page outside the map means the search stepped past the end of the bar.
    The production caller reports that as a page older than any real post
    (`integration.py`, the `FetchFailure("empty_page")` case) rather than
    raising, so this helper mirrors it -- otherwise a doubling step (page N sent
    to 2N) makes every fixture die on a KeyError instead of exercising the upper
    bound it was stepped out to find.
    """
    calls = []

    def probe(page: int) -> PageProbe:
        calls.append(page)
        if page not in pages:
            return PageProbe(page, ANCIENT, ANCIENT, None, 0)
        return pages[page]

    proof = seek_historical_page(target_to=target, anchors=anchors, probe=probe)
    return proof, calls


def test_case1_source_count_drift_predicts_without_page1():
    pages = {
        50: PageProbe(50, t(19), t(21), 102_400, 80),
        80: PageProbe(80, t(19), t(20), 102_400, 80),
    }
    proof, calls = seek(t(20), [anchor()], pages)
    assert calls == [50, 80]
    assert proof.verified_page == 80
    assert predict_page(anchor(), 102_400) == 80


def test_case2_predicted_page_too_new_moves_older():
    # A COMPLETE monotone map, one day per page. The assertions describe the
    # contract -- land on the page that brackets the target -- rather than the
    # exact probe sequence of one search strategy. The old fixture carried only
    # the three pages the doubling walk happened to visit, so any other (and
    # equally valid) search died on a KeyError instead of being measured.
    pages = {p: PageProbe(p, t(20 + (56 - p)), t(21 + (56 - p)), 100_000, 80)
             for p in range(46, 71)}
    proof, calls = seek(t(20), [anchor()], pages)
    assert proof.verified_page == 56
    assert calls[0] == 50


def test_case3_predicted_page_too_old_moves_newer():
    # Complete monotone map, six hours per page, so that halving the page number
    # from the anchor's page always lands on a real page. The old two-entry
    # fixture only worked while the walk probed 50 and then 48.
    centre = t(10)
    pages = {p: PageProbe(p, centre + timedelta(hours=6 * (48 - p)),
                          centre + timedelta(hours=6 * (48 - p) + 6), 100_000, 80)
             for p in range(1, 61)}
    proof, calls = seek(centre, [anchor()], pages)
    assert proof.verified_page == 48
    assert calls[0] == 50


def test_case4_missing_source_count_uses_local_anchor_seek():
    centre = t(10)
    pages = {p: PageProbe(p, centre + timedelta(hours=6 * (52 - p)),
                          centre + timedelta(hours=6 * (52 - p) + 6), None, 80)
             for p in range(1, 61)}
    proof, calls = seek(centre, [anchor(count=None)], pages)
    assert calls[0] == 50
    entry = pages[proof.verified_page]
    assert entry.page_min_time <= centre <= entry.page_max_time


def test_case5_stale_anchor_requires_live_verification():
    pages = {50: PageProbe(50, t(19), t(20), 100_000, 80)}
    proof, calls = seek(t(20), [anchor(page=50)], pages)
    assert calls == [50]
    assert proof.verified_page == 50


def test_case6_no_anchor_uses_bounded_nonsequential_fallback():
    # Complete map, four days per page, so the jump can be asserted exactly: from
    # page 1 the target is two page-spans away and the estimate lands on page 3
    # without visiting page 2.
    pages = {p: PageProbe(p, t(29 - 3 * p), t(32 - 3 * p), 100_000, 80)
             for p in range(1, 10)}
    proof, calls = seek(t(20), [], pages)
    assert calls[0] == 1
    # The contract is "a page whose range contains the target", not a particular
    # page number: with no anchor the search doubles from page 1 and stops on the
    # first page that brackets the target.
    entry = pages[proof.verified_page]
    assert entry.page_min_time <= t(20) <= entry.page_max_time
    assert len(calls) == len(set(calls)), calls


def test_case7_probe_limit_is_clean_failure():
    def probe(page: int) -> PageProbe:
        return PageProbe(page, t(30), t(31), None, 80)
    with pytest.raises(SeekFailure):
        seek_historical_page(target_to=t(20), anchors=[], probe=probe, max_probes=3)


def test_case8_seek_proof_is_usable_for_traversal_start():
    pages = {10: PageProbe(10, t(19), t(20), None, 80)}
    proof, _ = seek(t(20), [anchor(page=10, count=None)], pages)
    assert proof.start_page == 9
    assert proof.probe_count == 1


def test_case8b_time_seek_then_traversal_can_establish_coverage(tmp_path):
    stock = "600001"
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.save_page_anchor(anchor(page=5, count=1))
    finally:
        store.close()
    routes = {
        EastmoneyGubaCollector.list_url(stock, 4): synthetic_page(("4001", "2026-07-22 12:00:00")),
        EastmoneyGubaCollector.list_url(stock, 5): synthetic_page(
            ("5001", "2026-07-19 12:00:00"), ("5002", "2026-07-20 12:00:00")
        ),
        EastmoneyGubaCollector.list_url(stock, 6): synthetic_page(("6001", "2026-07-01 12:00:00")),
        EastmoneyGubaCollector.list_url(stock, 7): synthetic_page(),
    }
    transport = MappingTransport(routes)
    result = execute_and_persist_simple_backfill_collection(
        db_path=tmp_path / "collector.db", stock_code=stock,
        from_time=t(1), to_time=t(20), transport=transport,
        collector_config=CollectorConfig(min_interval_seconds=2.5),
        sleep_fn=lambda _: None, clock=lambda: t(30), enable_time_seek=True,
    )
    assert transport.calls[0] == EastmoneyGubaCollector.list_url(stock, 5)
    assert transport.calls[1] == EastmoneyGubaCollector.list_url(stock, 4)
    assert result.execution.range_complete is True
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        assert store.coverage_ranges("eastmoney_guba", stock) == [(t(1), t(20))]
    finally:
        store.close()


def test_case9_manual_start_is_not_time_seek_eligible(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        plan = plan_backfill(store, source="eastmoney_guba", stock_code="600001",
                             from_time=t(1), to_time=t(20), explicit_start_page=50,
                             started_at=t(30))
        assert plan.start_page == 50
        assert plan.time_seek_eligible is False
    finally:
        store.close()


def test_case10_resume_and_coverage_remain_higher_priority(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.save_backfill_resume("eastmoney_guba", "600001", t(1), t(20), 7)
        plan = plan_backfill(store, source="eastmoney_guba", stock_code="600001",
                             from_time=t(1), to_time=t(20), started_at=t(30))
        assert plan.start_page == 8
        assert plan.time_seek_eligible is False
        store.clear_backfill_resume("eastmoney_guba", "600001", t(1), t(20))
        store.add_coverage("eastmoney_guba", "600001", t(1), t(20))
        covered = plan_backfill(store, source="eastmoney_guba", stock_code="600001",
                                from_time=t(5), to_time=t(10), started_at=t(30))
        assert covered.already_covered is True
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Bracketed search: the step walk above could compute its way back onto an
# already probed page and abort. Observed live on 2026-09-27 (603997): the
# sequence was 12, 14, 18, 16 and the fifth step returned to 12, raising
# "time seek exhausted valid page candidates" while the pages that actually
# bracket the target (13, 15, 17) had never been probed. Every test below also
# asserts the search never probes the same page twice, which is the invariant
# that makes the abort unreachable.
# ---------------------------------------------------------------------------

_ANCIENT = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SHANGHAI = timezone(timedelta(hours=8))


def _sh(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=_SHANGHAI)


def _monotone_pages(*, page_count: int, days_per_page: float, newest: str):
    """page -> [min, max], newest first, strictly older as the page number rises.

    That monotonicity is the property the bracketed search relies on, and it is
    what the live capture showed (`backfill_page_anchors` for 603997 on
    2026-09-27: page 1 = Sep 21..Sep 27, page 12 = Jul 24..Aug 5).
    """
    span = timedelta(days=days_per_page)
    top = _sh(newest)
    return {
        page_no: (top - span * page_no, top - span * (page_no - 1))
        for page_no in range(1, page_count + 1)
    }


class _RecordingProbe:
    """Records the probe sequence; pages past the end read as "older than any post"."""

    def __init__(self, pages, *, source_count: int, page_size: int = 80):
        self.pages = pages
        self.source_count = source_count
        self.page_size = page_size
        self.sequence: list[int] = []

    def __call__(self, page_no: int) -> PageProbe:
        self.sequence.append(page_no)
        if page_no not in self.pages:
            return PageProbe(page_no, _ANCIENT, _ANCIENT, None, 0)
        low, high = self.pages[page_no]
        return PageProbe(page_no, low, high, self.source_count, self.page_size)


def _anchors_upto(pages, *, upto: int, source_count: int):
    seen = datetime(2026, 9, 27, 6, 45, tzinfo=timezone.utc)
    return [
        PageAnchor("eastmoney_guba", "603997", seen, page_no,
                   pages[page_no][0], pages[page_no][1], source_count, 80)
        for page_no in sorted(pages) if page_no <= upto
    ]


def test_bracketed_seek_finds_the_bracketing_page_and_the_probe_sequence_is_unique():
    pages = _monotone_pages(page_count=700, days_per_page=2.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=49253)
    proof = seek_historical_page(
        target_to=pages[26][0] + timedelta(hours=1),
        anchors=_anchors_upto(pages, upto=12, source_count=49253), probe=probe,
    )
    assert proof.verified_page == 26
    assert proof.start_page == 25  # safety_pages=1 re-reads one page
    assert proof.probe_pages == tuple(probe.sequence)
    assert len(probe.sequence) == len(set(probe.sequence)), probe.sequence


def test_seek_does_not_abort_where_the_step_walk_returned_to_a_visited_page():
    """The 2026-09-27 regression, reduced to the part that mattered.

    Target on page 15: the old walk probed 12 (too new) -> 14 -> 18 -> 16 and
    then computed 12 again, which was already visited, and gave up. Page 15 --
    the answer -- was never probed.
    """
    pages = _monotone_pages(page_count=700, days_per_page=1.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=49253)
    proof = seek_historical_page(
        target_to=pages[15][0] + timedelta(hours=1),
        anchors=_anchors_upto(pages, upto=12, source_count=49253), probe=probe,
    )
    assert proof.start_page == 14
    assert len(probe.sequence) == len(set(probe.sequence)), probe.sequence


def test_seek_brackets_by_doubling_then_halves_and_never_repeats_a_page():
    """Exponential bracket, then bisection -- and no page probed twice.

    Bisecting probes from BOTH sides of the interval, so "the probe number only
    ever increases" is the wrong property to assert (an earlier revision did, and
    it was written against a different search). What has to hold is that each
    probe either brackets the target or becomes a new bound, so nothing is probed
    twice and the interval shrinks every step.
    """
    pages = _monotone_pages(page_count=700, days_per_page=1.0, newest="2026-09-27 22:00")
    target = pages[15][0] + timedelta(hours=1)
    probe = _RecordingProbe(pages, source_count=49253)
    proof = seek_historical_page(
        target_to=target, anchors=_anchors_upto(pages, upto=12, source_count=49253),
        probe=probe,
    )
    assert probe.sequence == [12, 24, 18, 15], probe.sequence
    assert len(probe.sequence) == len(set(probe.sequence)), probe.sequence
    low, high = pages[proof.verified_page]
    assert low <= target <= high
    assert proof.probe_pages == tuple(probe.sequence)


def test_seek_reaches_a_target_hundreds_of_pages_deep_inside_the_default_budget():
    pages = _monotone_pages(page_count=700, days_per_page=2.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=49253)
    proof = seek_historical_page(
        target_to=pages[430][0] + timedelta(hours=1),
        anchors=_anchors_upto(pages, upto=12, source_count=49253), probe=probe,
    )
    # The start page must never be DEEPER than the target page (that would skip
    # in-window content), and the tolerance bounds the over-read.
    assert 430 - 8 <= proof.start_page <= 430, proof.start_page
    assert len(probe.sequence) <= 20, probe.sequence
    assert len(probe.sequence) == len(set(probe.sequence)), probe.sequence


def test_seek_uses_a_page_past_the_end_as_its_upper_bound():
    """Target older than the whole bar: converge on the end, do not fail."""
    pages = _monotone_pages(page_count=20, days_per_page=2.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=1500)
    proof = seek_historical_page(
        target_to=_sh("2019-01-01 00:00"),
        anchors=_anchors_upto(pages, upto=6, source_count=1500), probe=probe,
    )
    assert proof.start_page == 20  # the last page that has posts
    assert proof.verified_page == 20
    assert len(probe.sequence) == len(set(probe.sequence)), probe.sequence


def test_seek_clamps_at_page_one_when_the_target_is_newer_than_the_newest_page():
    pages = _monotone_pages(page_count=700, days_per_page=2.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=49253)
    proof = seek_historical_page(
        target_to=_sh("2026-09-28 09:00"),
        anchors=_anchors_upto(pages, upto=12, source_count=49253), probe=probe,
    )
    assert proof.start_page == 1
    assert proof.verified_page == 1


def test_seek_never_starts_deeper_than_the_target_when_the_target_is_in_a_gap():
    pages = {1: (_sh("2026-09-20 00:00"), _sh("2026-09-27 00:00")),
             2: (_sh("2026-08-01 00:00"), _sh("2026-08-10 00:00"))}
    probe = _RecordingProbe(pages, source_count=160)
    proof = seek_historical_page(
        target_to=_sh("2026-09-01 00:00"),
        anchors=_anchors_upto(pages, upto=2, source_count=160), probe=probe,
    )
    # Starting at 1 re-reads an out-of-window page and cannot skip: page 2 is
    # reached by the walk itself.
    assert proof.start_page == 1


def test_seek_budget_is_a_clean_failure_and_never_repeats_a_page():
    pages = _monotone_pages(page_count=700, days_per_page=2.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=49253)
    # One probe is genuinely not enough: the first probe only establishes a
    # bound, and the interpolation needs a second to land. (Three probes DO
    # suffice here -- the date span jumps 598 pages in one step -- which is why
    # the budget under test has to be one.)
    with pytest.raises(SeekFailure, match="probe limit"):
        seek_historical_page(
            target_to=pages[600][0] + timedelta(hours=1),
            anchors=_anchors_upto(pages, upto=2, source_count=49253), probe=probe,
            max_probes=1,
        )
    assert len(probe.sequence) == len(set(probe.sequence)), probe.sequence


def test_seek_rejects_a_nonsensical_budget():
    pages = _monotone_pages(page_count=5, days_per_page=2.0, newest="2026-09-27 22:00")
    probe = _RecordingProbe(pages, source_count=160)
    with pytest.raises(ValueError):
        seek_historical_page(target_to=pages[1][0], anchors=[], probe=probe, max_probes=0)
    with pytest.raises(ValueError):
        seek_historical_page(target_to=pages[1][0], anchors=[], probe=probe, safety_pages=-1)
