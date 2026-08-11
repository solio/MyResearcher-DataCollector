"""Independent offline acceptance matrix for the approved Xueqiu contract.

The production adapter was not present on the acceptance-preparation baseline.
The single loader below therefore reports ``PENDING_IMPLEMENTATION`` until a
Developer exposes a source-isolated Xueqiu collector. Once exposed, constructor
or result-shape mismatches fail these tests rather than being hidden by skips.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest


SOURCE = "xueqiu"
SYMBOL = "SH600519"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "xueqiu"
UTC = timezone.utc
CHECKPOINT = datetime.fromtimestamp(1700000000, tz=UTC)


@dataclass(frozen=True)
class FakeBrowserResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]
    final_url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


class FakeBrowserTransport:
    """A browser-network seam with deterministic responses and no real browser."""

    def __init__(self, responses: list[FakeBrowserResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.owner_thread = threading.get_ident()
        self.active = 0
        self.max_active = 0
        self.context_created = False

    def get(self, url: str, *, timeout: float | None = None, **kwargs: Any) -> FakeBrowserResponse:
        assert threading.get_ident() == self.owner_thread, "browser requests must be sequential"
        assert self.responses, "fake browser response script was exhausted"
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            parsed = urlparse(url)
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            self.calls.append({"url": url, "path": parsed.path, "query": query, "timeout": timeout, "kwargs": kwargs})
            return self.responses.pop(0)
        finally:
            self.active -= 1


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def utcnow(self) -> datetime:
        return datetime(2026, 8, 11, tzinfo=UTC)


def fixture_response(name: str, status: int = 200) -> FakeBrowserResponse:
    body = (FIXTURES / name).read_bytes()
    return FakeBrowserResponse(
        status_code=status,
        body=body,
        headers={"content-type": "application/json"},
        final_url="https://xueqiu.com/query/v1/symbol/search/status.json",
    )


def _load_factory() -> Any:
    candidates = (
        "myresearcher_collector.sources.xueqiu.collector",
        "myresearcher_collector.sources.xueqiu",
    )
    module = None
    for name in candidates:
        try:
            module = importlib.import_module(name)
            break
        except ModuleNotFoundError as exc:
            package = name.rsplit(".", 1)[0]
            if exc.name not in {name, package} and not exc.name.startswith(package + "."):
                raise
    if module is None:
        pytest.skip("PENDING_IMPLEMENTATION: Xueqiu collector module is not exposed")
    for name in ("XueqiuCollector", "Collector", "create_collector"):
        factory = getattr(module, name, None)
        if factory is not None:
            return factory
    pytest.skip("PENDING_IMPLEMENTATION: expose XueqiuCollector or create_collector")


@pytest.fixture
def xueqiu_factory() -> Any:
    return _load_factory()


def _construct(factory: Any, transport: FakeBrowserTransport, clock: ManualClock) -> Any:
    signature = inspect.signature(factory)
    params = signature.parameters
    kwargs: dict[str, Any] = {}
    for name in ("transport", "browser_transport", "browser"):
        if name in params:
            kwargs[name] = transport
            break
    for name, value in (
        ("clock", clock.utcnow),
        ("sleep_fn", clock.sleep),
        ("monotonic_fn", clock.monotonic),
    ):
        if name in params:
            kwargs[name] = value
    if "min_interval_seconds" in params:
        kwargs["min_interval_seconds"] = 3.0
    return factory(**kwargs)


def _collect(
    factory: Any,
    transport: FakeBrowserTransport,
    clock: ManualClock,
    *,
    checkpoint: datetime | None = None,
    max_pages: int = 2,
    existing_ids: set[str] | None = None,
    existing_observations: dict[str, Any] | None = None,
) -> Any:
    collector = _construct(factory, transport, clock)
    method = getattr(collector, "collect", None)
    if method is None:
        method = getattr(collector, "run", None)
    if method is None:
        raise AssertionError("Xueqiu collector must expose collect() or run()")
    params = inspect.signature(method).parameters
    positional: list[Any] = []
    kwargs: dict[str, Any] = {}
    if "symbol" in params:
        kwargs["symbol"] = SYMBOL
    elif "stock_code" in params:
        kwargs["stock_code"] = "600519"
    elif "stock_symbol" in params:
        kwargs["stock_symbol"] = SYMBOL
    else:
        positional.append(SYMBOL)
    for name in ("checkpoint", "watermark", "committed_checkpoint"):
        if name in params:
            kwargs[name] = checkpoint
            break
    if "max_pages" in params:
        kwargs["max_pages"] = max_pages
    for name in ("existing_ids", "known_ids"):
        if name in params and existing_ids is not None:
            kwargs[name] = existing_ids
            break
    for name in ("existing_observations", "observations"):
        if name in params and existing_observations is not None:
            kwargs[name] = existing_observations
            break
    return method(*positional, **kwargs)


def _status(result: Any) -> str:
    value = getattr(result, "status", result)
    return str(getattr(value, "value", value))


def _items(result: Any) -> list[Any]:
    return list(getattr(result, "items", ()))


def _stop_reason(result: Any) -> str | None:
    return getattr(result, "stop_reason", None)


def _frontier(result: Any) -> Any:
    for name in ("safe_frontier", "checkpoint", "watermark"):
        value = getattr(result, name, None)
        if value is not None:
            return value
    return None


def _item_id(item: Any) -> str:
    return str(getattr(item, "source_item_id", getattr(item, "id", "")))


def _item_published(item: Any) -> Any:
    return getattr(item, "published_at", getattr(item, "created_at", None))


def test_xq001_browser_transport_is_injectable_offline(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    result = _collect(xueqiu_factory, transport, ManualClock())
    assert _status(result) == "SUCCESS"
    assert transport.calls


def test_xq002_offline_execution_does_not_construct_real_browser(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    result = _collect(xueqiu_factory, transport, ManualClock())
    assert _status(result) == "SUCCESS"
    assert transport.context_created is False


def test_xq003_page_one_uses_approved_json_route_and_page_one(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    _collect(xueqiu_factory, transport, ManualClock())
    first = transport.calls[0]
    assert first["path"] == "/query/v1/symbol/search/status.json"
    assert first["query"].get("page") == "1"
    assert first["query"].get("symbol") == SYMBOL


def test_xq004_page_and_last_id_continuity_is_preserved(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    _collect(xueqiu_factory, transport, ManualClock())
    assert len(transport.calls) >= 2
    second = transport.calls[1]["query"]
    assert second.get("page") == "2"
    assert second.get("last_id"), "page 2 must carry browser-produced last_id"


def test_xq005_pagination_is_sequential_and_respects_three_second_interval(xueqiu_factory: Any) -> None:
    clock = ManualClock()
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    _collect(xueqiu_factory, transport, clock)
    assert transport.max_active == 1
    assert clock.sleeps
    assert all(delay >= 3.0 for delay in clock.sleeps)


def test_xq006_two_page_bootstrap_commits_newest_created_at(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    result = _collect(xueqiu_factory, transport, ManualClock(), max_pages=2)
    assert _status(result) == "SUCCESS"
    assert _stop_reason(result) == "bootstrap_complete"
    published = [_item_published(item) for item in _items(result)]
    assert published
    assert _frontier(result) == max(published)


def test_xq007_bootstrap_page_one_failure_keeps_checkpoint_null(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("challenge.json", 503)] * 3)
    result = _collect(xueqiu_factory, transport, ManualClock(), max_pages=2)
    assert _status(result) not in {"SUCCESS", "NO_NEW_DATA"}
    assert _frontier(result) is None


def test_xq008_bootstrap_page_two_failure_keeps_checkpoint_null(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport(
        [fixture_response("page_1.json")] + [fixture_response("challenge.json", 503)] * 3
    )
    result = _collect(xueqiu_factory, transport, ManualClock(), max_pages=2)
    assert _status(result) not in {"SUCCESS", "NO_NEW_DATA"}
    assert _frontier(result) is None


def test_xq009_invalid_required_item_is_schema_failure_not_success(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("invalid_item.json"), fixture_response("page_2.json")])
    result = _collect(xueqiu_factory, transport, ManualClock(), max_pages=2)
    assert _status(result) not in {"SUCCESS", "NO_NEW_DATA"}
    assert _frontier(result) is None


def test_xq010_access_challenge_is_not_no_new_data(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("challenge.json", 403)] * 2)
    result = _collect(xueqiu_factory, transport, ManualClock(), max_pages=2)
    assert _status(result) not in {"SUCCESS", "NO_NEW_DATA"}


def test_xq011_prior_observations_without_checkpoint_restart_page_one(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=None,
        existing_ids={"910001"},
    )
    assert transport.calls[0]["query"].get("page") == "1"
    assert _stop_reason(result) == "bootstrap_complete"


def test_xq012_incremental_known_boundary_stops_without_detail_or_extra_page(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json")])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=CHECKPOINT,
        existing_ids={"910001", "910002"},
        max_pages=3,
    )
    assert _status(result) == "NO_NEW_DATA"
    assert len(transport.calls) == 1


def test_xq013_unknown_old_id_is_eligible_at_or_before_checkpoint(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_2.json")])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=datetime.fromtimestamp(1800000000, tz=UTC),
        existing_ids=set(),
        max_pages=1,
    )
    assert "909901" in {_item_id(item) for item in _items(result)}


def test_xq014_known_historical_item_needs_no_forced_refetch(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json")])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=datetime.fromtimestamp(1800000000, tz=UTC),
        existing_ids={"910001", "910002"},
        max_pages=1,
    )
    assert _items(result) == []


def test_xq015_authorized_drift_is_versioned_not_silently_merged(xueqiu_factory: Any) -> None:
    first_transport = FakeBrowserTransport([fixture_response("page_1.json")])
    first = _collect(
        xueqiu_factory,
        first_transport,
        ManualClock(),
        checkpoint=datetime.fromtimestamp(1600000000, tz=UTC),
        existing_ids=set(),
        max_pages=1,
    )
    prior = next(item for item in _items(first) if _item_id(item) == "910001")
    changed = json.loads((FIXTURES / "page_1.json").read_text())
    changed["list"][0]["description"] = "synthetic authorized drift"
    first = FakeBrowserResponse(200, json.dumps(changed).encode(), {"content-type": "application/json"}, "https://xueqiu.com/query/v1/symbol/search/status.json")
    transport = FakeBrowserTransport([first])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=datetime.fromtimestamp(1600000000, tz=UTC),
        existing_ids={"910001"},
        existing_observations={"910001": prior},
        max_pages=1,
    )
    items = _items(result)
    assert items
    changed_item = next(item for item in items if _item_id(item) == "910001")
    assert getattr(changed_item, "content", None) == "synthetic authorized drift"
    assert getattr(changed_item, "observation_version", 0) >= 2


def test_xq016_coverage_cap_before_boundary_is_partial_without_frontier(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json")])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=CHECKPOINT,
        existing_ids=set(),
        max_pages=1,
    )
    assert _status(result) == "PARTIAL_COLLECTION"
    assert _frontier(result) is None


def test_xq017_no_new_data_requires_existing_checkpoint_and_known_boundary(xueqiu_factory: Any) -> None:
    fresh_transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_2.json")])
    fresh = _collect(xueqiu_factory, fresh_transport, ManualClock(), checkpoint=None)
    assert _status(fresh) != "NO_NEW_DATA"
    boundary_transport = FakeBrowserTransport([fixture_response("page_1.json")])
    boundary = _collect(
        xueqiu_factory,
        boundary_transport,
        ManualClock(),
        checkpoint=CHECKPOINT,
        existing_ids={"910001", "910002"},
    )
    assert _status(boundary) == "NO_NEW_DATA"


def test_xq018_nonadvancing_or_repeated_pagination_is_failure_not_no_data(xueqiu_factory: Any) -> None:
    transport = FakeBrowserTransport([fixture_response("page_1.json"), fixture_response("page_repeat.json")])
    result = _collect(
        xueqiu_factory,
        transport,
        ManualClock(),
        checkpoint=None,
        existing_ids={"910001", "910002"},
        max_pages=2,
    )
    assert _status(result) not in {"SUCCESS", "NO_NEW_DATA"}


def test_xq019_raw_evidence_lineage_has_source_bytes_url_and_sha(xueqiu_factory: Any) -> None:
    page_one = fixture_response("page_1.json")
    page_two = fixture_response("page_2.json")
    transport = FakeBrowserTransport([page_one, page_two])
    result = _collect(xueqiu_factory, transport, ManualClock())
    assert len(transport.calls) >= 2
    expected_shas = {
        hashlib.sha256(page_one.body).hexdigest(),
        hashlib.sha256(page_two.body).hexdigest(),
    }
    refs = [getattr(item, "raw_ref", None) for item in _items(result)]
    assert all(refs)
    serialized_refs = json.dumps(refs, default=str)
    assert any(sha in serialized_refs for sha in expected_shas)
    assert all(call["path"] == "/query/v1/symbol/search/status.json" for call in transport.calls)


def test_xq020_eastmoney_regression_suite_remains_in_scope() -> None:
    repo = Path(__file__).parents[2]
    required = (
        repo / "tests/unit/test_eastmoney_guba_collector.py",
        repo / "tests/acceptance/test_persistence_integration_acceptance.py",
    )
    assert all(path.is_file() for path in required)
