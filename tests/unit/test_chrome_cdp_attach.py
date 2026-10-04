"""`chrome-cdp`: attach to a Chrome the operator launched (2026-09-28).

Measured on this machine, the only difference page JavaScript can read between a
run of ours and a browser the operator starts himself is `navigator.webdriver`
-- True for anything Playwright launches, False for a Chrome started from a
shell; userAgent, plugins.length, hardwareConcurrency, languages and
window.chrome all measured identical.

Getting a False was closed by configuration (`ignore_default_args=
["--enable-automation"]` does not clear it) and by launching Chrome here (this
environment refuses Chrome's sandbox: `sandbox initialization failed: Operation
not permitted`, which is why every launch path in this repo carries
`--no-sandbox`). Hence: the operator launches, we attach.

The property these tests protect is the one that would hurt a user: **`close()`
must not close his browser.** The parent class closes the context, which for an
attached browser is the operator's own browser.
"""

from __future__ import annotations

import pytest

from myresearcher_collector.sources.eastmoney_guba.browser_runtime import (
    ChromeCdpAttachTransport,
    create_eastmoney_transport,
)


class _SpyContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _SpyPlaywright:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_the_endpoint_is_configurable_and_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("MYRESEARCHER_CDP_ENDPOINT", raising=False)
    assert ChromeCdpAttachTransport().endpoint == "http://127.0.0.1:9222"

    monkeypatch.setenv("MYRESEARCHER_CDP_ENDPOINT", "http://127.0.0.1:9333")
    assert ChromeCdpAttachTransport().endpoint == "http://127.0.0.1:9333"
    # An explicit argument wins over the environment.
    assert ChromeCdpAttachTransport(endpoint="http://127.0.0.1:9444").endpoint.endswith("9444")


def test_it_identifies_itself_as_attached_not_as_a_launcher():
    transport = ChromeCdpAttachTransport()

    assert transport.acquisition_mode == "chrome-cdp"
    assert transport.profile_mode == "attached"
    # A string on purpose: nothing here may create or own a profile, so an
    # accidental `.mkdir()` on this attribute must fail loudly rather than
    # silently make a directory.
    assert isinstance(transport.profile_dir, str)
    assert isinstance(transport.dialogs, list)


def test_close_disconnects_without_closing_the_operators_browser():
    """THE safety property. Closing the context would close his Chrome."""
    transport = ChromeCdpAttachTransport()
    context = _SpyContext()
    playwright = _SpyPlaywright()
    transport.context = context
    transport._playwright = playwright
    transport.page = object()
    transport.delegate = object()

    transport.close()

    assert context.closed is False, "close() must NOT close the attached context"
    assert playwright.stopped is True, "but it must drop the Playwright connection"
    assert transport.context is None and transport.delegate is None


def test_close_is_safe_before_anything_was_attached():
    ChromeCdpAttachTransport().close()  # must not raise


def test_the_factory_builds_the_attached_transport():
    transport = create_eastmoney_transport("chrome-cdp", detail_referer_from_list=True)

    assert isinstance(transport, ChromeCdpAttachTransport)
    assert transport.detail_referer_from_list is True


def test_list_click_paging_is_allowed_here_because_the_delegate_supports_it():
    """Unlike the AppleScript modes, this shares `EastmoneyBrowserTransport`.

    The guard exists because the other modes can only set a tab URL, so accepting
    the flag would silently do nothing. This mode drives the same delegate as
    `managed-chromium`, so the flag is real here.
    """
    transport = create_eastmoney_transport("chrome-cdp", list_click_paging=True)

    assert transport.list_click_paging is True

    with pytest.raises(ValueError):
        create_eastmoney_transport("chrome-clean", list_click_paging=True)


def test_loopback_is_exempted_from_the_http_proxy(monkeypatch):
    """REGRESSION 2026-10-02: the environment sets HTTP_PROXY and no NO_PROXY.

    Playwright's driver honours them, so `connect_over_cdp` asked the proxy for
    `http://127.0.0.1:9222/json/version` and got `Unexpected status 502 ... This
    does not look like a DevTools server` -- which reads as a broken browser.
    Loopback must never be proxied.
    """
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "example.com")

    ChromeCdpAttachTransport._exempt_loopback_from_proxy()

    import os

    # An existing exemption must survive where it was set...
    assert "example.com" in os.environ["NO_PROXY"].split(",")
    # ...and every host must be exempt in BOTH spellings, since either can be the
    # one a given client reads.
    for key in ("NO_PROXY", "no_proxy"):
        hosts = os.environ[key].split(",")
        for host in ("127.0.0.1", "localhost", "::1"):
            assert host in hosts, f"{host} missing from {key}"


def test_exempting_loopback_twice_does_not_duplicate_entries(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    ChromeCdpAttachTransport._exempt_loopback_from_proxy()
    ChromeCdpAttachTransport._exempt_loopback_from_proxy()

    import os

    assert os.environ["NO_PROXY"].count("127.0.0.1") == 1
