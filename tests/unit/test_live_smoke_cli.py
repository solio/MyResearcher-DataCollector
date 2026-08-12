"""Offline-only checks for the future live smoke command and run report."""

from __future__ import annotations

import json
from pathlib import Path

from myresearcher_collector.cli.main import (
    build_parser,
    execute_live_smoke,
    execute_persistent_run,
    main,
    persistent_run_plan,
)
from myresearcher_collector.sources.eastmoney_guba import HttpResponse


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"


class FixtureTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        assert timeout == 20.0
        self.calls.append(url)
        fixture = "list_page_1.html" if "/list," in url else "detail_1001.html"
        return HttpResponse(
            200,
            (FIXTURES / fixture).read_bytes(),
            {"content-type": "text/html"},
            final_url=url,
        )


def live_args(data_dir: Path, mode: str) -> list[str]:
    return [
        "eastmoney-guba-live-smoke",
        "601012",
        "--data-dir",
        str(data_dir),
        "--max-pages",
        "1",
        "--min-interval",
        "3.0",
        mode,
    ]


def persistent_args(data_dir: Path, mode: str) -> list[str]:
    return [
        "eastmoney-guba-persistent",
        "601012",
        "--data-dir",
        str(data_dir),
        "--max-pages",
        "3",
        "--min-interval",
        "3.0",
        mode,
    ]


def test_plan_only_is_explicitly_offline_and_does_not_create_data_dir(
    tmp_path: Path, capsys
) -> None:
    data_dir = tmp_path / "planned-live-smoke"

    assert main(live_args(data_dir, "--plan-only")) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan == {
        "mode": "PLAN_ONLY",
        "network_execution": False,
        "source": "eastmoney_guba",
        "source_access": "BROWSER_HOST_EXPERIMENTAL_AVAILABILITY_BLOCKED",
        "unattended_production_ready": False,
        "stock_code": "601012",
        "max_pages": 1,
        "timeout_seconds": 20.0,
        "min_interval_seconds": 3.0,
        "data_dir": str(data_dir),
        "data_dir_state": "absent",
        "data_dir_ready": True,
        "sqlite_location": str(data_dir / "collector.db"),
        "raw_evidence_location": str(data_dir / "raw" / "eastmoney_guba"),
        "secrets_required": "NONE",
    }
    assert not data_dir.exists()


def test_injected_fixture_transport_exercises_real_persistence_and_summary(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "offline-live-shape"
    args = build_parser().parse_args(live_args(data_dir, "--confirm-live"))
    transport = FixtureTransport()

    summary = execute_live_smoke(
        args,
        transport=transport,
        sleep_fn=lambda _: None,
        run_id="offline-smoke-shape",
    )

    assert len(transport.calls) == 2
    assert summary == {
        "run_id": "offline-smoke-shape",
        "source": "eastmoney_guba",
        "stock_code": "601012",
        "scope_key": "stock:601012",
        "status": "PARTIAL_COLLECTION",
        "requests_total": 2,
        "requests_success": 2,
        "requests_failed": 0,
        "pages_requested": 1,
        "pages_success": 1,
        "pages_failed": 0,
        "records_received": 2,
        "records_accepted": 1,
        "records_failed": 0,
        "raw_evidence_location": str(data_dir / "raw" / "eastmoney_guba"),
        "raw_evidence_count": 2,
        "raw_evidence_file_count": 2,
        "sqlite_location": str(data_dir / "collector.db"),
        "checkpoint_before": None,
        "checkpoint_after": None,
        "checkpoint_updated": False,
        "safe_frontier": None,
        "first_published_at": "2026-08-10T02:00:00.000000Z",
        "last_published_at": "2026-08-10T02:00:00.000000Z",
        "attempts_persisted": 2,
        "failures_persisted": 0,
    }
    assert (data_dir / "collector.db").is_file()
    assert len(list((data_dir / "raw" / "eastmoney_guba").glob("*.body"))) == 2


def test_persistent_runner_bootstraps_then_uses_incremental_checkpoint(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "persistent"
    args = build_parser().parse_args(
        persistent_args(data_dir, "--confirm-live")
    )

    assert persistent_run_plan(
        build_parser().parse_args(persistent_args(data_dir, "--plan-only"))
    )["collection_mode"] == "BOOTSTRAP_PENDING"
    first_transport = FixtureTransport()
    first = execute_persistent_run(
        args,
        transport=first_transport,
        sleep_fn=lambda _: None,
        run_id="persistent-bootstrap",
    )

    assert first["status"] == "SUCCESS"
    assert first["checkpoint_before"] is None
    assert first["checkpoint_after"] == "2026-08-10T02:00:00.000000Z"
    assert len(first_transport.calls) == 6
    plan = persistent_run_plan(
        build_parser().parse_args(persistent_args(data_dir, "--plan-only"))
    )
    assert plan["collection_mode"] == "INCREMENTAL"
    assert plan["checkpoint"] == first["checkpoint_after"]

    second_transport = FixtureTransport()
    second = execute_persistent_run(
        args,
        transport=second_transport,
        sleep_fn=lambda _: None,
        run_id="persistent-incremental",
    )

    assert second["status"] == "NO_NEW_DATA"
    assert second["checkpoint_before"] == first["checkpoint_after"]
    assert second["checkpoint_after"] == first["checkpoint_after"]
    assert len(second_transport.calls) == 2
    assert all("/list," in url for url in second_transport.calls)


def test_inspect_run_reads_the_same_persisted_authority(
    tmp_path: Path, capsys
) -> None:
    data_dir = tmp_path / "inspectable"
    args = build_parser().parse_args(live_args(data_dir, "--confirm-live"))
    expected = execute_live_smoke(
        args,
        transport=FixtureTransport(),
        sleep_fn=lambda _: None,
        run_id="inspect-me",
    )

    assert main(["inspect-run", "--data-dir", str(data_dir), "--run-id", "inspect-me"]) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_live_smoke_rejects_nonempty_data_dir_before_transport_use(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "not-isolated"
    data_dir.mkdir()
    (data_dir / "existing.txt").write_text("belongs to someone else", encoding="utf-8")
    args = build_parser().parse_args(live_args(data_dir, "--confirm-live"))
    transport = FixtureTransport()

    try:
        execute_live_smoke(args, transport=transport, sleep_fn=lambda _: None)
    except ValueError as exc:
        assert str(exc) == "live smoke data_dir must be a new or empty directory"
    else:
        raise AssertionError("nonempty live smoke data directory was accepted")
    assert transport.calls == []


def test_plan_rejects_nonfinite_or_too_fast_request_interval(
    tmp_path: Path, capsys
) -> None:
    base = live_args(tmp_path / "never-created", "--plan-only")
    interval_index = base.index("3.0")
    for invalid in ("2.9", "nan", "inf"):
        argv = list(base)
        argv[interval_index] = invalid
        assert main(argv) == 2
        assert "min_interval must be at least 3.0 seconds" in capsys.readouterr().err
    assert not (tmp_path / "never-created").exists()
