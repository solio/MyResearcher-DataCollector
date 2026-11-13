"""Read and switch the local mihomo node selection over its HTTP controller.

Equivalent to /opt/mihomo/switch.sh list|latency|<node>, but callable from the
collector container: the controller is reached over the docker bridge instead
of 127.0.0.1, so no host shell access is required.

A latency sweep asks every provider node for a fresh delay and takes tens of
seconds — longer than a console request should ever wait. The sweep therefore
runs as a background job that writes a cache file; reading the node list only
returns that file, and a caller that finds it missing starts the job and polls.
switch.sh latency writes the same file, so a manual sweep and a console load
share one result instead of measuring twice.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def _env(name: str, fallback: str) -> str:
    # An exported-but-empty variable is common in generated compose files; treat it as unset
    # instead of building request paths like "/proxies/WW" with no scheme or host.
    value = (os.environ.get(name) or "").strip()
    return value or fallback


def _base() -> str:
    return _env("MIHOMO_API", "http://127.0.0.1:9090").rstrip("/")


def _group() -> str:
    return _env("MIHOMO_GROUP", "WW")


def _provider() -> str:
    return _env("MIHOMO_PROVIDER", "westworld")


def _test_url() -> str:
    # A list page weighs a fraction of the homepage, so a slow-but-working line is not dropped
    # for pulling one heavy page inside the timeout.
    return _env("MIHOMO_TEST_URL", "https://guba.eastmoney.com/list,600096,f.html")


def _cache_path() -> str:
    return _env("MIHOMO_CACHE_FILE", "/data/mihomo-nodes.json")


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


_state_lock = threading.Lock()
_sweeping = False


def _read_cache():
    path = _cache_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None
    data["cached"] = True
    data["refreshing"] = _sweeping
    data["at"] = os.path.getmtime(path)
    data["age"] = max(0, int(time.time() - data["at"]))
    return data


def _write_cache(payload) -> None:
    path = _cache_path()
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        pass


def _current_now(timeout: float = 8.0):
    try:
        return (_get("/proxies/" + urllib.parse.quote(_group(), safe=""), timeout=timeout) or {}).get("now")
    except Exception:
        return None


def sweep():
    """Measure every provider node once, store the result and return it."""
    provider = _provider()
    test_url = _test_url()
    timeout_ms = max(1000, min(30000, int(_env("MIHOMO_TIMEOUT_MS", "5000"))))
    workers = max(1, min(32, int(_env("MIHOMO_WORKERS", "8"))))
    group = _group()
    # 选择器偶发查不到时不能把 now 写成 null，否则卡片上“有节点”会突然变成“尚未测速”。
    previous = _read_cache() or {}
    now = ((_get("/proxies/" + urllib.parse.quote(group, safe="")) or {}).get("now")
           or previous.get("now"))
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
    payload = {"now": now, "group": group, "test_url": test_url,
               "measured_at": int(time.time()), "nodes": measured}
    _write_cache(payload)
    return dict(payload, cached=False, refreshing=False)


def nodes():
    """Return the last sweep immediately; start one in the background if none exists."""
    cached = _read_cache()
    if cached:
        # Latency is a point-in-time measurement, but the selected node can change after the
        # sweep (the console's switch never rewrites this file). Report the controller's live
        # selection so opening the panel cannot show a node that is no longer in use.
        cached["now"] = _current_now(timeout=2.5) or cached.get("now")
        return cached
    started = refresh_async().get("started", False)
    return {"now": _current_now(), "group": _group(), "test_url": _test_url(),
            "nodes": [], "cached": False, "refreshing": started or _sweeping}


_SELECTION_MEMO = {"at": 0.0, "now": None}


def cached_selection():
    """当前出口节点：以选择器实时值为准，和代理面板（nodes()）同一口径。

    测速缓存只提供延迟，不决定“是哪条线”——缓存里的 now 可能是几分钟前的，
    auto 组随时会换线路，两边读不同来源就会出现“面板有节点、卡片说没测速”。
    """
    payload = _read_cache() or {}
    nodes = payload.get("nodes") or []
    if time.time() - _SELECTION_MEMO["at"] > 30:
        live = _current_now(timeout=2.5)
        # 查询失败不能把已知节点清空：那会让卡片在“有节点/没节点”之间反复跳。
        # 成功才覆盖，失败就继续用上一次的实时值。
        if live:
            _SELECTION_MEMO["now"] = live
            # 同时落到缓存文件：进程重启后内存里没有记忆，靠文件才不会再闪一次“未取得”。
            if nodes and live != payload.get("now"):
                _write_cache(dict(payload, now=live))
                payload["now"] = live
        _SELECTION_MEMO["at"] = time.time()
    now = _SELECTION_MEMO["now"] or payload.get("now")
    delay = next((item.get("delay") for item in nodes if item.get("name") == now), None)
    return {"now": now, "delay": delay, "measured_at": payload.get("measured_at"),
            "source": "selector" if _SELECTION_MEMO["now"] else "sweep"}


def refresh_async():
    """Start one background sweep; a second caller joins the running one."""
    global _sweeping
    with _state_lock:
        if _sweeping:
            return {"started": False, "refreshing": True}
        _sweeping = True

    def run():
        global _sweeping
        try:
            sweep()
        except Exception:
            pass
        finally:
            with _state_lock:
                _sweeping = False

    threading.Thread(target=run, daemon=True).start()
    return {"started": True, "refreshing": True}


def select(name):
    """Switch the selector group to an existing provider node."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("节点名称不能为空")
    group = _group()
    try:
        _put("/proxies/" + urllib.parse.quote(group, safe=""), {"name": name})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:160].strip()
        raise ValueError("节点控制器拒绝切换（HTTP " + str(exc.code) + "）：" + (detail or "无返回内容")) from exc
    except urllib.error.URLError as exc:
        raise ValueError("无法连接节点控制器：" + str(getattr(exc, "reason", exc))) from exc
    now = (_get("/proxies/" + urllib.parse.quote(group, safe="")) or {}).get("now")
    return {"now": now, "group": group}
