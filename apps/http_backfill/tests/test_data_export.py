"""Post-file exports preserve source facts and do not dump private runtime state."""
from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import test_core  # App/source import paths.
from data_export import export_posts
from myresearcher_collector.simple_store import SimplePostStore


class PostExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "collector.db"
        self.store = SimplePostStore(self.db)
        for identity, body in (("1", None), ("2", ""), ("3", '正文,"换行\n第二行')):
            self.store.upsert_post(source="eastmoney_guba", source_item_id=identity, stock_code="601012",
                                  title="列表标题" + identity, content=body, author_id=None, author_name=None,
                                  published_at="2026-10-01T00:00:00Z", url="https://guba.eastmoney.com/news,601012," + identity + ".html",
                                  read_count=None, reply_count=0, like_count=None, forward_count=None)
        self.store.conn.execute("CREATE TABLE secrets(value TEXT)")
        self.store.conn.execute("INSERT INTO secrets VALUES('private-fixture-must-not-export')")
        self.store.conn.commit()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_jsonl_preserves_title_body_missing_empty_and_excludes_private_tables(self):
        before = self.db.read_bytes()
        path = self.root / "posts.jsonl"
        report = export_posts(self.db, path)
        items = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual((report["posts"], report["body_complete"], report["content_missing"]), (3, 2, 1))
        self.assertEqual([item["content"] for item in items], [None, "", '正文,"换行\n第二行'])
        self.assertEqual([item["title"] for item in items], ["列表标题1", "列表标题2", "列表标题3"])
        self.assertTrue(items[0]["content_missing"])
        self.assertFalse(items[1]["content_missing"])
        self.assertTrue(all(not item["model_database_eligible"] for item in items))
        self.assertNotIn("private-fixture", path.read_text())
        self.assertEqual(before, self.db.read_bytes())

    def test_csv_roundtrips_quotes_newline_chinese_and_marks_null(self):
        path = self.root / "posts.csv"
        export_posts(self.db, path, "csv")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            items = list(csv.DictReader(stream))
        self.assertEqual(items[2]["content"], '正文,"换行\n第二行')
        self.assertEqual(items[0]["content_missing"], "True")
        self.assertEqual(items[1]["content_missing"], "False")
        self.assertEqual(items[0]["source_item_id"], "1")

    def test_invalid_missing_or_existing_output_is_not_created_overwritten(self):
        path = self.root / "keep"
        path.write_text("keep")
        with self.assertRaises(ValueError):
            export_posts(self.db, path)
        self.assertEqual(path.read_text(), "keep")
        with self.assertRaises(ValueError):
            export_posts(self.root / "missing.db", self.root / "missing.jsonl")
        with self.assertRaises(ValueError):
            export_posts(self.db, self.root / "bad.jsonl", "html")
        self.assertFalse((self.root / "missing.db").exists())
        with closing(sqlite3.connect(self.root / "invalid.db")) as db:
            db.execute("CREATE TABLE posts(secret TEXT)")
        with self.assertRaises(ValueError):
            export_posts(self.root / "invalid.db", self.root / "invalid.jsonl")
        self.assertFalse((self.root / "invalid.jsonl").exists())
        self.assertFalse(list(self.root.glob(".posts-export-*")))

    def test_publication_race_does_not_overwrite_output_and_cleans_temporary(self):
        path = self.root / "raced.jsonl"
        def fail_link(*args):
            path.write_text("another export")
            raise FileExistsError("already created")
        with patch("data_export.os.link", side_effect=fail_link), self.assertRaises(FileExistsError):
            export_posts(self.db, path)
        self.assertEqual(path.read_text(), "another export")
        self.assertFalse(list(self.root.glob(".posts-export-*")))

    def test_wal_writer_can_continue_while_export_keeps_one_snapshot(self):
        self.store.conn.execute("PRAGMA journal_mode=WAL")
        self.store.conn.commit()
        original_dumps = json.dumps
        mutated = False
        def serialize(item, **options):
            nonlocal mutated
            if not mutated:
                mutated = True
                self.store.conn.execute("UPDATE posts SET content='后来取得的正文' WHERE source_item_id='3'")
                self.store.conn.execute("INSERT INTO posts SELECT source,'4',stock_code,title,content,author_id,author_name,published_at,url,read_count,reply_count,like_count,forward_count,created_at,updated_at FROM posts WHERE source_item_id='2'")
                self.store.conn.commit()
            return original_dumps(item, **options)
        path = self.root / "snapshot.jsonl"
        with patch("data_export.json.dumps", side_effect=serialize):
            report = export_posts(self.db, path)
        items = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(report["posts"], 3)
        self.assertEqual(items[2]["content"], '正文,"换行\n第二行')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
