"""Independent XQ-021/XQ-022 supplement acceptance.

XQ-021 crosses the public Xueqiu persistence boundary. XQ-022 tests the
production ``XueqiuBrowserTransport`` against a minimal Playwright-like fake;
no real browser or network is started.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import pytest

from myresearcher_collector.integration import execute_and_persist_xueqiu_collection
from myresearcher_collector.sources.xueqiu import (
    CollectorConfig,
    XueqiuBrowserTransport,
    XueqiuResponse,
)
from myresearcher_collector.sources.xueqiu.browser_transport import (
    BrowserTransportError,
    DISCUSSION_PATH,
)
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "xueqiu"
UTC = timezone.utc
NOW = datetime(2026, 8, 11, tzinfo=UTC)
T0 = datetime.fromtimestamp(1_700_000_000, tz=UTC)
T1 = datetime.fromtimestamp(1_800_000_000, tz=UTC)
SECRET = "DO_NOT_PERSIST"


def fixture_response(name: str, *, final_url: str | None = None) -> XueqiuResponse:
    body = (FIXTURES / name).read_bytes()
    return XueqiuResponse(
        200,
        body,
        {"content-type": "application/json"},
        final_url or "https://xueqiu.com/query/v1/symbol/search/status.json",
    )


def json_response(payload: dict[str, object], *, final_url: str) -> XueqiuResponse:
    return XueqiuResponse(
        200,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        {"content-type": "application/json"},
        final_url,
    )


class GetTransport:
    """Deterministic fallback transport for the real integration boundary."""

    def __init__(self, responses: list[XueqiuResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> XueqiuResponse:
        del timeout
        assert self.responses, "response script exhausted"
        self.calls.append(url)
        return self.responses.pop(0)


def run_persisted(
    tmp_path: Path,
    run_id: str,
    transport: GetTransport,
    *,
    max_pages: int,
):
    return execute_and_persist_xueqiu_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "raw",
        stock_code="600519",
        transport=transport,
        run_id=run_id,
        collector_config=CollectorConfig(
            max_pages=max_pages,
            min_interval_seconds=3.0,
            base_backoff_seconds=0,
        ),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=max_pages,
    )


def seed_bootstrap(tmp_path: Path) -> tuple[str, str]:
    execution = run_persisted(
        tmp_path,
        "xq-supplement-seed",
        GetTransport([fixture_response("page_1.json"), fixture_response("page_2.json")]),
        max_pages=2,
    )
    assert execution.result.status.value == "SUCCESS"
    return (
        "https://xueqiu.com/query/v1/symbol/search/status.json",
        "xq-supplement-seed",
    )


def new_page_payload() -> dict[str, object]:
    return {
        "list": [
            {
                "id": 920001,
                "description": "new accepted item",
                "title": "new title",
                "created_at": 1_800_000_000_000,
                "target": "https://xueqiu.com/920001",
                "user": {"id": 820001, "screen_name": "new-author"},
                "fav_count": 1,
                "reply_count": 1,
                "retweet_count": 1,
            },
            {
                "id": 910001,
                "description": "<p>synthetic body one 😀</p>",
                "title": "synthetic title one",
                "created_at": 1_700_000_000_000,
                "target": "https://xueqiu.com/910001",
                "user": {"id": 810001, "screen_name": "synthetic-author-1"},
                "fav_count": 1,
                "reply_count": 2,
                "retweet_count": 3,
            },
        ],
        "count": 2,
        "maxPage": 2,
        "page": 1,
    }


def known_page_payload() -> dict[str, object]:
    payload = json.loads((FIXTURES / "page_2.json").read_text())
    payload["page"] = 2
    return payload


def checkpoint(tmp_path: Path) -> tuple[str | None, str | None] | None:
    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "raw", source="xueqiu"),
    )
    try:
        return store.checkpoint("xueqiu", "stock:600519")
    finally:
        store.close()


def test_xq021_incremental_new_item_advances_real_persisted_checkpoint(tmp_path: Path) -> None:
    seed_bootstrap(tmp_path)
    transport = GetTransport([
        json_response(
            new_page_payload(),
            final_url="https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1",
        ),
        json_response(
            known_page_payload(),
            final_url="https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=2&last_id=910001",
        ),
    ])

    execution = run_persisted(tmp_path, "xq-021-incremental", transport, max_pages=3)

    assert execution.result.status.value == "SUCCESS"
    assert execution.result.safe_frontier == T1
    assert len(transport.calls) == 2
    second_query = parse_qs(urlparse(transport.calls[1]).query)
    assert second_query["page"] == ["2"]
    assert second_query["last_id"] == ["910001"]
    assert checkpoint(tmp_path) == (T1.isoformat(timespec="microseconds").replace("+00:00", "Z"), "xq-021-incremental")

    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "raw", source="xueqiu"),
    )
    try:
        assert store.conn.execute(
            "SELECT count(*) FROM source_item_observations WHERE source='xueqiu' AND source_item_id='920001'"
        ).fetchone()[0] == 1
        assert store.conn.execute(
            "SELECT watermark_before_utc, watermark_after_utc FROM collection_runs WHERE run_id='xq-021-incremental'"
        ).fetchone() == (
            T0.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            T1.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        )
    finally:
        store.close()


def test_xq021_negative_control_no_new_data_keeps_checkpoint(tmp_path: Path) -> None:
    seed_bootstrap(tmp_path)
    transport = GetTransport([fixture_response("page_1.json")])

    execution = run_persisted(tmp_path, "xq-021-no-new", transport, max_pages=3)

    assert execution.result.status.value == "NO_NEW_DATA"
    assert execution.result.safe_frontier == T0
    assert checkpoint(tmp_path) == (
        T0.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "xq-021-no-new",
    )


class FakeRequest:
    method = "GET"


class FakeBrowserResponse:
    def __init__(self, url: str, body: bytes = b"{}") -> None:
        self.url = url
        self.request = FakeRequest()
        self.status = 200
        self._body = body

    def body(self) -> bytes:
        return self._body

    def all_headers(self) -> dict[str, str]:
        return {"content-type": "application/json"}


class ExpectResponse:
    def __init__(self, response: FakeBrowserResponse, predicate: object) -> None:
        self.value = response
        self.predicate = predicate

    def __enter__(self) -> "ExpectResponse":
        assert callable(self.predicate)
        assert self.predicate(self.value)
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeLocator:
    count_value = 1

    def __init__(self, on_click: Callable[[], None] | None = None) -> None:
        self.on_click = on_click

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return self.count_value

    def click(self) -> None:
        if self.on_click is not None:
            self.on_click()


class FakePage:
    def __init__(self, responses: list[FakeBrowserResponse]) -> None:
        self.responses = list(responses)
        self.goto_urls: list[str] = []
        self.clicked = 0

    def expect_response(self, predicate: object, *, timeout: int) -> ExpectResponse:
        del timeout
        assert self.responses
        return ExpectResponse(self.responses.pop(0), predicate)

    def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.goto_urls.append(url)

    def get_by_role(self, _role: str, *, name: str, exact: bool) -> FakeLocator:
        assert exact and name == "2"
        return FakeLocator(lambda: setattr(self, "clicked", self.clicked + 1))

    def get_by_text(self, _text: str, *, exact: bool) -> FakeLocator:
        assert exact
        return FakeLocator(lambda: setattr(self, "clicked", self.clicked + 1))


def response_url(*, page: int, last_id: str | None = None, symbol: str = "SH600519", extra: str = "") -> str:
    query = f"symbol={symbol}&count=10&page={page}"
    if last_id is not None:
        query += f"&last_id={last_id}"
    if extra:
        query += "&" + extra
    return f"https://xueqiu.com{DISCUSSION_PATH}?{query}"


def test_xq022a_browser_transport_accepts_page_one_response() -> None:
    page = FakePage([FakeBrowserResponse(response_url(page=1), b"page-1")])
    response = XueqiuBrowserTransport(page).fetch_page(
        "600519", page=1, last_id=None, timeout=1.0
    )
    assert response.status_code == 200
    assert response.body == b"page-1"
    assert page.goto_urls == ["https://xueqiu.com/S/SH600519"]


def test_xq022b_browser_transport_accepts_page_two_last_id_continuity() -> None:
    page = FakePage([FakeBrowserResponse(response_url(page=2, last_id="910002"), b"page-2")])
    response = XueqiuBrowserTransport(page).fetch_page(
        "600519", page=2, last_id="910002", timeout=1.0
    )
    assert response.body == b"page-2"
    assert page.clicked == 1


def test_xq022c_wrong_last_id_is_browser_pagination_failure() -> None:
    page = FakePage([FakeBrowserResponse(response_url(page=2, last_id="WRONG"))])
    with pytest.raises(BrowserTransportError):
        XueqiuBrowserTransport(page).fetch_page(
            "600519", page=2, last_id="910002", timeout=1.0
        )


@pytest.mark.parametrize(
    "url, requested_page, requested_last_id",
    [
        (response_url(page=1), 2, "910002"),
        (response_url(page=2, last_id="910002", symbol="SZ000001"), 2, "910002"),
    ],
)
def test_xq022d_wrong_page_or_symbol_is_browser_pagination_failure(
    url: str, requested_page: int, requested_last_id: str
) -> None:
    page = FakePage([FakeBrowserResponse(url)])
    with pytest.raises(BrowserTransportError):
        XueqiuBrowserTransport(page).fetch_page(
            "600519",
            page=requested_page,
            last_id=requested_last_id,
            timeout=1.0,
        )


def test_xq022e_browser_owned_secret_is_redacted_from_persisted_provenance(tmp_path: Path) -> None:
    unsafe = response_url(page=1, extra=f"challenge_secret={SECRET}")
    page = FakePage([FakeBrowserResponse(unsafe, b"page-1")])
    response = XueqiuBrowserTransport(page).fetch_page(
        "600519", page=1, last_id=None, timeout=1.0
    )
    assert SECRET not in (response.final_url or "")
    assert "symbol=SH600519" in (response.final_url or "")

    transport = GetTransport([
        fixture_response("page_1.json", final_url=unsafe),
        fixture_response("page_2.json", final_url=unsafe),
    ])
    run_persisted(tmp_path, "xq-022-secret", transport, max_pages=2)
    db_bytes = (tmp_path / "collector.db").read_bytes()
    assert SECRET.encode() not in db_bytes
    for path in (tmp_path / "raw").rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()
