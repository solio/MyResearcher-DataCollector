from __future__ import annotations

import pytest

import myresearcher_collector.sources.eastmoney_guba.browser_host as host_module

from myresearcher_collector.sources.eastmoney_guba.browser_host import (
    BrowserHostConfigError,
    _handle_request,
    _make_server,
    _prepare_socket_path,
    _send_payload,
    _PreflightCache,
    _captured_document_response,
)
from myresearcher_collector.sources.eastmoney_guba.browser_transport import (
    EastmoneyBrowserResponse,
)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float) -> EastmoneyBrowserResponse:
        self.calls.append((url, timeout))
        return EastmoneyBrowserResponse(
            status_code=200,
            body=b"<html></html>",
            headers={"content-type": "text/html"},
            final_url=url,
        )


class FakeCapturedResponse:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    status = 200

    def body(self):
        return b"<html></html>"

    def all_headers(self):
        return {"content-type": "text/html", "set-cookie": "host-only"}


def test_host_request_handler_preserves_main_document_shape() -> None:
    transport = FakeTransport()
    url = "https://guba.eastmoney.com/list,601012,f.html"

    payload = _handle_request(
        transport, {"method": "GET", "url": url, "timeout": 20.0}
    )

    assert transport.calls == [(url, 20.0)]
    assert payload["ok"] is True
    assert payload["status_code"] == 200
    assert payload["headers"] == {"content-type": "text/html"}
    assert payload["final_url"] == url


def test_preflight_cache_is_consumed_once_for_the_exact_list_url() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    cached = {"ok": True, "served_from_preflight_cache": True}
    cache = _PreflightCache(url, cached)

    assert cache.take({"method": "GET", "url": url}) is cached
    assert cache.take({"method": "GET", "url": url}) is None


def test_preflight_cache_does_not_consume_on_other_request() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    cached = {"ok": True}
    cache = _PreflightCache(url, cached)

    assert cache.take({"method": "GET", "url": url + "_2"}) is None
    assert cache.take({"method": "GET", "url": url}) is cached


def test_captured_operator_document_is_exact_and_sanitized() -> None:
    response = _captured_document_response(FakeCapturedResponse())
    assert response.status_code == 200
    assert response.body == b"<html></html>"
    assert response.headers == {"content-type": "text/html"}
    assert response.final_url == FakeCapturedResponse.url


def test_host_rejects_non_get_request() -> None:
    with pytest.raises(ValueError, match="only GET"):
        _handle_request(FakeTransport(), {"method": "POST", "url": "x", "timeout": 1})


def test_host_server_binds_owner_only_socket(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeServer:
        def bind(self, value):
            calls.append(("bind", value))

        def listen(self, value):
            calls.append(("listen", value))

        def settimeout(self, value):
            calls.append(("timeout", value))

        def close(self):
            calls.append(("close", None))

    fake = FakeServer()
    monkeypatch.setattr(host_module.socket, "socket", lambda *_args: fake)
    monkeypatch.setattr(host_module.os, "chmod", lambda path, mode: calls.append(("chmod", (path, mode))))

    assert _make_server(host_module.Path("/tmp/browser.sock")) is fake
    assert calls == [
        ("bind", "/tmp/browser.sock"),
        ("chmod", (host_module.Path("/tmp/browser.sock"), 0o600)),
        ("listen", 8),
        ("timeout", 0.5),
    ]


def test_host_does_not_unlink_non_socket_path(monkeypatch) -> None:
    class FakeStat:
        st_mode = 0o100600

    class FakePath:
        def expanduser(self):
            return self

        def resolve(self):
            return self

        def __str__(self):
            return "/tmp/browser.sock"

        def exists(self):
            return True

        def is_symlink(self):
            return False

        def lstat(self):
            return FakeStat()

        def unlink(self):
            raise AssertionError("must not unlink a non-socket path")

        @property
        def parent(self):
            return self

        def mkdir(self, **_kwargs):
            return None

    path = FakePath()
    monkeypatch.setattr(host_module, "Path", lambda _value: path)
    with pytest.raises(BrowserHostConfigError, match="not a Unix socket"):
        _prepare_socket_path(path)


def test_send_payload_treats_cancelled_client_as_request_end() -> None:
    class DisconnectedClient:
        def sendall(self, _value):
            raise BrokenPipeError

    assert _send_payload(DisconnectedClient(), {"ok": True}) is False
