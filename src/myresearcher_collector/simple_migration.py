"""Flatten legacy observations into the one-row-per-post store."""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
from .simple_store import SimplePostStore

def migrate_legacy_observations(old_db: str | Path, new_db: str | Path, *, source="eastmoney_guba", stock_code="601012"):
    old=sqlite3.connect(old_db); old.row_factory=sqlite3.Row; store=SimplePostStore(new_db)
    try:
        rows=old.execute("""SELECT o.* FROM source_item_observations o JOIN observation_scopes s ON s.observation_id=o.observation_id WHERE o.source=? AND s.scope_key=? AND o.observation_version=(SELECT max(o2.observation_version) FROM source_item_observations o2 WHERE o2.source=o.source AND o2.source_item_id=o.source_item_id) ORDER BY o.published_at_utc,o.source_item_id""",(source,f"stock:{stock_code}")).fetchall()
        inserted=updated=0
        for row in rows:
            list_only=json.loads(row["source_metadata_json"]).get("content_source")=="list_title"
            made=store.upsert_post(source=source,source_item_id=row["source_item_id"],stock_code=stock_code,title=row["title"],content=None if list_only else row["content"],author_id=row["author_id"],author_name=row["author_name"],published_at=row["published_at_utc"],url=row["url"],read_count=row["read_count"],reply_count=row["reply_count"],like_count=row["like_count"],forward_count=row["forward_count"],updated_at=row["observed_at_utc"])
            inserted+=int(made); updated+=int(not made)
        bounds=store.conn.execute("SELECT min(published_at),max(published_at) FROM posts WHERE source=? AND stock_code=?",(source,stock_code)).fetchone()
        return {"old_unique_posts":len(rows),"new_posts":store.count(source,stock_code),"inserted":inserted,"updated":updated,"earliest":bounds[0],"latest":bounds[1]}
    finally: store.close(); old.close()
