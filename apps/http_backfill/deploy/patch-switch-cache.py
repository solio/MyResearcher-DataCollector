"""Make /opt/mihomo/switch.sh latency write the collector's node cache.

Run on a node after the collector reads MIHOMO_CACHE_FILE:

    python3 apps/http_backfill/deploy/patch-switch-cache.py /opt/mihomo/switch.sh

It backs the script up as switch.sh.bak and is safe to re-run.
"""
import shutil
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/mihomo/switch.sh")
text = path.read_text(encoding="utf-8")
original = text

if "CACHE_FILE" not in text:
    text = text.replace(
        'TEST_WORKERS="${MIHOMO_TEST_WORKERS:-10}"',
        'TEST_WORKERS="${MIHOMO_TEST_WORKERS:-10}"\n'
        '    DATA_DIR="${COLLECTOR_DATA_DIR:-/usr/local/src/MyResearcher-DataCollector/apps/http_backfill/data}"\n'
        '    CACHE_FILE="$DATA_DIR/mihomo-nodes.json"',
        1)
    text = text.replace(
        'TEST_TIMEOUT="$TEST_TIMEOUT" TEST_WORKERS="$TEST_WORKERS" FILTER="$FILTER" \\',
        'TEST_TIMEOUT="$TEST_TIMEOUT" TEST_WORKERS="$TEST_WORKERS" FILTER="$FILTER" \\\n'
        '      CACHE_FILE="$CACHE_FILE" \\',
        1)
    text = text.replace("import sys\n", "import sys\nimport time\n", 1)
    marker = "results.sort(key=lambda row:"
    insert = '''cache_path = os.environ.get("CACHE_FILE", "")
if cache_path:
    try:
        now_name = None
        try:
            now_name = get_json(base + "/proxies/WW", 5).get("now")
        except Exception:
            now_name = None
        payload = {"now": now_name, "group": "WW", "test_url": test_url,
                   "measured_at": int(time.time()),
                   "nodes": [{"name": name, "delay": delay}
                             for delay, name, _ in results if delay is not None]}
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(cache_path + ".tmp", cache_path)
        print("已写入缓存：" + cache_path)
    except Exception as exc:
        print("写缓存失败：" + str(exc))
'''
    if marker not in text:
        raise SystemExit("results.sort anchor missing")
    text = text.replace(marker, insert + marker, 1)

if text == original:
    print("nothing to patch")
else:
    shutil.copy2(path, path.with_suffix(".sh.bak"))
    path.write_text(text, encoding="utf-8")
    print("patched", path)
