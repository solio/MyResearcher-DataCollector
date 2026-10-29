"""Read and switch the local mihomo node selection over its HTTP controller.

Equivalent to /opt/mihomo/switch.sh list|latency|<node>, but callable from the
collector container: the controller is reached over the docker bridge instead
of 127.0.0.1, so no host shell access is required.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import urllib.parse
import urllib.request


def _base() -> str:
    return os.environ.get("MIHOMO_API", "http://127.0.0.1:9090").rstrip("/")


def _headers(request: urllib.request.Request) -> urllib.request.Request:
    secret = os.environ.get("MIHOMO_SECRET", "")
    if secret:
        request.add_header("Authorization", "Bearer " + secret)
    return request


def _get(path: str, timeout: float = 8.0):
    request = _headers(urllib.request.Request(_base() + path))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _put(path: str, payload, timeout: float = 8.0) -> int:
    request = _headers(urllib.request.Request(
        _base() + path, data=json.dumps(payload).encode("utf-8"), method="PUT"))
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def _group() -> str:
    return os.environ.get("MIHOMO_GROUP", "WW")


def nodes():
    """List provider nodes with a delay measured against the source URL."""
    provider = os.environ.get("MIHOMO_PROVIDER", "westworld")
    test_url = os.environ.get("MIHOMO_TEST_URL", "https://guba.eastmoney.com/")
    timeout_ms = max(1000, min(30000, int(os.environ.get("MIHOMO_TIMEOUT_MS", "5000"))))
    workers = max(1, min(32, int(os.environ.get("MIHOMO_WORKERS", "8"))))
    group = _group()
    now = (_get("/proxies/" + urllib.parse.quote(group, safe="")) or {}).get("now")
    proxies = (_get("/providers/proxies/" + urllib.parse.quote(provider, safe="")) or {}).get("proxies", [])
    names = [item.get("name") for item in proxies if isinstance(item, dict) and item.get("name")]

    query = urllib.parse.urlencode({"url": test_url, "timeout": timeout_ms})

    def delay(name: str):
        path = ("/providers/proxies/" + urllib.parse.quote(provider, safe="")
                + "/" + urllib.parse.quote(name, safe="") + "/healthcheck?" + query)
        try:
            data = _get(path, timeout=timeout_ms / 1000 + 5)
            value = data.get("delay")
            return name, int(value) if isinstance(value, (int, float)) and value > 0 else None
        except Exception:
            return name, None

    measured = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for name, value in pool.map(delay, names):
            if value is not None:
                measured.append({"name": name, "delay": value})
    measured.sort(key=lambda item: item["delay"])
    return {"now": now, "group": group, "test_url": test_url, "nodes": measured}


def select(name):
    """Switch the selector group to an existing provider node."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("节点名称不能为空")
    group = _group()
    _put("/proxies/" + urllib.parse.quote(group, safe=""), {"name": name})
    now = (_get("/proxies/" + urllib.parse.quote(group, safe="")) or {}).get("now")
    return {"now": now, "group": group}
