#!/usr/bin/env python3
"""Read-only, snapshot-consistent exports of compatible posts, without secrets."""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

COLUMNS = ("source", "source_item_id", "stock_code", "title", "content", "author_id", "author_name",
           "published_at", "url", "read_count", "reply_count", "like_count", "forward_count", "created_at", "updated_at")
EXTRA = ("content_missing", "research_only", "model_database_eligible")


def export_posts(db_path, output, format="jsonl"):
    """Hold one read transaction while streaming rows to an atomically published file.

    No Engine is started, no ledger/credential table is exported, and incomplete
    body NULL remains distinct from a genuinely acquired empty body in JSONL
    (and by content_missing in CSV). The file is a projection, not a merge bundle.
    """
    if format not in {"jsonl", "csv"}:
        raise ValueError("format 必须是 jsonl 或 csv")
    original, target = Path(db_path), Path(output)
    if original.is_symlink() or not original.is_file():
        raise ValueError("采集数据库必须是已存在的普通文件")
    if target.exists() or target.is_symlink():
        raise ValueError("导出目标已存在，拒绝覆盖")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with closing(sqlite3.connect(original.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            columns = tuple(row[1] for row in db.execute("PRAGMA table_info(posts)"))
            if columns != COLUMNS:
                raise ValueError("数据库的 posts 表不符合现有采集格式")
            captured = datetime.now(timezone.utc).isoformat()
            fd, temporary = tempfile.mkstemp(prefix=".posts-export-", dir=target.parent)
            count, bodies, missing = 0, 0, 0
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                writer = None
                if format == "csv":
                    stream.write("\ufeff")
                    writer = csv.DictWriter(stream, fieldnames=COLUMNS + EXTRA)
                    writer.writeheader()
                for row in db.execute("SELECT * FROM posts ORDER BY source,source_item_id"):
                    item = dict(row)
                    is_missing = item["content"] is None
                    item.update(content_missing=is_missing, research_only=True, model_database_eligible=False)
                    if writer is None:
                        stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                    else:
                        writer.writerow(item)
                    count += 1
                    missing += int(is_missing)
                    bodies += int(not is_missing)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link publication refuses an output created concurrently and
            # cannot replace an existing database, symlink or earlier export.
            os.link(temporary, target)
            os.unlink(temporary)
            temporary = None
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"schema_version": "http-backfill.posts-file.v1", "format": format, "snapshot_at": captured,
                "posts": count, "body_complete": bodies, "content_missing": missing,
                "bytes": target.stat().st_size, "sha256": digest.hexdigest(),
                "research_only": True, "model_database_eligible": False}
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "jsonl"), default="jsonl")
    args = parser.parse_args()
    try:
        result = export_posts(args.db, args.output, args.format)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"导出失败: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
