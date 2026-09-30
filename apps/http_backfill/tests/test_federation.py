"""Offline node evidence export/merge invariants; no source transports."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))
from compatible_store import CompatibleDataStore
from federation import FederationError, MergeStore, export_page, export_raw, _hash
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import parser as guba
from test_compatible_store import Ledger
from test_core import row


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.nodes = []
        self.store = MergeStore(self.root / "hub")

    def tearDown(self):
        for node in self.nodes:
            node.close()
        self.tmp.cleanup()

    def node(self, name):
        directory = self.root / name
        directory.mkdir()
        node = Ledger(directory)
        with node.db:
            node._set("instance_id", name)
        node.writer = CompatibleDataStore(directory, name)
        node.writer.sync(node)
        self.nodes.append(node)
        return node

    def acquire(self, node, source=None, body=None):
        source = source or row()
        request = node.list([source])
        node.writer.sync(node, request)
        if body is not None:
            request = node.detail(source, body)
            node.writer.sync(node, request)
        return source

    def merge(self, node, **kwargs):
        identity = node._get("instance_id")
        return self.store.merge_page(identity, export_page(node, **kwargs), lambda rid: export_raw(node, rid))

    def posts(self, store=None):
        with closing(sqlite3.connect((store or self.store).db_path)) as db:
            db.row_factory = sqlite3.Row
            return {r["source_item_id"]: dict(r) for r in db.execute("SELECT * FROM posts")}

    @staticmethod
    def resign(record):
        payload = {k: v for k, v in record.items() if k not in {"seq", "evidence_sha256"}}
        record["evidence_sha256"] = _hash(payload)

    def test_immutable_journal_baseline_and_incremental_snapshot(self):
        node = self.node("node-a")
        source = self.acquire(node)
        before = export_page(node)
        with node.db:
            node.db.execute("DROP TRIGGER export_journal_no_delete")
            node.db.execute("DROP TRIGGER export_journal_no_update")
            node.db.execute("DROP TABLE export_journal")
        result = node.writer.sync(node)
        self.assertEqual(result["projected"], 0)
        self.assertEqual(export_page(node)["items"], before["items"])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            node.db.execute("DELETE FROM export_journal")
        node.db.rollback()
        original = export_page(node, limit=1)
        request = node.detail(source, "新取得正文")
        node.writer.sync(node, request)
        frozen = export_page(node, after=1, snapshot=original["snapshot"])
        self.assertEqual(frozen["items"], [])
        self.assertEqual(export_page(node, after=1)["items"][0]["post"]["content"], "新取得正文")

    def test_raw_export_validates_path_hash_and_is_instance_namespaced(self):
        node = self.node("node-a")
        request = node.list([row()])
        raw = export_raw(node, request)
        self.assertEqual(raw["instance_id"], "node-a")
        path = node.data_dir / node.db.execute("SELECT raw_ref FROM requests WHERE id=?", (request,)).fetchone()[0]
        path.write_bytes(path.read_bytes() + b"corruption")
        with self.assertRaisesRegex(FederationError, "SHA-256"):
            export_raw(node, request)
        with node.db:
            node.db.execute("UPDATE requests SET raw_ref='../../outside' WHERE id=?", (request,))
        with self.assertRaisesRegex(FederationError, "路径"):
            export_raw(node, request)

    def test_overlap_enriches_one_compatible_post_and_retains_both_instances(self):
        a, b = self.node("node-a"), self.node("node-b")
        self.acquire(a)
        self.acquire(b, body="实际正文")
        self.merge(a)
        self.merge(b)
        self.assertEqual(self.posts()["1001"]["content"], "实际正文")
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(len(self.store.versions(guba.SOURCE, "1001")), 3)
        status = self.store.status()
        self.assertEqual((status["unique_posts"], status["body_complete"], status["instances"]), (1, 1, 2))
        self.assertFalse(status["coverage_complete"])
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                             {"posts", "backfill_resume", "backfill_coverage", "backfill_page_anchors"})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM backfill_coverage").fetchone()[0], 0)

    def test_observed_empty_detail_survives_newer_missing_list(self):
        a, b = self.node("node-a"), self.node("node-b")
        self.acquire(a, body="")
        self.merge(a)
        b.clock.advance(1000)
        self.acquire(b)
        self.merge(b)
        self.assertEqual(self.posts()["1001"]["content"], "")
        self.assertEqual(self.store.status()["body_complete"], 1)

    def test_identity_and_body_conflicts_keep_versions_and_deterministic_whole_winner(self):
        a, b = self.node("node-a"), self.node("node-b")
        self.acquire(a, row(user_nickname="甲作者"), "甲正文")
        self.acquire(b, row(user_nickname="乙作者"), "乙正文")
        self.merge(a)
        self.merge(b)
        reverse = MergeStore(self.root / "reverse")
        for node in (b, a):
            reverse.merge_page(node._get("instance_id"), export_page(node), lambda rid, node=node: export_raw(node, rid))
        self.assertEqual(self.posts(), self.posts(reverse))
        self.assertEqual(self.posts()["1001"]["author_name"], "乙作者")
        self.assertEqual(self.posts()["1001"]["content"], "乙正文")
        self.assertEqual({c["kind"] for c in self.store.conflicts()}, {"identity", "body"})
        self.assertEqual(len(self.store.versions(guba.SOURCE, "1001")), 4)

    def test_incremental_replay_reuses_hash_raw_and_resumes_after_restart(self):
        node = self.node("node-a")
        self.acquire(node, body="真实正文")
        first = export_page(node, limit=1)
        self.store.merge_page("node-a", first, lambda rid: export_raw(node, rid))
        def should_not_transfer(_):
            raise AssertionError("raw already exists")
        self.store.merge_page("node-a", first, should_not_transfer)
        self.assertEqual(self.store.status()["observations"], 1)
        self.store = MergeStore(self.root / "hub")
        after = self.store.cursor("node-a")
        self.merge(node, after=after)
        self.assertEqual(self.store.cursor("node-a"), 2)
        self.assertEqual(self.store.status()["observations"], 2)
        self.assertEqual(self.posts()["1001"]["content"], "真实正文")

    def test_corrupt_raw_whole_page_retains_previous_cursor_and_no_versions(self):
        node = self.node("node-a")
        self.acquire(node, body="真实正文")
        def corrupt(rid):
            raw = export_raw(node, rid)
            if rid == 2:
                raw["body_base64"] = "Y29ycnVwdA=="
            return raw
        with self.assertRaisesRegex(FederationError, "SHA-256"):
            self.store.merge_page("node-a", export_page(node), corrupt)
        self.assertEqual(self.store.cursor("node-a"), 0)
        self.assertEqual(self.store.status()["observations"], 0)
        self.assertEqual(self.posts(), {})
        self.merge(node)
        self.assertEqual(self.store.cursor("node-a"), 2)

    def test_signed_but_false_post_title_body_and_time_fail_independent_raw_validation(self):
        node = self.node("node-a")
        self.acquire(node, body="真实正文")
        for field, value in (("title", "伪造标题"), ("content", "伪造正文"), ("created_at", "2020-01-01T00:00:00.000000Z")):
            with self.subTest(field=field):
                page = copy.deepcopy(export_page(node))
                page["items"][-1]["post"][field] = value
                self.resign(page["items"][-1])
                with self.assertRaises(FederationError):
                    self.store.merge_page("node-a", page, lambda rid: export_raw(node, rid))
                self.assertEqual(self.store.cursor("node-a"), 0)
                self.assertEqual(self.store.status()["observations"], 0)

    def test_sequence_gaps_mutated_immutable_versions_and_wrong_instance_rejected(self):
        node = self.node("node-a")
        self.acquire(node)
        page = export_page(node)
        bad = copy.deepcopy(page)
        bad["items"][0]["seq"] = 2
        with self.assertRaisesRegex(FederationError, "序列"):
            self.store.merge_page("node-a", bad, lambda rid: export_raw(node, rid))
        with self.assertRaisesRegex(FederationError, "实例"):
            self.store.merge_page("wrong-node", page, lambda rid: export_raw(node, rid))
        self.merge(node)
        mutated = copy.deepcopy(page)
        mutated["items"][0]["coverage_complete"] = True
        self.resign(mutated["items"][0])
        with self.assertRaisesRegex(FederationError, "不同事实"):
            self.store.merge_page("node-a", mutated, lambda rid: export_raw(node, rid))
        self.assertEqual(self.store.cursor("node-a"), 1)

    def test_posts_commit_failure_leaves_cursor_then_restart_repairs_locally(self):
        node = self.node("node-a")
        self.acquire(node, body="真实正文")
        with patch.object(SimplePostStore, "upsert_post", side_effect=sqlite3.OperationalError("disk fixture")):
            with self.assertRaisesRegex(FederationError, "disk fixture"):
                self.merge(node)
        self.assertEqual(self.store.cursor("node-a"), 0)
        self.assertEqual(self.store.status()["pending_instances"], ["node-a"])
        self.assertEqual(self.store.status()["observations"], 2)
        self.store = MergeStore(self.root / "hub")
        self.assertEqual(self.store.cursor("node-a"), 2)
        self.assertEqual(self.store.status()["pending_instances"], [])
        self.assertEqual(self.posts()["1001"]["content"], "真实正文")

    def test_changed_source_updates_only_its_affected_post(self):
        node = self.node("node-a")
        for number in ("1001", "1002", "1003"):
            self.acquire(node, row(number))
        self.merge(node)
        before = self.posts()
        request = node.detail(row("1002"), "只更新此帖")
        node.writer.sync(node, request)
        original = SimplePostStore.upsert_post
        with patch.object(SimplePostStore, "upsert_post", autospec=True, side_effect=original) as upsert:
            self.merge(node, after=3)
            self.assertEqual(upsert.call_count, 1)
        after = self.posts()
        self.assertEqual(before["1001"], after["1001"])
        self.assertEqual(before["1003"], after["1003"])
        self.assertEqual(after["1002"]["content"], "只更新此帖")

    def test_corrupt_pending_raw_restart_serves_other_nodes_then_repairs_from_node_evidence(self):
        a, b = self.node("node-a"), self.node("node-b")
        self.acquire(a, body="待恢复正文")
        with patch.object(SimplePostStore, "upsert_post", side_effect=sqlite3.OperationalError("disk fixture")):
            with self.assertRaises(FederationError):
                self.merge(a)
        raw = export_raw(a, 2)
        path = self.store.raw_dir / f"{raw['sha256']}.body"
        path.write_bytes(b"corrupt-local")
        self.store = MergeStore(self.root / "hub")
        self.assertEqual(self.store.cursor("node-a"), 0)
        self.assertIn("node-a", self.store.status()["recovery_errors"])
        self.acquire(b, row("1002"))
        self.merge(b)
        self.assertEqual(set(self.posts()), {"1002"})
        self.merge(a)
        self.assertEqual(self.store.cursor("node-a"), 2)
        self.assertEqual(self.store.status()["recovery_errors"], {})
        self.assertEqual(self.posts()["1001"]["content"], "待恢复正文")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), raw["sha256"])
        quarantine = list(self.store.raw_dir.glob("*.corrupt-*.body"))
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(quarantine[0].read_bytes(), b"corrupt-local")

    def test_unknown_destination_and_production_directory_are_never_modified(self):
        unknown = self.root / "unknown" / "fleet"
        unknown.mkdir(parents=True)
        with closing(sqlite3.connect(unknown / "collector.db")) as db:
            db.execute("CREATE TABLE protected(value TEXT)")
            db.commit()
        before = (unknown / "collector.db").read_bytes()
        with self.assertRaisesRegex(FederationError, "所有权"):
            MergeStore(unknown.parent)
        self.assertEqual((unknown / "collector.db").read_bytes(), before)
        with self.assertRaisesRegex(FederationError, "生产"):
            MergeStore(REPO / "data")

    def test_export_response_budget_truncates_large_body_pages_without_skipping(self):
        node = self.node("node-a")
        for number in ("1001", "1002"):
            self.acquire(node, row(number), "x" * (5 * 1024 * 1024))
        first = export_page(node, limit=100)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_after"], 3)
        next_page = export_page(node, after=first["next_after"], snapshot=first["snapshot"])
        self.assertEqual([item["seq"] for item in next_page["items"]], [4])
        self.assertFalse(next_page["has_more"])


if __name__ == "__main__":
    unittest.main()
