"""An idle node must not keep writing its summary.

2026-11-22: on the sg node the job had been completed for ten minutes and the worker was
still writing six meta keys twice a second, because tick() publishes the summary on every
pass where no scope is due. A strip copies the whole database (7.8 GB, ~80 s) while holding
the write lock, so one of those writes raised "database is locked"; the handler then called
engine.pause(), which writes too, and the thread died -- state stayed "running" and nothing
issued requests any more. Fixing the write rate removes the collision; the busy_timeout and
the hardened handler are the belt and braces.
"""
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(APP))
from core import Engine

CONFIG = {"stocks": ["601012"], "from_date": "2025-01-01", "to_date": "2025-01-31"}


class SummaryWriteTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(Path(tempfile.mkdtemp(prefix="summary-writes-")))
        self.engine.create_job(CONFIG)
        self.engine.pause()

    def tearDown(self):
        self.engine.close()

    def _count_writes(self):
        calls = []
        original = self.engine._set_global

        def counting(key, value):
            calls.append(key)
            return original(key, value)

        self.engine._set_global = counting
        try:
            self.engine._publish_stock_summary()
        finally:
            del self.engine._set_global
        return calls

    def test_second_publish_writes_nothing(self):
        self.engine._publish_stock_summary()          # 先让 meta 与摘要一致
        idle = self._count_writes()
        self.assertEqual(idle, [], f"摘要没变时不该再写，实际写了 {idle}")

    def test_publish_writes_again_when_state_changes(self):
        self.engine._publish_stock_summary()
        self.engine._set_global("state", "someone-else-changed-me")
        again = self._count_writes()
        self.assertIn("state", again)

    def test_connection_waits_instead_of_failing_immediately(self):
        self.assertEqual(self.engine.db.execute("PRAGMA busy_timeout").fetchone()[0], 10000)


if __name__ == "__main__":
    unittest.main()
