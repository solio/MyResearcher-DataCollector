#!/bin/bash
# Random-input smoke test for the Waygate app on a connected HarmonyOS device.
#
# HarmonyOS has no `monkey`, so this drives `uitest uiInput` instead: random taps, swipes and
# (rarely) Back, while checking after every round that the process is still alive. When it dies
# the script pulls the app's own crash.log / fault.log and a screenshot into reports/.
#
#   bash scripts/qa/smoke-waygate.sh [rounds] [delay-ms]
#
# Defaults: 200 rounds, 300 ms between actions.
set -uo pipefail

ROUNDS="${1:-200}"
DELAY_MS="${2:-300}"
BUNDLE=com.myresearcher.waygate
HDC="${HDC:-hdc}"
FILES="/data/app/el2/100/base/$BUNDLE/haps/entry/files"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="reports/smoke-$STAMP"

if ! "$HDC" list targets 2>/dev/null | grep -q .; then
  echo "没有设备：先 hdc tconn <手机IP:端口>"; exit 2
fi

mkdir -p "$OUT"
# No `wm` on HarmonyOS: take the panel resolution from the render service, and wake the device so
# injected taps actually land on the app instead of a sleeping screen.
"$HDC" shell "power-shell wakeup" >/dev/null 2>&1
sleep 1
SIZE=$("$HDC" shell "hidumper -s RenderService -a screen" 2>/dev/null | tr -d '\r' |
  grep -o 'physical resolution=[0-9]*x[0-9]*' | head -1 | cut -d= -f2)
if [ -z "$SIZE" ]; then
  echo "读不到屏幕分辨率（hidumper -s RenderService -a screen）"; exit 2
fi
W=${SIZE%x*}; H=${SIZE#*x}
# Keep taps in the content area: avoid the status bar (top 12%) and the gesture bar (bottom 6%).
Y0=$((H / 8)); Y1=$((H * 94 / 100))

echo "设备 $SIZE，共 $ROUNDS 轮，间隔 ${DELAY_MS}ms，结果写入 $OUT"
"$HDC" shell "aa start -a EntryAbility -b $BUNDLE" >/dev/null 2>&1
sleep 1

rnd() { echo $((RANDOM % ($2 - $1) + $1)); }

fail_dump() {
  echo "!! 第 $1 轮后进程不在了" | tee -a "$OUT/report.txt"
  {
    echo "=== crash.log ==="
    "$HDC" shell "cat $FILES/crash.log 2>/dev/null | tail -30" 2>/dev/null | tr -d '\r'
    echo "=== fault.log ==="
    "$HDC" shell "cat $FILES/fault.log 2>/dev/null | tail -20" 2>/dev/null | tr -d '\r'
  } >> "$OUT/report.txt"
  "$HDC" shell "uitest screenCap -p /data/local/tmp/smoke.png" >/dev/null 2>&1
  "$HDC" file recv /data/local/tmp/smoke.png "$OUT/screen.png" >/dev/null 2>&1
  echo "日志与截图已保存到 $OUT"
  exit 1
}

for i in $(seq 1 "$ROUNDS"); do
  if [ $((i % 5)) -eq 0 ]; then
    "$HDC" shell "aa start -a EntryAbility -b $BUNDLE" >/dev/null 2>&1
  fi
  roll=$(rnd 0 100)
  if [ "$roll" -lt 70 ]; then
    "$HDC" shell "uitest uiInput click $(rnd 40 $((W - 40))) $(rnd $Y0 $Y1)" >/dev/null 2>&1
  elif [ "$roll" -lt 92 ]; then
    fx=$(rnd 40 $((W - 40))); fy=$(rnd $Y0 $Y1)
    tx=$(rnd 40 $((W - 40))); ty=$(rnd $Y0 $Y1)
    "$HDC" shell "uitest uiInput swipe $fx $fy $tx $ty 800" >/dev/null 2>&1
  else
    "$HDC" shell "uitest uiInput keyEvent Back" >/dev/null 2>&1
  fi
  sleep "$(awk "BEGIN{print $DELAY_MS/1000}")"
  if [ -z "$("$HDC" shell "pidof $BUNDLE" 2>/dev/null | tr -d '\r')" ]; then
    fail_dump "$i"
  fi
done

echo "完成 $ROUNDS 轮，进程一直存活" | tee "$OUT/report.txt"
