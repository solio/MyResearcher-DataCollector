"""Browser-owned DOM acquisition for the verified public Xueqiu stock page."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .browser_transport import ENTRY_URL
from .collector import symbol_for as _symbol_for


class XueqiuDomTransportError(RuntimeError):
    """The page did not produce a safe, progressing DOM state."""


class XueqiuDomTransport:
    acquisition_mode = "managed-chromium"

    def __init__(self, page: Any, *, runtime: Any | None = None, timeout_ms: int = 20_000) -> None:
        self.page = page
        self.runtime = runtime
        self.timeout_ms = timeout_ms
        self.stock_code: str | None = None
        self.symbol: str | None = None
        self.current_page = 0
        self.detail_close_failures = 0
        self.diagnostics: list[str] = []

    @staticmethod
    def symbol_for(stock_code: str) -> str:
        return _symbol_for(stock_code)

    def open_stock(self, stock_code: str) -> None:
        self.stock_code = stock_code
        self.symbol = self.symbol_for(stock_code)
        if callable(getattr(self.page, "open_stock", None)):
            self.page.open_stock(stock_code)
        else:
            self.page.goto(ENTRY_URL.format(symbol=self.symbol), wait_until="domcontentloaded")
        self._wait_posts_loaded()
        self.current_page = self._active_page(default=1)

    def _wait_posts_loaded(self) -> None:
        if callable(getattr(self.page, "wait_posts_loaded", None)):
            self.page.wait_posts_loaded(self.timeout_ms)
            return
        try:
            self.page.wait_for_selector(
                ".status-list article.timeline__item",
                state="visible", timeout=self.timeout_ms,
            )
        except Exception as exc:
            raise XueqiuDomTransportError("Xueqiu posts did not load") from exc

    def _active_page(self, *, default: int | None = None) -> int:
        if callable(getattr(self.page, "active_page", None)):
            value = self.page.active_page()
            return int(value)
        try:
            text = self.page.locator(
                ".pagination .active, .pagination li.active, [aria-current='page']"
            ).first.inner_text()
            return int(text.strip())
        except Exception:
            if default is not None:
                return default
            raise XueqiuDomTransportError("active page is not observable")

    def read_current_page(self) -> dict[str, Any]:
        if callable(getattr(self.page, "read_dom_page", None)):
            value = self.page.read_dom_page()
            items = list(value)
            return {
                "page_no": self._active_page(default=self.current_page or 1),
                "items": items,
                "active_ids": tuple(str(item.get("status_id")) for item in items),
            }
        try:
            items = self.page.locator("article.timeline__item").evaluate_all(
                """
                nodes => nodes.map(node => {
                  const source = node.querySelector('a.date-and-source');
                  const user = node.querySelector('a.user-name');
                  const body = node.querySelector('.timeline__item__content');
                  const text = selector => {
                    const value = node.querySelector(selector);
                    return value ? (value.innerText || value.textContent || '').trim() : null;
                  };
                  const count = name => {
                    const value = node.querySelector(`[data-${name}], .${name}`);
                    const raw = value ? (value.getAttribute(`data-${name}`) || value.innerText || '') : '';
                    const parsed = Number.parseInt(raw.replace(/[^0-9]/g, ''), 10);
                    return Number.isFinite(parsed) ? parsed : null;
                  };
                  return {
                    status_id: source && (source.getAttribute('data-id') || source.dataset.id),
                    author_id: user && (user.getAttribute('data-user-id') || user.dataset.userId),
                    author_name: user ? (user.innerText || user.textContent || '').trim() : null,
                    url: source && source.href,
                    content: body ? (body.innerText || body.textContent || '').trim() : '',
                    title: text('.timeline__item__title'),
                    time_text_observed: source ? (source.innerText || source.textContent || '').trim() : '',
                    read_count: count('read-count'), reply_count: count('reply-count'),
                    like_count: count('like-count'), forward_count: count('forward-count')
                  };
                })
                """
            )
            return {
                "page_no": self._active_page(default=self.current_page or 1),
                "items": items,
                "active_ids": tuple(str(item.get("status_id")) for item in items),
            }
        except Exception as exc:
            raise XueqiuDomTransportError("Xueqiu post DOM is not readable") from exc

    def goto_page(self, page_no: int, *, previous_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        if page_no < 1:
            raise ValueError("page_no must be positive")
        if callable(getattr(self.page, "goto_page", None)):
            self.page.goto_page(page_no)
        else:
            locator = self.page.get_by_role("link", name=str(page_no), exact=True)
            if locator.count() == 0:
                locator = self.page.get_by_text(str(page_no), exact=True)
            if locator.count() == 0:
                raise XueqiuDomTransportError(f"pagination control {page_no} is unavailable")
            locator.first.click()
        self._wait_posts_loaded()
        deadline = time.monotonic() + self.timeout_ms / 1000
        last_page = None
        while time.monotonic() < deadline:
            page = self.read_current_page()
            last_page = page
            ids = tuple(str(item.get("status_id")) for item in page["items"])
            if page["page_no"] == page_no and (not previous_ids or ids != previous_ids):
                self.current_page = page_no
                return page
            time.sleep(0.05)
        raise XueqiuDomTransportError(
            f"pagination did not progress to page {page_no}; observed={last_page}"
        )

    def read_detail_created_at(self, url: str) -> dict[str, Any]:
        detail_page = self._new_detail_page()
        try:
            detail_page.goto(url, wait_until="domcontentloaded")
            detail_page.wait_for_function(
                "() => Boolean(window.SNOWMAN_STATUS && window.SNOWMAN_STATUS.created_at !== undefined && window.SNOWMAN_STATUS.created_at !== null)",
                timeout=self.timeout_ms,
            )
            value = detail_page.evaluate("() => window.SNOWMAN_STATUS")
            if not isinstance(value, dict):
                raise XueqiuDomTransportError("SNOWMAN_STATUS is not an object")
            return value
        except XueqiuDomTransportError:
            raise
        except Exception as exc:
            raise XueqiuDomTransportError("Xueqiu detail timestamp is unavailable") from exc
        finally:
            try:
                detail_page.close()
            except Exception as exc:
                self.detail_close_failures += 1
                self.diagnostics.append(f"detail_page_close_failed: {type(exc).__name__}")

    def _new_detail_page(self) -> Any:
        context = getattr(self.runtime, "context", None)
        if context is None:
            context = getattr(self.page, "context", None)
            if callable(context):
                context = context()
        new_page = getattr(context, "new_page", None)
        if not callable(new_page):
            raise XueqiuDomTransportError(
                "managed browser context cannot create a temporary detail page"
            )
        try:
            return new_page()
        except Exception as exc:
            raise XueqiuDomTransportError(
                "temporary Xueqiu detail page could not be created"
            ) from exc

    def assert_current_page(
        self, page_no: int, *, expected_ids: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Lightweight state check that never navigates the main list page."""
        page = self.read_current_page()
        if int(page.get("page_no", 0)) != page_no:
            raise XueqiuDomTransportError(
                f"main list page changed during detail lookup: expected {page_no}"
            )
        if expected_ids:
            observed_ids = tuple(str(item.get("status_id")) for item in page.get("items", ()))
            if observed_ids != tuple(expected_ids):
                raise XueqiuDomTransportError(
                    f"main list page IDs changed during detail lookup: expected page {page_no}"
                )
        return page

    def restore_page(
        self, page_no: int, *, expected_ids: tuple[str, ...] = (),
        previous_ids: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return to a target page without applying next-page progress rules.

        ``previous_ids`` is retained as a compatibility alias for callers of
        the first DOM integration.  On restore those IDs describe the target
        page and therefore are checked for equality after navigation; they are
        never passed to ``goto_page`` as a requirement to change.
        """
        if self.stock_code is None:
            raise XueqiuDomTransportError("stock page is not open")
        if previous_ids is not None and not expected_ids:
            expected_ids = previous_ids
        if page_no == 1:
            self.open_stock(self.stock_code)
            page = self.read_current_page()
        else:
            self.open_stock(self.stock_code)
            page = self.goto_page(page_no, previous_ids=())
        if int(page.get("page_no", 0)) != page_no:
            raise XueqiuDomTransportError(
                f"restore did not reach page {page_no}; observed={page.get('page_no')}"
            )
        if expected_ids:
            observed_ids = tuple(str(item.get("status_id")) for item in page.get("items", ()))
            if observed_ids != tuple(expected_ids):
                raise XueqiuDomTransportError(
                    f"restored page {page_no} IDs differ from target page"
                )
        return page

    def close(self) -> None:
        close = getattr(self.runtime, "close", None)
        if callable(close):
            close()


def create_xueqiu_dom_transport(
    acquisition_mode: str = "managed-chromium", *, profile_dir: str | None = None,
) -> XueqiuDomTransport:
    if acquisition_mode != "managed-chromium":
        raise ValueError("Xueqiu DOM production path requires managed-chromium")
    from ..eastmoney_guba.browser_runtime import ManagedChromiumTransport

    runtime = ManagedChromiumTransport(profile_dir=profile_dir)
    runtime._ensure_started()
    return XueqiuDomTransport(runtime.page, runtime=runtime)
