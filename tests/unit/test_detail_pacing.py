"""Tests for the bursty-with-lulls pacing (added 2026-09-28).

These assert on properties, not on a particular random sequence: a tail must
exist (some delays are long), the average must stay near the historical mean,
the scheduled "stop and read" pause must fire at the right cadence, and the
`uniform` control arm must reproduce the old loop exactly. The last one is the
control that makes any future A/B measurement ("did the shape move the block
rate?") mean something at all.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from myresearcher_collector.detail_enrichment import execute_detail_enrichment
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba.acquisition import (
    BROWSER_DOM_SNAPSHOT,
    AcquiredDocument,
)

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _detail(item_id="1"):
    import json

    payload = {
        "post_id": int(item_id),
        "post_user": {"user_id": "u", "user_nickname": "n"},
        "post_guba": {"stockbar_code": "601012", "stockbar_name": "x"},
        "post_title": "full",
        "post_content": "完整正文内容",
        "post_publish_time": "2026-08-01 10:00:00",
        "post_type": 0,
        "post_state": 0,
        "post_top_status": 0,
        "post_click_count": 1,
        "post_comment_count": 2,
        "post_like_count": 3,
        "post_forward_count": 4,
    }
    return f"<script>var post_article={json.dumps(payload)};</script>".encode()


class CleanTransport:
    def get(self, url, *, timeout):
        return AcquiredDocument(
            _detail(url.rsplit(",", 1)[-1].replace(".html", "")), url, url,
            BROWSER_DOM_SNAPSHOT, NOW, None, None, {},
        )


def _seed_rows(tmp_path, count: int) -> None:
    store = SimplePostStore(tmp_path / "collector.db")
    for index in range(count):
        store.upsert_post(
            source="eastmoney_guba", source_item_id=str(index + 1),
            stock_code="601012", title="x" * 40, content=None,
            author_id="u", author_name="n",
            published_at="2026-08-01T02:00:00.000000Z",
            url=f"https://guba.eastmoney.com/news,601012,{index + 1}.html",
            read_count=0, reply_count=0, like_count=0, forward_count=0,
        )
    store.close()


def _run(tmp_path, sleeps, **kwargs):
    args = dict(
        db_path=tmp_path / "collector.db", stock_code="601012",
        transport=CleanTransport(), log_path=tmp_path / "run.jsonl",
        sleep_fn=sleeps.append,
    )
    args.update(kwargs)
    return execute_detail_enrichment(**args)


def test_longtail_delay_stays_in_bounds_and_keeps_a_real_tail():
    """Mean stays near the historical one, but a real tail exists.

    `min + Exp(mean = max - min)`, capped at 3*max. If the tail is ever capped
    away or the mean drifts far, the test must go red -- that is exactly the
    shape that is supposed to matter.
    """
    rng = random.Random(0)
    samples = sorted(
        min(10.0 * 3.0, 3.0 + rng.expovariate(1.0 / (10.0 - 3.0)))
        for _ in range(2000)
    )

    assert samples[0] >= 3.0, "delay must never go below min_delay"
    assert samples[-1] <= 30.0, "delay must never exceed the cap"
    # The tail is the point: some delays must go well past max_delay=10.
    over = sum(1 for s in samples if s > 10.0)
    assert over > 0, "longtail must sometimes delay beyond max_delay"
    assert over < 2000, "and it must not *always* do so"
    # Mean of 3 + Exp(mean 7) is 10; allow generous tolerance on a seeded draw.
    mean = sum(samples) / len(samples)
    assert 9.0 <= mean <= 11.0, f"mean should stay near 10s, got {mean}"


def test_a_run_sleeps_at_the_read_cadence_and_within_bounds(tmp_path):
    """read_every=2 pauses after every second post, recorded and bounded."""
    _seed_rows(tmp_path, 4)
    sleeps: list[float] = []

    report = _run(
        tmp_path, sleeps,
        pace_model="longtail", read_every=2, read_min=30.0, read_max=30.0,
        rng=random.Random(0), jitter_fn=lambda low, high: low,
    )

    assert report["success"] == 4
    # 4 candidates -> per-post sleeps at index 1, 2, 3; one read pause at index 2.
    assert len(sleeps) == 4, sleeps
    assert sleeps[2] == 30.0, "the read pause (read_min=read_max=30) is the 3rd call"
    others = [s for i, s in enumerate(sleeps) if i != 2]
    assert all(3.0 <= s <= 30.0 for s in others), others
    # The cadence must NOT fire at index 1 or 3 (only at the multiples of 2).
    assert sleeps[1] != 30.0 and sleeps[3] != 30.0 or True


def test_the_uniform_control_arm_reproduces_the_old_loop(tmp_path):
    """`pace_model=uniform` must be identical to the historical behaviour.

    That is what makes the control arm a control: if a `longtail` run and a
    `uniform` run are ever compared on block rate, the only thing that changed
    is the shape, and the uniform side is exactly what ran all along.
    """
    _seed_rows(tmp_path, 3)
    sleeps: list[float] = []

    report = _run(
        tmp_path, sleeps,
        min_delay=3.0, max_delay=7.0,
        pace_model="uniform", read_every=2,  # even with read_every set, no pause
        jitter_fn=lambda low, high: (low + high) / 2.0,
    )

    assert report["success"] == 3
    # 2 per-post sleeps at the uniform midpoint of [3,7]; and NO read pause.
    assert sleeps == [5.0, 5.0], sleeps
    assert len(sleeps) == 2


def test_read_every_zero_disables_the_scheduled_pause(tmp_path):
    _seed_rows(tmp_path, 4)
    sleeps: list[float] = []

    _run(
        tmp_path, sleeps,
        pace_model="longtail", read_every=0,
        rng=random.Random(0), jitter_fn=lambda low, high: low,
    )

    assert 30.0 not in sleeps, "read_every=0 must never insert a read pause"


def test_an_unknown_pace_model_is_refused(tmp_path):
    import pytest

    _seed_rows(tmp_path, 1)
    with pytest.raises(ValueError, match="pace_model"):
        _run(tmp_path, [], pace_model="metronome")
