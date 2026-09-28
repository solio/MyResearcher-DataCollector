#!/bin/zsh
# Retention for the per-run managed browser profiles.
#
# WHY: `ManagedChromiumTransport` picks a NEW `--user-data-dir` for every run
# (`_fresh_managed_profile_path()`), and nothing ever removed them. Measured
# 2026-09-28: **393 directories / 9.5 GB** of dead Chrome profiles — the single
# largest thing this project writes, ~100x the collector database. It was
# recorded as an open item in D-026 (which is about durability, not size, but
# the same "nothing cleans up after the runs" cause).
#
# SAFETY RULES BUILT IN, because a glob that is one level too wide here would
# delete a browser profile that is still in use, or a sibling that is not ours:
#
#  * It only ever looks inside ONE named subdirectory (`PROFILE_SET`, default
#    `eastmoney-managed`). Its siblings in the same parent --
#    `eastmoney-chrome` (the persistent profile `chrome-clean` reuses),
#    `eastmoney-managed-test`, `xueqiu-dedicated` -- are never touched, and the
#    test suite asserts that.
#  * It MOVES the directories out of the profile set; it never deletes in place.
#    Moving to `~/.Trash` is an ordinary reversible move -- but **the Trash is
#    not a backup**: it can be emptied by the operator or by the OS at any time,
#    without this script knowing. Observed 2026-09-28: a run reported
#    `moved=383` and the source was indeed emptied, then the Trash's contents
#    fell while the run was still being checked -- **because the operator was
#    emptying it concurrently**. The lesson is not "the Trash is unreliable", it
#    is: (a) when a count does not reconcile, ask whether a human is acting
#    before inventing a mechanism, and (b) if you want a staging area nothing
#    else will touch, point `TRASH_DIR` at a directory you own
#    (e.g. `TRASH_DIR=/tmp/profile-archive`).
#  * `DRY_RUN=1` prints exactly what would move and changes nothing.
#  * It keeps the newest KEEP profiles and works oldest-first, in batches, and
#    re-counts after every batch: if the count did not fall by exactly the batch
#    size it stops immediately rather than continuing to move things.
#  * Every move is written to a manifest, so "what did it take" is answerable
#    after the fact.
#
# The newest profiles are kept on purpose: they are the ones a live
# investigation is most likely to want (e.g. the profile from the run that just
# hit a challenge).
#
# Usage:
#   scripts/ops/prune_browser_profiles.sh                # DRY_RUN=1 is the default
#   DRY_RUN=0 scripts/ops/prune_browser_profiles.sh      # actually move
#
# Env:
#   PROFILES_ROOT  default <repo>/.runtime/browser-profiles
#   PROFILE_SET    default eastmoney-managed
#   KEEP           default 10    how many newest profiles to keep
#   BATCH          default 10    how many to move per batch
#   TRASH_DIR      default ~/.Trash
#   DRY_RUN        default 1

set -u

REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
PROFILES_ROOT="${PROFILES_ROOT:-$REPO/.runtime/browser-profiles}"
PROFILE_SET="${PROFILE_SET:-eastmoney-managed}"
TARGET="$PROFILES_ROOT/$PROFILE_SET"
KEEP="${KEEP:-10}"
BATCH="${BATCH:-10}"
TRASH_DIR="${TRASH_DIR:-$HOME/.Trash}"
DRY_RUN="${DRY_RUN:-1}"

if [ ! -d "$TARGET" ]; then
  echo "PRUNE_FAILED reason=profile_set_missing path=$TARGET"
  exit 2
fi
if [ ! -d "$TRASH_DIR" ]; then
  echo "PRUNE_FAILED reason=trash_dir_missing path=$TRASH_DIR"
  exit 2
fi
case "$BATCH" in ''|*[!0-9]*) echo "PRUNE_FAILED reason=bad_BATCH value=$BATCH"; exit 2;; esac
case "$KEEP"  in ''|*[!0-9]*) echo "PRUNE_FAILED reason=bad_KEEP value=$KEEP";   exit 2;; esac
[ "$BATCH" -ge 1 ] || { echo "PRUNE_FAILED reason=BATCH_must_be_ge_1"; exit 2; }

PY="${PY:-/Users/mac/.workbuddy/binaries/python/envs/default/bin/python}"
[ -x "$PY" ] || PY=python3

echo "PRUNE_START $(date -u +%FT%TZ) target=$TARGET keep=$KEEP batch=$BATCH dry_run=$DRY_RUN"
echo "PRUNE_NOTE destinations=$TRASH_DIR  -- the Trash can be emptied at any time; it is not a backup"

"$PY" - "$TARGET" "$TRASH_DIR" "$KEEP" "$BATCH" "$DRY_RUN" <<'PY'
import os
import shutil
import sys
from datetime import datetime, timezone

target, trash_dir, keep, batch, dry_run = (
    sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]),
    sys.argv[5] == "1",
)

# Only real directories whose names look like the timestamp the transport
# generates (`%Y%m%d-%H%M%S-%f`). This is the guard that keeps a stray file, or a
# hand-made directory, from being swept up by the retention glob.
def is_profile(name: str) -> bool:
    if not os.path.isdir(os.path.join(target, name)):
        return False
    head = name.split("-")
    return (
        len(head) == 3
        and len(head[0]) == 8 and head[0].isdigit()
        and len(head[1]) == 6 and head[1].isdigit()
        and len(head[2]) == 6 and head[2].isdigit()
    )

entries = sorted(
    (e for e in os.listdir(target) if is_profile(e)),
    key=lambda n: os.path.getmtime(os.path.join(target, n)),
)
total = len(entries)
survivors = entries[-keep:] if keep else []
candidates = entries[: max(0, total - keep)]

if not candidates:
    print(f"PRUNE_NOTHING_TO_DO profiles={total} keep={keep}")
    raise SystemExit(0)

manifest = os.path.join(
    target, f".trash-manifest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
)
print(f"PRUNE_PLAN profiles={total} keep={keep} to_move={len(candidates)}")
print(f"PRUNE_KEEP {survivors[0] if survivors else '-'} .. {survivors[-1] if survivors else '-'}")

if dry_run:
    print(f"PRUNE_DRY_RUN first_to_move={candidates[0]} last_to_move={candidates[-1]}")
    for name in candidates[:3]:
        print(f"PRUNE_WOULD_MOVE {os.path.join(target, name)}")
    print(f"PRUNE_DRY_RUN_OK total_candidates={len(candidates)} (nothing was moved)")
    raise SystemExit(0)

def directory_size(path: str) -> int:
    size = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return size

handle = open(manifest, "a", encoding="utf-8")
moved = 0
try:
    for start in range(0, len(candidates), batch):
        chunk = candidates[start:start + batch]
        for name in chunk:
            src = os.path.join(target, name)
            dst = os.path.join(trash_dir, name)
            if os.path.exists(dst):
                dst = f"{dst}-{os.getpid()}-{moved}"
            size = directory_size(src)
            shutil.move(src, dst)
            moved += 1
            handle.write(
                '{"moved_at":"%s","from":"%s","to":"%s","bytes":%d}\n'
                % (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    src.replace("\\", "/"), dst.replace("\\", "/"), size,
                )
            )
            print(f"PRUNE_MOVED {name} bytes={size}")
        handle.flush()
        after_total = len([e for e in os.listdir(target) if is_profile(e)])
        expected = total - moved
        if after_total != expected:
            print(
                f"PRUNE_FAILED reason=count_mismatch after_batch_expected={expected} "
                f"actual={after_total} moved={moved}"
            )
            raise SystemExit(4)
        print(f"PRUNE_BATCH_OK moved={moved} remaining={after_total}")
finally:
    handle.close()

remaining = len([e for e in os.listdir(target) if is_profile(e)])
print(f"PRUNE_DONE moved={moved} remaining={remaining} manifest={manifest}")
PY

echo "PRUNE_END $(date -u +%FT%TZ)"
