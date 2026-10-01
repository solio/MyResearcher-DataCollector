"""Actual offline ZIP export/import; no worker or external source requests."""
from contextlib import closing, redirect_stdout, redirect_stderr
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))
import test_core
from test_compatible_store import Ledger
from compatible_store import CompatibleDataStore
from federation import MergeStore, _hash, _json
import transfer

NODE_A = "00000000-0000-4000-8000-000000000001"
NODE_B = "00000000-0000-4000-8000-000000000002"


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.nodes = []

    def tearDown(self):
        for node in self.nodes:
            node.close()
        self.tmp.cleanup()

    def node(self, name, identity=NODE_A):
        directory = self.root / name
        directory.mkdir()
        node = Ledger(directory)
        self.nodes.append(node)
        with node.db:
            node._set("instance_id", identity)
            node._set("compatible_storage_instance", identity)
        node.instance_id = identity
        node.writer = CompatibleDataStore(directory, identity)
        node.writer.sync(node)
        return node

    def acquire(self, node, source=None, body=None):
        source = source or test_core.row()
        rid = node.list([source])
        node.writer.sync(node, rid)
        if body is not None:
            rid = node.detail(source, body)
            node.writer.sync(node, rid)
        return source

    def export(self, node, name="node.zip"):
        path = self.root / name
        result = transfer.export_bundle(node.data_dir, path)
        return path, result

    @staticmethod
    def read_bundle(path):
        with zipfile.ZipFile(path) as archive:
            files = {i.filename: archive.read(i) for i in archive.infolist()}
        manifest = json.loads(files["manifest.json"])
        records = [json.loads(line) for line in files["records.jsonl"].splitlines()]
        return files, manifest, records

    def altered(self, source, name, mutate):
        files, manifest, records = self.read_bundle(source)
        mutate(files, manifest, records)
        for record in records:
            record["evidence_sha256"] = _hash({k: v for k, v in record.items() if k not in {"seq", "evidence_sha256"}})
        files["records.jsonl"] = b"".join((_json(r) + "\n").encode() for r in records)
        for member in manifest["members"]:
            body = files[member["path"]]
            member.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        files["manifest.json"] = _json(manifest).encode()
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for filename, body in files.items():
                archive.writestr(transfer._zip_info(filename), body)
        return path

    def test_running_node_exports_owned_sequence_no_secrets_database_or_worker(self):
        node = self.node("node")
        source = self.acquire(node)
        self.acquire(node, source)
        (node.data_dir / "console.token").write_text("private-console-secret")
        (node.data_dir / "fleet").mkdir()
        (node.data_dir / "fleet/registry.json").write_text('{"token":"private-node-secret"}')
        before = dict(node.db.execute("SELECT key,value FROM meta"))
        with (node.data_dir / "worker.lock").open("w") as lock, patch("core.Engine.__init__", side_effect=AssertionError("no Engine")), patch("core.fetch", side_effect=AssertionError("no source")):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path, result = self.export(node)
        files, manifest, records = self.read_bundle(path)
        self.assertEqual((result["snapshot"], result["counts"]["unique_posts"]), (2, 1))
        self.assertEqual((result["counts"]["raw_responses"], result["counts"]["raw_files"]), (2, 1))
        self.assertEqual(set(files), {"manifest.json", "records.jsonl", *[f"raw/{r[0]}.body" for r in node.db.execute("SELECT DISTINCT sha256 FROM requests")]})
        self.assertTrue(all(b"private-console-secret" not in body and b"private-node-secret" not in body for body in files.values()))
        self.assertEqual(dict(node.db.execute("SELECT key,value FROM meta")), before)
        self.assertEqual([r["seq"] for r in records], [1, 2])
        self.assertFalse(manifest["coverage_complete"])
        self.assertFalse(manifest["model_database_eligible"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_wal_snapshot_excludes_acquisition_committed_during_export(self):
        node = self.node("node")
        node.db.execute("PRAGMA journal_mode=WAL")
        self.acquire(node)
        original, calls = transfer.export_page, []
        def append_after_snapshot(reader, *args, **kwargs):
            page = original(reader, *args, **kwargs)
            if not calls:
                self.acquire(node, test_core.row(post_id=1002))
            calls.append(page)
            return page
        with patch.object(transfer, "export_page", side_effect=append_after_snapshot):
            path, result = self.export(node)
        self.assertEqual(result["snapshot"], 1)
        self.assertEqual(result["source_posts_at_snapshot"], 1)
        self.assertEqual(node.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 2)
        self.assertEqual(len(self.read_bundle(path)[2]), 1)

    def test_two_node_merge_same_post_enrichment_empty_body_and_idempotent_restart(self):
        a, b = self.node("a"), self.node("b", NODE_B)
        self.acquire(a)
        self.acquire(b, body="真实详情")
        self.acquire(b, test_core.row(post_id=1002), "")
        pa, _ = self.export(a, "a.zip")
        pb, _ = self.export(b, "b.zip")
        target = self.root / "hub"
        transfer.merge_bundle(pa, target)
        result = transfer.merge_bundle(pb, target)
        self.assertEqual((result["merged"]["unique_posts"], result["merged"]["body_complete"]), (2, 2))
        self.assertEqual(result["merged"]["observations"], 5)
        self.assertFalse((target / "collector.db").exists())
        with closing(sqlite3.connect(target / "fleet/collector.db")) as db:
            self.assertEqual(db.execute("SELECT content FROM posts WHERE source_item_id='1001'").fetchone()[0], "真实详情")
            self.assertEqual(db.execute("SELECT content FROM posts WHERE source_item_id='1002'").fetchone()[0], "")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM backfill_coverage").fetchone()[0], 0)
        repeated = transfer.merge_bundle(pb, target)
        self.assertEqual(repeated["cursor_before"], repeated["cursor_after"])
        self.assertEqual(repeated["merged"]["observations"], 5)
        old = transfer.merge_bundle(pa, target)
        self.assertEqual(old["merged"]["body_complete"], 2)

    def test_empty_owned_node_bundle_imports_without_claiming_coverage(self):
        node = self.node("node")
        path, exported = self.export(node)
        result = transfer.merge_bundle(path, self.root / "empty-hub")
        self.assertEqual((exported["snapshot"], result["cursor_after"], result["merged"]["unique_posts"]), (0, 0, 0))
        self.assertFalse(result["dataset_complete"])

    def test_unprojected_source_post_is_reported_without_repair_or_fabricated_export(self):
        node = self.node("node")
        self.acquire(node)
        node.list([test_core.row(post_id=1002)])  # Acquired, but not yet projected/journaled.
        path, exported = self.export(node)
        self.assertEqual((exported["source_posts_at_snapshot"], exported["counts"]["unique_posts"], exported["unexported_source_posts"]), (2, 1, 1))
        self.assertEqual(exported["counts_scope"], "latest_version_per_source_post_in_bundle")
        self.assertEqual(node.db.execute("SELECT COUNT(*) FROM export_journal").fetchone()[0], 1)
        result = transfer.merge_bundle(path, self.root / "hub")
        self.assertEqual((result["merged"]["unique_posts"], result["unexported_source_posts"]), (1, 1))

    def test_failed_export_does_not_publish_or_overwrite_and_symlink_raw_rejected(self):
        node = self.node("node")
        self.acquire(node)
        path, _ = self.export(node)
        before = path.read_bytes()
        with self.assertRaisesRegex(transfer.TransferError, "覆盖"):
            transfer.export_bundle(node.data_dir, path)
        self.assertEqual(path.read_bytes(), before)
        raw = node.data_dir / node.db.execute("SELECT raw_ref FROM requests").fetchone()[0]
        original = raw.read_bytes()
        raw.write_bytes(original + b"broken")
        failed = self.root / "failed.zip"
        with self.assertRaises(transfer.TransferError):
            transfer.export_bundle(node.data_dir, failed)
        self.assertFalse(failed.exists())
        self.assertEqual(list(self.root.glob(".collector-export-*")), [])
        raw.unlink()
        other = self.root / "retained.body"
        other.write_bytes(original)
        raw.symlink_to(other)
        with self.assertRaisesRegex(transfer.TransferError, "符号链接"):
            transfer.export_bundle(node.data_dir, failed)

    def test_corrupt_later_detail_and_forged_payload_fail_before_target_creation(self):
        node = self.node("node")
        self.acquire(node, body="来源实际正文")
        package, _ = self.export(node)
        def forge(files, manifest, records):
            records[-1]["post"]["content"] = "伪造正文"
        bad = self.altered(package, "forged.zip", forge)
        target = self.root / "untouched"
        with self.assertRaises(transfer.TransferError):
            transfer.merge_bundle(bad, target)
        self.assertFalse(target.exists())
        def corrupt_raw(files, manifest, records):
            name = next(k for k in files if k.startswith("raw/"))
            files[name] += b"corruption"
        bad = self.altered(package, "corrupt-raw.zip", corrupt_raw)
        with self.assertRaises(transfer.TransferError):
            transfer.merge_bundle(bad, target)
        self.assertFalse(target.exists())

    def test_invalid_sequence_identity_and_credentials_rejected_before_merge(self):
        node = self.node("node")
        self.acquire(node)
        package, _ = self.export(node)
        def sequence(files, manifest, records): records[0]["seq"] = 2
        def identity(files, manifest, records): records[0]["instance_id"] = NODE_B
        def credential(files, manifest, records): records[0]["provenance"]["token"] = "sensitive"
        for label, mutate in (("sequence", sequence), ("identity", identity), ("credential", credential)):
            with self.subTest(label=label):
                bad = self.altered(package, f"{label}.zip", mutate)
                target = self.root / label
                with self.assertRaises(transfer.TransferError):
                    transfer.merge_bundle(bad, target)
                self.assertFalse(target.exists())

    def test_zip_traversal_private_files_symlinks_duplicates_and_limits_rejected(self):
        node = self.node("node")
        self.acquire(node)
        package, _ = self.export(node)
        for name in ("../outside", "console.token", "collector.db"):
            path = self.root / (name.replace("/", "_") + ".zip")
            path.write_bytes(package.read_bytes())
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr(name, b"unexpected")
            with self.assertRaises(transfer.TransferError):
                transfer.merge_bundle(path, self.root / "unsafe")
        symlink = self.root / "link.zip"
        files, _, _ = self.read_bundle(package)
        with zipfile.ZipFile(symlink, "w") as archive:
            for name, body in files.items():
                info = transfer._zip_info(name)
                if name == "records.jsonl": info.external_attr = (stat.S_IFLNK | 0o600) << 16
                archive.writestr(info, body)
        with self.assertRaisesRegex(transfer.TransferError, "普通"):
            transfer.merge_bundle(symlink, self.root / "unsafe")
        duplicate = self.root / "duplicate.zip"
        duplicate.write_bytes(package.read_bytes())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "a") as archive: archive.writestr("records.jsonl", files["records.jsonl"])
        with self.assertRaisesRegex(transfer.TransferError, "重复"):
            transfer.merge_bundle(duplicate, self.root / "unsafe")
        with patch.object(transfer, "MAX_UNCOMPRESSED", 64):
            with self.assertRaisesRegex(transfer.TransferError, "上限"):
                transfer.merge_bundle(package, self.root / "unsafe")
        self.assertFalse((self.root / "unsafe").exists())

    def test_active_destination_unowned_store_and_production_path_refused(self):
        node = self.node("node")
        self.acquire(node)
        package, _ = self.export(node)
        target = self.root / "busy"
        target.mkdir()
        with (target / "worker.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(transfer.TransferError, "运行中的 worker"):
                transfer.merge_bundle(package, target)
        self.assertFalse((target / "fleet").exists())
        target = self.root / "unowned"
        (target / "fleet").mkdir(parents=True)
        unknown = target / "fleet/collector.db"
        unknown.write_bytes(b"unowned")
        with self.assertRaises(transfer.TransferError):
            transfer.merge_bundle(package, target)
        self.assertEqual(unknown.read_bytes(), b"unowned")
        with self.assertRaisesRegex(transfer.TransferError, "生产"):
            transfer.merge_bundle(package, transfer.PRODUCTION_DATA)

    def test_cloned_uuid_conflict_preserves_prior_cursor_body_and_versions(self):
        a, fork = self.node("a"), self.node("fork")
        self.acquire(a, body="原正文")
        self.acquire(fork, body="另一份正文")
        first, _ = self.export(a, "first.zip")
        conflicting, _ = self.export(fork, "conflicting.zip")
        target = self.root / "hub"
        good = transfer.merge_bundle(first, target)
        with self.assertRaises(transfer.TransferError): transfer.merge_bundle(conflicting, target)
        store = MergeStore(target)
        self.assertEqual(store.cursor(NODE_A), good["cursor_after"])
        self.assertEqual(store.status()["observations"], 2)
        self.assertEqual(store.versions("eastmoney_guba", "1001")[-1]["post"]["content"], "原正文")

    def test_local_two_body_conflicts_are_retained_not_blended(self):
        a, b = self.node("a"), self.node("b", NODE_B)
        self.acquire(a, body="甲正文")
        self.acquire(b, body="乙正文")
        pa, _ = self.export(a, "a.zip")
        pb, _ = self.export(b, "b.zip")
        target = self.root / "hub"
        transfer.merge_bundle(pa, target)
        result = transfer.merge_bundle(pb, target)
        self.assertEqual(result["merged"]["unique_posts"], 1)
        self.assertEqual(result["merged"]["conflicts"], 1)
        store = MergeStore(target)
        self.assertEqual(len(store.versions("eastmoney_guba", "1001")), 4)

    def test_cli_returns_json_and_immutable_page_stream_restarts_after_one_hundred(self):
        node = self.node("node")
        rows = [test_core.row(post_id=1000 + i) for i in range(105)]
        rid = node.list(rows)
        node.writer.sync(node, rid)
        path = self.root / "cli.zip"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(transfer.main(["export", "--data-dir", str(node.data_dir), "--output", str(path)]), 0)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        result = transfer.merge_bundle(path, self.root / "hub")
        self.assertEqual((result["cursor_after"], result["merged"]["unique_posts"]), (105, 105))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(transfer.main(["export", "--data-dir", str(node.data_dir), "--output", str(path)]), 1)
        self.assertFalse(json.loads(stderr.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
