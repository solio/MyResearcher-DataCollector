from __future__ import annotations

import json
from pathlib import Path

import pytest

from myresearcher_collector.sources.xueqiu.dedicated_chrome import (
    XueqiuDedicatedChromePage,
)
from myresearcher_collector.sources.xueqiu.dom_scripts import (
    ACTIVE_PAGE_JS,
    CLICK_PAGE_JS,
    DETAIL_STATE_JS,
    PAGE_STATE_JS,
    READ_PAGE_JS,
)
from myresearcher_collector.sources.xueqiu.dom_transport import (
    XueqiuDomTransport,
    XueqiuDomTransportError,
    create_xueqiu_dom_transport,
)


def _item(status_id: str) -> dict[str, object]:
    return {
        "status_id": status_id,
        "author_id": "author",
        "author_name": "author",
        "url": f"https://xueqiu.com/author/{status_id}",
        "content": f"content-{status_id}",
        "title": None,
        "time_text_observed": "08/17 10:00",
        "read_count": None,
        "reply_count": 1,
        "like_count": 2,
        "forward_count": 3,
    }


class _Process:
    pid = 4242

    def __init__(self) -> None:
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = 0

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout: int) -> int:
        assert timeout > 0
        return int(self.returncode or 0)


class _Frame:
    def __init__(self, target: dict[str, object]) -> None:
        self.target = target

    @property
    def url(self) -> str:
        return str(self.target["url"])


class _Page:
    def __init__(self, target: dict[str, object]) -> None:
        self.target = target
        self.main_frame = _Frame(target)
        self.handlers: dict[str, object] = {}

    @property
    def url(self) -> str:
        return str(self.target["url"])

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout > 0
        self.target["url"] = url
        handler = self.handlers.get("framenavigated")
        if handler is not None:
            handler(self.main_frame)

    def evaluate(self, expression: str):
        page_no = int(self.target.get("page_no", 1))
        if expression == PAGE_STATE_JS:
            return json.dumps({
                "url": self.url,
                "title": "隆基绿能",
                "readyState": "complete",
                "posts": 2,
                "challenge": [],
            })
        if expression == ACTIVE_PAGE_JS:
            return str(page_no)
        if expression == READ_PAGE_JS:
            prefix = "p1" if page_no == 1 else "p2"
            return json.dumps([_item(f"{prefix}-a"), _item(f"{prefix}-b")])
        if "const wanted = String(2)" in expression:
            self.target["page_no"] = 2
            return json.dumps({"clicked": True})
        if expression == DETAIL_STATE_JS:
            return json.dumps({
                "url": self.url,
                "title": "detail",
                "readyState": "complete",
                "challenge": [],
                "status": {"id": "modified", "created_at": 1786607804000},
                "embeddedStatusJson": None,
            })
        raise AssertionError(f"unexpected JavaScript: {expression[:60]}")


class _BrowserState:
    def __init__(self) -> None:
        self.targets: dict[str, dict[str, object]] = {}
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.next_target = 1


class _Session:
    def __init__(self, state: _BrowserState) -> None:
        self.state = state
        self.detached = 0

    def send(self, method: str, params: dict[str, object]):
        self.state.commands.append((method, dict(params)))
        if method == "Target.createTarget":
            target_id = f"target-{self.state.next_target}"
            self.state.next_target += 1
            self.state.targets[target_id] = {
                "url": str(params["url"]),
                "page_no": 1,
            }
            return {"targetId": target_id}
        if method == "Target.closeTarget":
            self.state.targets.pop(str(params["targetId"]), None)
            return {"success": True}
        raise AssertionError(method)

    def detach(self) -> None:
        self.detached += 1


class _Context:
    def __init__(self, state: _BrowserState) -> None:
        self.pages = [_Page(target) for target in state.targets.values()]


class _Browser:
    def __init__(self, state: _BrowserState) -> None:
        self.state = state
        self.contexts = [_Context(state)]

    def new_browser_cdp_session(self) -> _Session:
        return _Session(self.state)


class _Chromium:
    """Only connect_over_cdp exists: launch APIs are deliberately absent."""

    def __init__(self, state: _BrowserState, endpoints: list[str]) -> None:
        self.state = state
        self.endpoints = endpoints

    def connect_over_cdp(self, endpoint: str, *, timeout: int) -> _Browser:
        assert timeout > 0
        self.endpoints.append(endpoint)
        return _Browser(self.state)


class _Playwright:
    def __init__(self, state: _BrowserState, endpoints: list[str]) -> None:
        self.chromium = _Chromium(state, endpoints)
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


def test_dedicated_chrome_runs_list_pagination_and_background_detail(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "Google Chrome"
    executable.touch()
    state = _BrowserState()
    endpoints: list[str] = []
    process = _Process()
    launches: list[tuple[list[str], dict[str, object]]] = []

    def process_factory(args, **kwargs):
        launches.append((list(args), dict(kwargs)))
        return process

    focus_identity = {
        "frontmost_application": "Terminal",
        "frontmost_pid": "99",
        "chrome_window_id": "user-window",
        "chrome_active_tab_id": "user-tab",
        "chrome_active_tab_index": "3",
        "chrome_active_url_hash": "url-hash",
        "chrome_active_title_hash": "title-hash",
    }
    page = XueqiuDedicatedChromePage(
        profile_dir=tmp_path / "profile",
        cdp_port=29227,
        chrome_executable=executable,
        process_factory=process_factory,
        playwright_starter=lambda: _Playwright(state, endpoints),
        cdp_probe=lambda _port, _process: {
            "Browser": "Chrome/151.0",
            "Protocol-Version": "1.3",
            "User-Agent": "Chrome/151.0",
        },
        port_free_checker=lambda _port: None,
        port_open_checker=lambda _port: False,
        focus_observer=lambda action: {"action": action, **focus_identity},
        sleep_fn=lambda _seconds: None,
    )
    transport = XueqiuDomTransport(page, timeout_ms=500)

    transport.open_stock("601012")
    first = transport.read_current_page()
    second = transport.goto_page(2, previous_ids=first["active_ids"])
    detail = transport.read_detail_created_at(
        "https://xueqiu.com/author/modified"
    )
    preserved = transport.assert_current_page(2, expected_ids=second["active_ids"])
    transport.close()

    assert transport.acquisition_mode == "dedicated-chrome-cdp"
    assert first["active_ids"] == ("p1-a", "p1-b")
    assert second["active_ids"] == ("p2-a", "p2-b")
    assert detail == {"id": "modified", "created_at": 1786607804000}
    assert preserved["page_no"] == 2
    assert launches and "--no-startup-window" in launches[0][0]
    assert "--remote-debugging-port=29227" in launches[0][0]
    assert f"--user-data-dir={(tmp_path / 'profile').resolve()}" in launches[0][0]
    assert all(endpoint == "http://127.0.0.1:29227" for endpoint in endpoints)
    creates = [params for method, params in state.commands if method == "Target.createTarget"]
    assert len(creates) == 2
    assert all(params["background"] is True for params in creates)
    assert any(method == "Target.closeTarget" for method, _ in state.commands)
    assert process.terminated == 1
    assert process.killed == 0
    assert page.runtime_report["playwright_launch_used"] is False
    assert page.runtime_report["apple_events_browser_control_used"] is False
    assert page.runtime_report["focus"]["final_user_chrome_matches_baseline"] is True


def test_dedicated_factory_is_lazy_and_validates_fixed_port(tmp_path: Path) -> None:
    transport = create_xueqiu_dom_transport(
        "dedicated-chrome-cdp",
        profile_dir=str(tmp_path / "profile"),
        cdp_port=29228,
        chrome_executable=str(tmp_path / "missing-until-live"),
    )
    assert isinstance(transport.page, XueqiuDedicatedChromePage)
    assert transport.page.process is None
    assert transport.page.cdp_port == 29228

    with pytest.raises(ValueError, match="CDP port"):
        create_xueqiu_dom_transport("dedicated-chrome-cdp", cdp_port=0)


def test_visible_verification_and_repeated_challenge_navigation_fail_closed(
    tmp_path: Path,
) -> None:
    class ChallengePage:
        def evaluate(self, expression: str):
            assert expression == PAGE_STATE_JS
            return json.dumps({
                "url": "https://xueqiu.com/S/SH601012",
                "posts": 0,
                "challenge": ["访问验证"],
            })

    page = XueqiuDedicatedChromePage(
        profile_dir=tmp_path / "profile",
        cdp_port=29229,
        observe_focus=False,
    )
    page._main_page = ChallengePage()
    with pytest.raises(XueqiuDomTransportError, match="verification"):
        page._page_state()

    page.navigation_urls = [
        "https://xueqiu.com/S/SH601012?md5__1038=<redacted>"
        for _ in range(3)
    ]
    with pytest.raises(XueqiuDomTransportError, match="did not settle"):
        page._page_state()


def test_dedicated_profile_lock_rejects_a_second_runtime(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    first = XueqiuDedicatedChromePage(
        profile_dir=profile, cdp_port=29230, observe_focus=False
    )
    second = XueqiuDedicatedChromePage(
        profile_dir=profile, cdp_port=29230, observe_focus=False
    )
    first._acquire_profile_lock()
    try:
        with pytest.raises(XueqiuDomTransportError, match="already owned"):
            second._acquire_profile_lock()
    finally:
        first._release_profile_lock()
