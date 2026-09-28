"""Browser-owned transport for the approved Eastmoney HTML surfaces.

The caller owns the normal browser page/context.  This adapter navigates only
to approved public Eastmoney URLs and returns the exact main-document response
bytes to the existing Collector.  It never reads or exports browser cookies,
storage, challenge values or credentials.
"""

from __future__ import annotations

import math
import base64
import json
import re
import socket
import random
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .acquisition import HTTP_RESPONSE
from .challenge_dom import DOM_CHALLENGE_JS, challenge_reasons as dom_challenge_reasons
from .list_paging import (
    CLICK_NEXT_LIST_PAGE_JS,
    NAVIGATION_COUNTS,
    list_page_parts,
    list_page_url,
)


_ALLOWED_HOSTS = {"guba.eastmoney.com", "caifuhao.eastmoney.com"}
_MAX_SOCKET_RESPONSE_BYTES = 32 * 1024 * 1024

# A bar detail page: https://guba.eastmoney.com/news,<bar>,<post id>.html
_DETAIL_PATH_RE = re.compile(r"^/news,([^,]+),[0-9]+\.html$")

# The headers that record **how** a navigation was initiated. Read with
# `request.all_headers()`: `request.headers` does NOT expose `sec-fetch-*`
# (scripts/ops/README.md, "Reader trap"), and reading it would report
# "no Sec-Fetch-Site" for every page while looking like a measurement.
_NAVIGATION_SHAPE_HEADERS = (
    "referer", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-user",
)


def _request_shape(response: Any) -> dict[str, str]:
    """Sampled `Referer`/`Sec-Fetch-*` of a response's request; {} if unreadable."""
    try:
        headers = {
            str(key).lower(): str(value)
            for key, value in response.request.all_headers().items()
        }
    except Exception:  # noqa: BLE001
        return {}
    return {
        key: headers[key] for key in _NAVIGATION_SHAPE_HEADERS if key in headers
    }


class EastmoneyBrowserTransportError(OSError):
    """A normal browser navigation did not yield an approved response."""


class EastmoneyBrowserChallengeError(EastmoneyBrowserTransportError):
    """The page we were about to navigate *from* is behind verification.

    Raised by the referer scheme when the bar's list page comes back challenged.
    A reader who is shown a verification overlay on a list page does not then
    click through to a post -- jumping anyway is the exact signature the scheme
    exists to avoid, and it throws away the challenged page, which is the page an
    operator needs on screen to clear it. So the navigation is refused instead.

    It stays a ``EastmoneyBrowserTransportError`` so every existing handler keeps
    catching it, but the caller is expected to route it into the ACCESS-BLOCK
    path (ledger row, visible verification wait, fail closed) rather than the
    transport-failure path. It is detected by the presence of
    ``list_page_challenge_reasons`` rather than by class, so a caller that must
    stay source-agnostic can still recognise it. The reasons are already
    list-page-prefixed, so the ledger keeps recording *where* the challenge was
    seen.
    """

    def __init__(self, message: str, *, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.list_page_challenge_reasons = list(reasons or [])


class EastmoneyBrowserBoundaryError(RuntimeError):
    """A requested or final URL leaves the approved source boundary."""


def _page_url(page: Any) -> str | None:
    """The page's committed URL, or None. Never raises: diagnostics only."""
    try:
        value = getattr(page, "url", None)
    except Exception:  # noqa: BLE001
        return None
    return str(value) if value else None


def _validate_browser_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise EastmoneyBrowserBoundaryError("browser URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise EastmoneyBrowserBoundaryError(
            "browser navigation is outside approved Eastmoney HTTPS hosts"
        )


@dataclass(frozen=True)
class EastmoneyBrowserResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]
    final_url: str | None = None
    diagnostics: dict[str, object] | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def capture_method(self) -> str:
        return HTTP_RESPONSE


class EastmoneyBrowserTransport:
    """Use a caller-owned synchronous Playwright-like Page for HTML GETs.

    Two opt-in navigation schemes change the *shape* of a request without
    changing which URL is requested. Both default OFF and both are purely about
    how the browser is asked to navigate:

    ``detail_referer_from_list``
        reach a detail page through its bar's list page (enrich);
    ``list_click_paging``
        turn list pages by clicking the page's own pager anchor (backfill).
    """

    def __init__(
        self,
        page: Any,
        *,
        detail_referer_from_list: bool = False,
        list_click_paging: bool = False,
        detail_dwell_seconds: tuple[float, float] | None = None,
    ) -> None:
        self.page = page
        self.detail_referer_from_list = bool(detail_referer_from_list)
        self.list_click_paging = bool(list_click_paging)
        # How long to sit on the bar's list page before jumping to the detail.
        # Only meaningful with `detail_referer_from_list`: without that scheme
        # there is no list page to sit on. The pause exists so the two hops are
        # not back to back -- a reader who lands on a list looks at it before
        # clicking through, and the gap between the two requests is one of the
        # few timing signals a page can measure. It is randomised because a fixed
        # gap is itself a signature.
        self.detail_dwell_seconds = detail_dwell_seconds
        # Which list page the browser is currently showing, and for which bar.
        # Only set after a list page has actually been served, so the click path
        # is taken only when we really are adjacent to the page being asked for.
        self._list_page_position: tuple[str, int] | None = None
        # The instrument, not a decoration: a run has to be able to prove which
        # navigation scheme each page used, and which request headers came out of
        # it. Without these, "we paged by clicking" would be an unfalsifiable
        # claim -- the exact class of error that let a detector bug look like a
        # source block (see scripts/ops/README.md).
        self.list_navigation_counts: dict[str, int] = {
            mode: 0 for mode in NAVIGATION_COUNTS
        }
        self.list_navigation_samples: dict[str, dict[str, str]] = {}

    def _bar_list_url(self, url: str) -> str | None:
        """The bar list page that owns `url`, or None when this is not a detail URL.

        Backfill fetches List URLs, so it never takes this path; only the detail
        surface (enrich) does. caifuhao URLs have no bar list and return None.
        """
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if parsed.hostname != "guba.eastmoney.com":
            return None
        match = _DETAIL_PATH_RE.match(parsed.path)
        if match is None:
            return None
        return f"https://guba.eastmoney.com/list,{match.group(1)},f.html"

    def _build_response(
        self, response: Any, *, extra_diagnostics: dict[str, Any] | None = None
    ) -> EastmoneyBrowserResponse:
        if response is None:
            raise EastmoneyBrowserTransportError(
                "browser navigation returned no main-document response"
            )
        final_url = str(response.url)
        _validate_browser_url(final_url)
        body = bytes(response.body())
        headers = {
            str(key).lower(): str(value)
            for key, value in response.all_headers().items()
            if str(key).lower() != "set-cookie"
        }
        diagnostics: dict[str, Any] = {
            "actual_url": str(getattr(self.page, "url", final_url)),
            "page_title": str(self.page.title()) if callable(getattr(self.page, "title", None)) else None,
        }
        if extra_diagnostics:
            diagnostics.update(extra_diagnostics)
        return EastmoneyBrowserResponse(
            status_code=int(response.status),
            body=body,
            headers=headers,
            final_url=final_url,
            diagnostics=diagnostics,
        )

    def _trace(self, event: str, **fields: object) -> None:
        """One line per navigation-plane event, on stderr.

        WHY THIS EXISTS: the enrich path had no navigation record at all, so "the
        verification page flashed for a moment" could not be attributed -- whether
        this code re-navigated the browser or the page did it by itself was
        unanswerable. The collector carries the same helper for the backfill walk;
        this one covers what the collector never sees: detail fetches, and the
        referer scheme's two hops (list page, then the in-page JS jump) plus the
        dwell between them.

        Same line shape on purpose -- `HH:MM:SS.mmm EVENT k=v` -- so one grep reads
        both files. Never raises: an instrument that can break the thing it
        measures is worse than no instrument.
        """
        try:
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            detail = " ".join(f"{key}={value}" for key, value in fields.items())
            print(f"{stamp} {event} {detail}".rstrip(), file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            pass

    def _dwell_on_list_page(self) -> float:
        """Sit on the bar list page for a randomised moment before jumping.

        The referer scheme's two hops -- list page, then in-page JS jump to the
        detail -- are otherwise back to back, and the gap between them is one of
        the few timing signals the page can measure. The gap is RANDOMISED on
        purpose: a fixed one is its own signature. Splitting the inter-request
        sleep this way keeps the per-item wall clock the operator tuned while
        moving part of the pause to where a reader would actually pause.

        Never raises. A fake page in a test need not implement a wait, and a
        dwell that cannot be taken must not fail a run.
        """
        window = self.detail_dwell_seconds
        if not window:
            return 0.0
        low, high = window
        seconds = random.uniform(min(low, high), max(low, high))
        if seconds <= 0:
            return 0.0
        waiter = getattr(self.page, "wait_for_timeout", None)
        try:
            if callable(waiter):
                waiter(int(round(seconds * 1000)))
            else:
                time.sleep(seconds)
        except Exception:  # noqa: BLE001
            pass
        return seconds

    def _get_after_list_visit(
        self, url: str, list_url: str, *, timeout: float
    ) -> EastmoneyBrowserResponse:
        """Open the owning bar list page, then reach the detail by in-page JS.

        `page.goto(url, referer=...)` does put a `Referer` on the wire, but it
        arrives with no initiator, so `Sec-Fetch-Site` stays `none` where a real
        in-page navigation carries `same-origin`. Measured in
        `scripts/ops/referer_probe_selftest.py` phase 2 -- so the two are not
        equivalent, and this is the one that reproduces a human click.

        A scripted navigation has no return value, so the response is collected
        from the `response` event and reshaped identically to the direct path:
        callers keep seeing `response.body()` / `response.all_headers()` bytes.

        REDIRECTS -- the first revision could not see them (2026-09-23)
        --------------------------------------------------------------
        It matched `response.url == url` exactly. Every detail URL that redirects
        therefore captured nothing: a deleted post answers 302 into
        `error?type=2`, so no response ever carried our URL, the capture stayed
        empty, and the candidate surfaced as a transport error instead of the
        `detail_not_found` the direct `page.goto` path produces -- which means
        such posts never reach the skip ledger and are re-requested forever.
        The symptom in the 2026-09-23 run was small (1 candidate in 258) but the
        consequence is not, so the capture now mirrors `page.goto`: keep the
        main-document responses, and accept the one the browser actually ended
        on. `wait_for_url` likewise accepts any non-list page, because a
        redirected detail never commits our URL.

        Note: the redirect path is covered by a unit-test fixture
        (`FakeRefererPage(detail_final_url=...)`), NOT by a live deleted post --
        the source was blocking when this was written, so every URL answered the
        challenge shell and a live check would have proved nothing.
        """
        page = self.page
        milliseconds = max(1, int(timeout * 1000))
        captured: dict[str, Any] = {"documents": []}

        def _capture(response: Any) -> None:
            """Collect main-document responses; the last one wins."""
            try:
                request = getattr(response, "request", None)
                resource_type = getattr(request, "resource_type", None)
                if resource_type is not None and resource_type != "document":
                    return
                captured["documents"].append(response)
            except Exception:  # noqa: BLE001
                pass

        def _list_page(page_url: str) -> bool:
            return page_url.split("#")[0] == list_url

        def _landed(page_url: str) -> bool:
            # Either our detail, or wherever a redirect took us -- anything but
            # staying on the list page. `_validate_browser_url` still vets the
            # host when the response is built.
            candidate = page_url.split("#")[0]
            return candidate == url or not _list_page(candidate)

        page.on("response", _capture)
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=milliseconds)
            self._trace("LIST_PAGE", url=list_url)
            list_reasons = self.challenge_reasons()
            if list_reasons:
                # REFUSE THE JUMP. A reader shown a verification overlay on a
                # list page does not click through to a post: jumping anyway is
                # the exact "scripted" signature this scheme exists to avoid, and
                # it throws away the challenged document -- the page the operator
                # needs on screen in order to clear it. Raising (instead of
                # recording it as diagnostics and proceeding) hands the caller the
                # access-block path, so the same visible-verification wait runs
                # against the page that is actually showing the challenge.
                self._trace(
                    "LIST_CHALLENGE", url=list_url, reasons=";".join(list_reasons)
                )
                raise EastmoneyBrowserChallengeError(
                    "bar list page is behind verification",
                    reasons=[f"list_page:{reason}" for reason in list_reasons],
                )
            # Only capture from here on: the list page's own document response
            # must never be mistaken for the detail's.
            captured["documents"].clear()
            dwell = self._dwell_on_list_page()
            # LOOK AGAIN, IMMEDIATELY BEFORE THE JUMP (added 2026-09-28).
            # The probe above runs ~0.2s after `domcontentloaded`, and the
            # verification overlay is injected client-side AFTER that -- the
            # dwell exists precisely because a reader pauses on a list page, and
            # it is 0.4-1.6s of the page getting on with its own scripts. Without
            # this second probe, an overlay that appears during the dwell was
            # never seen at all: the run jumped away, destroying the challenge
            # page, and logged NOTHING -- no LIST_CHALLENGE, no block, just the
            # operator's captcha disappearing under them (measured 2026-09-28:
            # 111 navigations across two live streams with a single LIST_CHALLENGE,
            # while the operator watched the slider get wiped twice).
            pre_jump_reasons = self.challenge_reasons()
            if pre_jump_reasons:
                self._trace(
                    "LIST_CHALLENGE", url=list_url, when="pre_jump",
                    reasons=";".join(pre_jump_reasons),
                )
                raise EastmoneyBrowserChallengeError(
                    "bar list page came back challenged during the dwell",
                    reasons=[
                        f"list_page:{reason}" for reason in pre_jump_reasons
                    ],
                )
            self._trace("JUMP", url=url, dwell_sec=round(dwell, 2))
            page.evaluate("u => { location.href = u; }", url)
            page.wait_for_url(
                _landed,
                wait_until="domcontentloaded",
                timeout=milliseconds,
            )
            return self._build_response(
                self._pick_document(captured["documents"], _page_url(page)),
                extra_diagnostics=(
                    {"list_challenge_reasons": list_reasons} if list_reasons else None
                ),
            )
        except (EastmoneyBrowserBoundaryError, EastmoneyBrowserTransportError):
            raise
        except Exception as exc:
            # Name the cause: "no response captured" and "the navigation timed
            # out" were indistinguishable in the log, and that ambiguity is what
            # made the redirect defect take an investigation to find.
            raise EastmoneyBrowserTransportError(
                "browser navigation via the bar list did not yield an Eastmoney "
                f"response ({type(exc).__name__}: {exc})"[:200]
            ) from exc
        finally:
            # A leaked listener would accumulate one closure per fetched detail.
            remove = getattr(page, "remove_listener", None)
            if callable(remove):
                try:
                    remove("response", _capture)
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _pick_document(documents: list[Any], final_url: str | None) -> Any:
        """Prefer the document the browser ended on; else the last one seen.

        `page.goto` returns the main-document response *after* redirects, and
        this reproduces it. Falls back to the last document so a page whose
        final URL we cannot read still yields bytes the parser can classify --
        an error shell then correctly becomes `detail_not_found` instead of a
        transport error.
        """
        if not documents:
            return None
        if final_url:
            wanted = final_url.split("#")[0]
            for response in reversed(documents):
                try:
                    if str(response.url).split("#")[0] == wanted:
                        return response
                except Exception:  # noqa: BLE001
                    continue
        return documents[-1]

    def challenge_reasons(self) -> list[str]:
        """Live-DOM challenge signals; empty when the probe could not run.

        An empty list means "no signal", NOT "proven clean" -- the usual
        fail-open caveat of a probe. `challenge_dom` explains why the live DOM is
        required: the 2026-08-13 challenge was an overlay injected after load, so
        it is absent from `response.body()` and therefore from
        `parser.is_access_block_page`.
        """
        evaluate = getattr(self.page, "evaluate", None)
        if not callable(evaluate):
            return []
        try:
            dom = evaluate(DOM_CHALLENGE_JS)
        except Exception:  # noqa: BLE001
            return []
        return dom_challenge_reasons(dom if isinstance(dom, dict) else {})

    def get(self, url: str, *, timeout: float) -> EastmoneyBrowserResponse:
        _validate_browser_url(url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        # List pages always go through `_get_list_page`, whether or not the click
        # scheme is on: the counters must describe the navigation that actually
        # happened, so that a run with the scheme OFF (`click: 0`, `goto: 2`) is a
        # usable negative control for a run with it ON.
        parts = list_page_parts(url)
        if parts is not None:
            return self._get_list_page(url, parts, timeout=timeout)
        if self.detail_referer_from_list:
            list_url = self._bar_list_url(url)
            if list_url is not None:
                self._trace("FETCH_MODE", url=url, mode="referer")
                return self._get_after_list_visit(url, list_url, timeout=timeout)
        self._trace("FETCH_MODE", url=url, mode="direct")
        return self._goto_page(url, timeout=timeout)[0]

    def _goto_page(
        self, url: str, *, timeout: float
    ) -> tuple[EastmoneyBrowserResponse, Any]:
        """Navigate by URL. Returns (payload, raw response).

        The raw response comes back too because the request shape -- `Referer`,
        `Sec-Fetch-*` -- only exists on `response.request`, and the payload is a
        dataclass carrying no request. Reading it off the payload instead would
        silently produce empty samples that look like a source that sends nothing.
        """
        try:
            response = self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=max(1, int(timeout * 1000)),
            )
            return self._build_response(response), response
        except (EastmoneyBrowserBoundaryError, EastmoneyBrowserTransportError):
            raise
        except Exception as exc:
            raise EastmoneyBrowserTransportError(
                "browser navigation did not yield an Eastmoney response"
            ) from exc

    def _record_list_navigation(self, mode: str, response: Any) -> None:
        """Keep one sampled request shape per mode. First sample wins."""
        if mode in self.list_navigation_samples:
            return
        self.list_navigation_samples[mode] = _request_shape(response)

    def _get_list_page(
        self, url: str, parts: tuple[str, int], *, timeout: float
    ) -> EastmoneyBrowserResponse:
        """Serve one list page, by clicking the pager when we are adjacent to it.

        The click is not an optimisation for its own sake: it is the only way to
        turn a page with the same origin/referrer shape a reader's click produces
        (`list_paging.py`). Everything else about the walk is unchanged -- same
        URL, same body, same counters -- so a page fetched this way is
        indistinguishable downstream from one fetched by URL.

        This method serves **every** list page, flagged or not, so the counters
        below always describe the navigation that really happened.

        FALLING BACK IS THE POINT OF THE COUNTERS
        -----------------------------------------
        If no pager anchor points at the page we asked for, the page is still
        perfectly reachable by URL, and refusing to fetch it would make an opt-in
        navigation scheme able to *break* a walk that worked before it existed. So
        the fallback exists -- and is counted separately (`click_no_anchor`,
        `click_not_taken`), because a silent fallback would make a run look like
        it paged by clicking when it never did.

        The fallback is deliberately narrower than "anything went wrong": it fires
        only when the browser is **still on the previous page**, i.e. no
        navigation was initiated at all. If the browser moved but the document
        could not be captured, that raises instead -- re-navigating after a
        committed navigation is the D-018 trap, which cost a duplicate-request
        loop when it was mistaken for a failed fetch.
        """
        bar, page = parts
        if (
            self.list_click_paging
            and page >= 2
            and self._list_page_position == (bar, page - 1)
        ):
            clicked = self._click_next_list_page(url, parts, timeout=timeout)
            if clicked is not None:
                return clicked
        response, raw_response = self._goto_page(url, timeout=timeout)
        self._list_page_position = parts
        self.list_navigation_counts["goto"] += 1
        self._record_list_navigation("goto", raw_response)
        return response

    def _click_next_list_page(
        self, url: str, parts: tuple[str, int], *, timeout: float
    ) -> EastmoneyBrowserResponse | None:
        """Click the pager anchor for `parts`; None when it had to be skipped.

        Returns a response only when the click really navigated and its document
        was captured. `None` means "caller should navigate by URL instead" -- and
        the caller has already had the reason counted by the time it gets here.
        """
        bar, page = parts
        previous_url = list_page_url(bar, page - 1)
        page_object = self.page
        captured: dict[str, Any] = {"documents": []}
        milliseconds = max(1, int(timeout * 1000))

        def _capture(response: Any) -> None:
            try:
                request = getattr(response, "request", None)
                resource_type = getattr(request, "resource_type", None)
                if resource_type is not None and resource_type != "document":
                    return
                captured["documents"].append(response)
            except Exception:  # noqa: BLE001
                pass

        def _left_previous(page_url: str) -> bool:
            # Any other list page counts: a server-side normalisation redirect
            # never commits the URL we asked for, and the collector's own
            # `pagination_not_progressing` check is the backstop for a click that
            # lands on the page it started from.
            candidate = page_url.split("#")[0]
            return candidate != previous_url and list_page_parts(candidate) is not None

        page_object.on("response", _capture)
        try:
            outcome = page_object.evaluate(CLICK_NEXT_LIST_PAGE_JS, {"page": page})
            if not (isinstance(outcome, dict) and outcome.get("clicked")):
                self.list_navigation_counts["click_no_anchor"] += 1
                return None
            page_object.wait_for_url(
                _left_previous, wait_until="domcontentloaded", timeout=milliseconds
            )
            raw_response = self._pick_document(
                captured["documents"], _page_url(page_object)
            )
            response = self._build_response(
                raw_response,
                extra_diagnostics={
                    "list_navigation": "click",
                    "clicked_href": outcome.get("href"),
                },
            )
            self._list_page_position = parts
            self.list_navigation_counts["click"] += 1
            self._record_list_navigation("click", raw_response)
            return response
        except (EastmoneyBrowserBoundaryError, EastmoneyBrowserTransportError):
            raise
        except Exception as exc:
            if _page_url(page_object) != previous_url:
                # The browser moved, so something was requested from the source.
                # We cannot hand a document to the parser, and re-navigating would
                # duplicate the request: fail loudly instead.
                raise EastmoneyBrowserTransportError(
                    "browser pager click navigated but yielded no Eastmoney "
                    f"document ({type(exc).__name__}: {exc})"[:200]
                ) from exc
            self.list_navigation_counts["click_not_taken"] += 1
            return None
        finally:
            remove = getattr(page_object, "remove_listener", None)
            if callable(remove):
                try:
                    remove("response", _capture)
                except Exception:  # noqa: BLE001
                    pass


class EastmoneyBrowserSocketTransport:
    """Request navigation from a long-lived local browser host.

    The Unix socket contains no browser cookies or storage.  Each response is
    the same sanitized main-document shape returned by
    :class:`EastmoneyBrowserTransport`.
    """

    def __init__(self, socket_path: str | Path) -> None:
        resolved = Path(socket_path).expanduser().resolve()
        if len(str(resolved).encode()) >= 100:
            raise ValueError("browser socket path is too long")
        self.socket_path = resolved

    def get(self, url: str, *, timeout: float) -> EastmoneyBrowserResponse:
        _validate_browser_url(url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        request = json.dumps(
            {"method": "GET", "url": url, "timeout": timeout},
            separators=(",", ":"),
        ).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout + 5.0)
                client.connect(str(self.socket_path))
                client.sendall(request)
                response_bytes = bytearray()
                while b"\n" not in response_bytes:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response_bytes.extend(chunk)
                    if len(response_bytes) > _MAX_SOCKET_RESPONSE_BYTES:
                        raise EastmoneyBrowserTransportError(
                            "browser host response exceeded the size limit"
                        )
        except EastmoneyBrowserTransportError:
            raise
        except (OSError, TimeoutError) as exc:
            raise EastmoneyBrowserTransportError(
                "browser host is unavailable or navigation timed out"
            ) from exc
        if b"\n" not in response_bytes:
            raise EastmoneyBrowserTransportError(
                "browser host returned an incomplete response"
            )
        try:
            payload = json.loads(bytes(response_bytes).split(b"\n", 1)[0])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EastmoneyBrowserTransportError(
                "browser host returned an invalid response"
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            message = (
                payload.get("error")
                if isinstance(payload, dict) and isinstance(payload.get("error"), str)
                else "browser host navigation failed"
            )
            raise EastmoneyBrowserTransportError(message)
        try:
            final_url = payload.get("final_url")
            if not isinstance(final_url, str):
                raise ValueError("missing final URL")
            _validate_browser_url(final_url)
            body = base64.b64decode(payload["body_base64"], validate=True)
            headers = payload.get("headers")
            if not isinstance(headers, dict):
                raise ValueError("invalid headers")
            sanitized_headers = {
                str(key).lower(): str(value)
                for key, value in headers.items()
                if str(key).lower() != "set-cookie"
            }
            return EastmoneyBrowserResponse(
                status_code=int(payload["status_code"]),
                body=body,
                headers=sanitized_headers,
                final_url=final_url,
            )
        except (KeyError, TypeError, ValueError, base64.binascii.Error) as exc:
            raise EastmoneyBrowserTransportError(
                "browser host response has an invalid shape"
            ) from exc
