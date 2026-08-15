"""Simple one-row-per-post SQLite store."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.backfill import merge_coverage_intervals
from myresearcher_collector.page_anchor import PageAnchor

SCHEMA = """CREATE TABLE IF NOT EXISTS posts (
 source TEXT NOT NULL, source_item_id TEXT NOT NULL, stock_code TEXT NOT NULL,
 title TEXT, content TEXT, author_id TEXT, author_name TEXT,
 published_at TEXT NOT NULL, url TEXT NOT NULL, read_count INTEGER,
 reply_count INTEGER, like_count INTEGER, forward_count INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(source, source_item_id));
CREATE INDEX IF NOT EXISTS idx_posts_stock_published ON posts(stock_code,published_at);"""
# Resume is only valid for the exact source/stock/from/to range that saved it;
# coverage is only written by fully completed ranges.
SCHEMA += """
CREATE TABLE IF NOT EXISTS backfill_resume (
 source TEXT NOT NULL, stock_code TEXT NOT NULL,
 from_time TEXT NOT NULL, to_time TEXT NOT NULL,
 last_successful_page INTEGER NOT NULL,
 PRIMARY KEY(source, stock_code, from_time, to_time)
);
CREATE TABLE IF NOT EXISTS backfill_coverage (
 source TEXT NOT NULL, stock_code TEXT NOT NULL,
 covered_from TEXT NOT NULL, covered_to TEXT NOT NULL,
 PRIMARY KEY(source, stock_code, covered_from)
);
CREATE TABLE IF NOT EXISTS backfill_page_anchors (
 source TEXT NOT NULL, stock_code TEXT NOT NULL,
 observed_at TEXT NOT NULL, page_no INTEGER NOT NULL,
 page_min_time TEXT NOT NULL, page_max_time TEXT NOT NULL,
 source_count INTEGER, page_size INTEGER NOT NULL,
 PRIMARY KEY(source, stock_code, observed_at, page_no)
);
DROP TABLE IF EXISTS backfill_state;
"""

def _now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")

class SimplePostStore:
    def __init__(self, db_path: str | Path, *, read_only: bool = False):
        self.db_path=Path(db_path)
        if read_only:
            if not self.db_path.is_file():
                raise ValueError("read_only store requires an existing database file")
            self.conn=sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            self.db_path.parent.mkdir(parents=True,exist_ok=True)
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

    @staticmethod
    def _time_text(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _time_value(text: str) -> datetime:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    def _has_table(self, name: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def backfill_resume_page(self, source: str, stock_code: str, from_time: datetime, to_time: datetime) -> int | None:
        """Resume page only when the persisted resume row matches the exact range."""
        if not self._has_table("backfill_resume"):
            return None
        row = self.conn.execute(
            "SELECT last_successful_page FROM backfill_resume WHERE source=? AND stock_code=? AND from_time=? AND to_time=?",
            (source, stock_code, self._time_text(from_time), self._time_text(to_time)),
        ).fetchone()
        return int(row[0]) if row else None

    def save_backfill_resume(self, source: str, stock_code: str, from_time: datetime, to_time: datetime, page: int) -> None:
        self.conn.execute("""INSERT INTO backfill_resume(source,stock_code,from_time,to_time,last_successful_page) VALUES(?,?,?,?,?)
            ON CONFLICT(source,stock_code,from_time,to_time) DO UPDATE SET last_successful_page=max(last_successful_page,excluded.last_successful_page)""",
            (source, stock_code, self._time_text(from_time), self._time_text(to_time), page))
        self.conn.commit()

    def clear_backfill_resume(self, source: str, stock_code: str, from_time: datetime, to_time: datetime) -> None:
        self.conn.execute(
            "DELETE FROM backfill_resume WHERE source=? AND stock_code=? AND from_time=? AND to_time=?",
            (source, stock_code, self._time_text(from_time), self._time_text(to_time)),
        )
        self.conn.commit()

    def coverage_ranges(self, source: str, stock_code: str) -> list[tuple[datetime, datetime]]:
        """Merged coverage intervals produced by fully completed backfill ranges."""
        if not self._has_table("backfill_coverage"):
            return []
        rows = self.conn.execute(
            "SELECT covered_from, covered_to FROM backfill_coverage WHERE source=? AND stock_code=?",
            (source, stock_code),
        ).fetchall()
        return merge_coverage_intervals(
            (self._time_value(f), self._time_value(t)) for f, t in rows
        )

    def add_coverage(self, source: str, stock_code: str, covered_from: datetime, covered_to: datetime) -> None:
        if covered_from > covered_to:
            return
        merged = merge_coverage_intervals(
            [*self.coverage_ranges(source, stock_code), (covered_from, covered_to)]
        )
        self.conn.execute("DELETE FROM backfill_coverage WHERE source=? AND stock_code=?", (source, stock_code))
        self.conn.executemany(
            "INSERT INTO backfill_coverage(source,stock_code,covered_from,covered_to) VALUES(?,?,?,?)",
            [(source, stock_code, self._time_text(f), self._time_text(t)) for f, t in merged],
        )
        self.conn.commit()

    def save_page_anchor(self, anchor: PageAnchor) -> None:
        self.conn.execute(
            """INSERT INTO backfill_page_anchors
               (source,stock_code,observed_at,page_no,page_min_time,page_max_time,source_count,page_size)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(source,stock_code,observed_at,page_no) DO UPDATE SET
                 page_min_time=excluded.page_min_time, page_max_time=excluded.page_max_time,
                 source_count=excluded.source_count, page_size=excluded.page_size""",
            (anchor.source, anchor.stock_code, self._time_text(anchor.observed_at), anchor.page_no,
             self._time_text(anchor.page_min_time), self._time_text(anchor.page_max_time),
             anchor.source_count, anchor.page_size),
        )
        self.conn.commit()

    def page_anchors(self, source: str, stock_code: str) -> list[PageAnchor]:
        if not self._has_table("backfill_page_anchors"):
            return []
        rows = self.conn.execute(
            """SELECT observed_at,page_no,page_min_time,page_max_time,source_count,page_size
               FROM backfill_page_anchors WHERE source=? AND stock_code=?
               ORDER BY observed_at DESC, page_no""", (source, stock_code)
        ).fetchall()
        return [
            PageAnchor(
                source=source, stock_code=stock_code,
                observed_at=self._time_value(row[0]), page_no=int(row[1]),
                page_min_time=self._time_value(row[2]), page_max_time=self._time_value(row[3]),
                source_count=int(row[4]) if row[4] is not None else None, page_size=int(row[5]),
            )
            for row in rows
        ]
