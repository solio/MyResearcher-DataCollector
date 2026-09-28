#!/bin/zsh
# Off-machine durability for the collector database (D-026's open item).
#
# WHY THIS EXISTS
# ---------------
# `data/collector.db` is the whole project's source of truth and it lived on
# exactly one Mac with no copy anywhere: searching this repo for
# `VACUUM INTO|.backup()|rsync|rclone|ossutil` returned nothing. D-026 decided
# NOT to move the database to a server (there is no second writer, and the sizes
# and concurrency measurements do not justify it) and to fix the real gap
# instead -- durability -- with a consistent local snapshot plus a ONE-WAY copy
# off the machine.
#
# WHY `VACUUM INTO` AND NOT `cp`
# ------------------------------
# The database is written by a live collector. `cp` of a file being written can
# capture a torn page image; `VACUUM INTO` runs inside SQLite and produces a
# transactionally consistent, compacted copy of everything committed at that
# moment, and it is safe to run against a live database. It takes a read lock
# for the duration of the copy (~1s at 100MB), which can delay one writer
# commit. That delay is the whole cost and it is acceptable; the alternative
# (stopping collection to back up) costs a rate-limited window instead.
#
# WHAT IT DELIBERATELY DOES NOT DO
# --------------------------------
# * It never touches credentials. `aliyunpan`, if used, reads its own already
#   logged-in config (`$ALIYUNPAN_CONFIG_DIR/aliyunpan_config.json`). That file
#   holds a plaintext refresh token for the user's cloud-drive account: treat it
#   as a password, never commit it, never read it out of this script.
# * It never runs `aliyunpan sync`. `sync` can mirror (and with `exclusive`,
#   delete) a remote directory, and a backup tool that can delete remote files
#   is a worse failure mode than no backup tool. It uploads exactly one file.
# * It never uploads unless asked. Nothing leaves the machine unless
#   ALIYUNPAN_REMOTE is set.
#
# Usage:
#   scripts/ops/backup_snapshot.sh                    # local snapshot only
#   scripts/ops/backup_snapshot.sh --verify           # snapshot + re-verify an existing one
#   ALIYUNPAN_REMOTE=/备份/myresearcher scripts/ops/backup_snapshot.sh
#
# The project's chosen destination is **/myresearcher-backup on the 阿里云盘
# 备份盘** (created 2026-09-28; the drive's root holds unrelated personal files,
# so backups get their own directory and never share one). Verified end to end
# the same day: upload 77.44MB in 7s, download back in 24s, and the downloaded
# copy is byte-identical (sha1 c43f0c8d5c394d2e0d9439ee846f1c72d558a52b) and
# opens with `PRAGMA integrity_check = ok`, 212817 posts, all four tables.
# The account has 三方权益包 未开通, which affects *download* acceleration only --
# 24s for 77MB is the throttled rate and it is fine for a daily snapshot.
#
# Env:
#   BACKUP_DIR       default runtime/backups   (gitignored)
#   KEEP             default 7                 local snapshots retained
#   ALIYUNPAN_REMOTE remote directory for the one-way copy; unset = local only
#   ALIYUNPAN_BIN    default `aliyunpan` on PATH

set -e
set -u
set -o pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
DB="${DB:-$REPO/data/collector.db}"
BACKUP_DIR="${BACKUP_DIR:-$REPO/runtime/backups}"
KEEP="${KEEP:-7}"
PY="${PY:-/Users/mac/.workbuddy/binaries/python/envs/default/bin/python}"
[ -x "$PY" ] || PY=python3

# --verify answers the only question that matters about a backup: can this file
# be read back as a database? A snapshot that exists, has the right size and
# cannot be opened is the failure mode that stays invisible until the day it is
# needed. It re-checks the newest snapshot and compares it against the manifest
# line written for it; it does not touch the live database, so it is safe to run
# any time (e.g. from a cron minutes after the snapshot).
if [ "${1:-}" = "--verify" ]; then
  SNAP=$(ls -1t "$BACKUP_DIR"/collector-*.db 2>/dev/null | head -1 || true)
  if [ -z "$SNAP" ]; then
    echo "VERIFY_FAILED reason=no_snapshot_in dir=$BACKUP_DIR"
    exit 2
  fi
  "$PY" - "$SNAP" "$BACKUP_DIR/manifest.jsonl" <<'PY'
import json, os, sqlite3, sys
snap_path, manifest = sys.argv[1], sys.argv[2]
recorded = None
if os.path.exists(manifest):
    for line in open(manifest, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("snapshot") == os.path.basename(snap_path):
            recorded = entry
problems = []
try:
    snap = sqlite3.connect(f"file:{snap_path}?mode=ro", uri=True)
    integrity = snap.execute("PRAGMA integrity_check").fetchone()[0]
    tables = {r[0] for r in snap.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    rows = snap.execute("SELECT count(*) FROM posts").fetchone()[0]
except sqlite3.Error as exc:
    print(f"VERIFY_FAILED reason=cannot_open error={exc}")
    raise SystemExit(3)
if integrity != "ok":
    problems.append(f"integrity_check={integrity}")
missing = sorted({"posts", "backfill_coverage", "backfill_resume",
                  "backfill_page_anchors"} - tables)
if missing:
    problems.append(f"missing_tables={missing}")
if recorded is None:
    problems.append("no_manifest_entry_for_this_snapshot")
elif rows < recorded.get("snapshot_posts", 0):
    problems.append(f"rows_below_manifest file={rows} manifest={recorded['snapshot_posts']}")
if problems:
    print(f"VERIFY_FAILED snapshot={os.path.basename(snap_path)} reason={';'.join(problems)}")
    raise SystemExit(4)
print(
    f"VERIFY_OK snapshot={os.path.basename(snap_path)} posts={rows} "
    f"bytes={os.path.getsize(snap_path)} integrity=ok manifest=matched"
)
PY
  exit $?
fi

if [ ! -f "$DB" ]; then
  echo "BACKUP_FAILED reason=database_missing path=$DB"
  exit 2
fi
mkdir -p "$BACKUP_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SNAP="$BACKUP_DIR/collector-$STAMP.db"
# A manual run right after a scheduled one lands in the same second, and
# `VACUUM INTO` refuses to write over an existing file -- which would turn "run
# it again now" into a confusing BACKUP_FAILED. Uniquify instead.
_suffix=1
while [ -e "$SNAP" ]; do
  SNAP="$BACKUP_DIR/collector-$STAMP-$_suffix.db"
  _suffix=$((_suffix + 1))
done
MANIFEST="$BACKUP_DIR/manifest.jsonl"

echo "BACKUP_START $(date -u +%FT%TZ) db=$DB snapshot=$SNAP"

# Snapshot + verify + manifest, one interpreter so the numbers cannot be read
# from two different moments. Exits non-zero on anything that would make the
# snapshot unusable, because a backup that silently produces a truncated file
# is worse than a loud failure.
"$PY" - "$DB" "$SNAP" "$MANIFEST" <<'PY'
import json, os, sqlite3, sys
from datetime import datetime, timezone

db_path, snap_path, manifest = sys.argv[1], sys.argv[2], sys.argv[3]
if os.path.exists(snap_path):
    print(f"BACKUP_FAILED reason=snapshot_path_exists path={snap_path}")
    raise SystemExit(2)

live = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
live_rows = live.execute("SELECT count(*) FROM posts").fetchone()[0]
live_content = live.execute(
    "SELECT count(*) FROM posts WHERE content IS NOT NULL"
).fetchone()[0]
live_page_count = live.execute("PRAGMA page_count").fetchone()[0]
live_page_size = live.execute("PRAGMA page_size").fetchone()[0]

try:
    live.execute("VACUUM INTO ?", (snap_path,))
except sqlite3.Error as exc:
    print(f"BACKUP_FAILED reason=vacuum_into_failed error={exc}")
    raise SystemExit(3)

snap = sqlite3.connect(f"file:{snap_path}?mode=ro", uri=True)
integrity = snap.execute("PRAGMA integrity_check").fetchone()[0]
snap_rows = snap.execute("SELECT count(*) FROM posts").fetchone()[0]
snap_content = snap.execute(
    "SELECT count(*) FROM posts WHERE content IS NOT NULL"
).fetchone()[0]
# The schema must have come along too; a snapshot without the tables is a file,
# not a backup.
tables = {
    row[0]
    for row in snap.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
}
required = {"posts", "backfill_coverage", "backfill_resume", "backfill_page_anchors"}
missing = sorted(required - tables)

size = os.path.getsize(snap_path)
entry = {
    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "snapshot": os.path.basename(snap_path),
    "bytes": size,
    "live_posts": live_rows,
    "snapshot_posts": snap_rows,
    "live_content": live_content,
    "snapshot_content": snap_content,
    "live_pages": live_page_count,
    "live_page_size": live_page_size,
    "integrity_check": integrity,
    "missing_tables": missing,
}
with open(manifest, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

problems = []
if integrity != "ok":
    problems.append(f"integrity_check={integrity}")
if missing:
    problems.append(f"missing_tables={missing}")
# The live count can only be <= the snapshot count if a writer committed between
# the two reads; it can never show the snapshot LOST rows that the live db had
# before the copy started.
if snap_rows < live_rows:
    problems.append(f"rows_regressed live={live_rows} snapshot={snap_rows}")
if problems:
    print(f"BACKUP_FAILED reason={';'.join(problems)}")
    raise SystemExit(4)

print(
    f"SNAPSHOT_OK posts={snap_rows} content={snap_content} "
    f"bytes={size} integrity=ok"
)
PY

# Retention: only files this script itself creates, and every deletion is named.
# `ls -t` on a strict glob inside our own backup dir -- never a wildcard over
# anything else, never recursive.
OLD=$(ls -1t "$BACKUP_DIR"/collector-*.db 2>/dev/null | tail -n +$((KEEP + 1)) || true)
if [ -n "$OLD" ]; then
  echo "$OLD" | while IFS= read -r victim; do
    echo "RETENTION_DELETE $victim"
    rm -f "$victim"
  done
fi

if [ -z "${ALIYUNPAN_REMOTE:-}" ]; then
  echo "UPLOAD_SKIPPED reason=ALIYUNPAN_REMOTE_not_set (local snapshot only)"
  echo "BACKUP_DONE $(date -u +%FT%TZ)"
  exit 0
fi

ALIYUNPAN_BIN="${ALIYUNPAN_BIN:-aliyunpan}"
if ! command -v "$ALIYUNPAN_BIN" >/dev/null 2>&1; then
  echo "UPLOAD_FAILED reason=aliyunpan_not_installed hint='brew install aliyunpan'"
  exit 5
fi

# One file, one direction. No `sync`, so this can never delete anything remote.
# The CLI uses its own saved login; this script passes no credentials.
#
# Argument order verified against the installed CLI (`aliyunpan upload --help`):
#     aliyunpan upload <本地文件> ... <目标目录>
# so the remote directory is the LAST argument, and this call is correct.
# Deliberately NOT passing `-ow`: that moves an existing same-named remote file
# to the recycle bin ("覆盖上传，已存在的同名文件会被移到回收站"), i.e. it would
# make the backup tool able to delete remote data -- the exact failure mode the
# docstring rules out. Snapshots are timestamped, so a name collision would mean
# something is already wrong and should fail loudly instead.
set +e
"$ALIYUNPAN_BIN" upload "$SNAP" "$ALIYUNPAN_REMOTE"
UPLOAD_RC=$?
set -e
if [ "$UPLOAD_RC" -ne 0 ]; then
  echo "UPLOAD_FAILED rc=$UPLOAD_RC remote=$ALIYUNPAN_REMOTE (snapshot kept locally)"
  exit 6
fi
echo "UPLOAD_OK remote=$ALIYUNPAN_REMOTE file=$(basename "$SNAP")"
echo "BACKUP_DONE $(date -u +%FT%TZ)"
