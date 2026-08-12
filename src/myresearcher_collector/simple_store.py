"""Simple one-row-per-post SQLite store."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS posts (
 source TEXT NOT NULL, source_item_id TEXT NOT NULL, stock_code TEXT NOT NULL,
 title TEXT, content TEXT, author_id TEXT, author_name TEXT,
 published_at TEXT NOT NULL, url TEXT NOT NULL, read_count INTEGER,
 reply_count INTEGER, like_count INTEGER, forward_count INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(source, source_item_id));
CREATE INDEX IF NOT EXISTS idx_posts_stock_published ON posts(stock_code,published_at);"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS backfill_state (
 source TEXT NOT NULL, stock_code TEXT NOT NULL, last_successful_page INTEGER NOT NULL,
 PRIMARY KEY(source, stock_code)
);"""

def _now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")

class SimplePostStore:
    def __init__(self, db_path: str | Path):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.db_path); self.conn.executescript(SCHEMA); self.conn.commit()
    def close(self): self.conn.close()
    def upsert_post(self, *, source, source_item_id, stock_code, title, content, author_id, author_name, published_at, url, read_count, reply_count, like_count, forward_count, updated_at=None):
        exists=self.conn.execute("SELECT 1 FROM posts WHERE source=? AND source_item_id=?",(source,source_item_id)).fetchone() is not None
        now=updated_at or _now()
        self.conn.execute("""INSERT INTO posts(source,source_item_id,stock_code,title,content,author_id,author_name,published_at,url,read_count,reply_count,like_count,forward_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(source,source_item_id) DO UPDATE SET stock_code=excluded.stock_code,title=excluded.title,content=COALESCE(excluded.content,posts.content),author_id=excluded.author_id,author_name=excluded.author_name,published_at=excluded.published_at,url=excluded.url,read_count=excluded.read_count,reply_count=excluded.reply_count,like_count=excluded.like_count,forward_count=excluded.forward_count,updated_at=excluded.updated_at""",(source,source_item_id,stock_code,title,content,author_id,author_name,published_at,url,read_count,reply_count,like_count,forward_count,now,now)); self.conn.commit(); return not exists
    def update_content(self, source, source_item_id, content, *, updated_at=None):
        cur=self.conn.execute("UPDATE posts SET content=?,updated_at=? WHERE source=? AND source_item_id=?",(content,updated_at or _now(),source,source_item_id)); self.conn.commit(); return cur.rowcount==1
    def upsert_source_item(self, item, *, stock_code: str, content: str | None = None, updated_at: str | None = None) -> bool:
        """Store a parser ``SourceItem`` without creating an observation version."""
        return self.upsert_post(
            source=item.source, source_item_id=item.source_item_id, stock_code=stock_code,
            title=item.title, content=content, author_id=item.author_id,
            author_name=item.author_name, published_at=item.published_at.isoformat().replace("+00:00", "Z"),
            url=item.url, read_count=item.read_count, reply_count=item.reply_count,
            like_count=item.like_count, forward_count=item.forward_count, updated_at=updated_at,
        )
    def count(self, source=None, stock_code=None):
        clauses=[]; args=[]
        if source is not None: clauses.append("source=?"); args.append(source)
        if stock_code is not None: clauses.append("stock_code=?"); args.append(stock_code)
        return int(self.conn.execute("SELECT count(*) FROM posts"+(" WHERE "+" AND ".join(clauses) if clauses else ""),args).fetchone()[0])
    def rows(self, source, stock_code):
        self.conn.row_factory=sqlite3.Row
        return self.conn.execute("SELECT * FROM posts WHERE source=? AND stock_code=? ORDER BY published_at,source_item_id",(source,stock_code)).fetchall()
    def last_successful_page(self, source: str, stock_code: str) -> int:
        row = self.conn.execute("SELECT last_successful_page FROM backfill_state WHERE source=? AND stock_code=?", (source, stock_code)).fetchone()
        return int(row[0]) if row else 0
    def mark_page(self, source: str, stock_code: str, page: int) -> None:
        self.conn.execute("""INSERT INTO backfill_state(source,stock_code,last_successful_page) VALUES(?,?,?)
            ON CONFLICT(source,stock_code) DO UPDATE SET last_successful_page=max(last_successful_page,excluded.last_successful_page)""", (source, stock_code, page))
        self.conn.commit()
