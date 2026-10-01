"""The deployment wrapper stops before migration and never starts on failure."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class MigrationWrapperTests(unittest.TestCase):
    def run_wrapper(self, failing_command=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "migrate-storage.sh"
            shutil.copyfile(Path(__file__).resolve().parents[1] / wrapper.name, wrapper)
            docker = root / "docker"
            docker.write_text("""#!/usr/bin/env python3
import os, sys
with open(os.environ['MIGRATION_TEST_TRACE'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
if len(sys.argv)>2 and sys.argv[2] == os.environ.get('MIGRATION_TEST_FAIL'):
    raise SystemExit(23)
""")
            docker.chmod(0o700)
            trace = root / "trace"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "MIGRATION_TEST_TRACE": str(trace), "MIGRATION_TEST_FAIL": failing_command or ""}
            result = subprocess.run(["bash", str(wrapper)], cwd="/", env=env, text=True, capture_output=True)
            commands = trace.read_text().splitlines()
            return result, commands

    def test_success_runs_offline_migration_between_stop_and_start(self):
        result, commands = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c.split()[1] for c in commands], ["build", "stop", "run", "up", "exec"])
        self.assertIn("--rm --no-deps collector-console python apps/http_backfill/migrate_storage.py --data-dir /data", commands[2])
        self.assertEqual(commands[3], "compose up -d --no-build collector-console")

    def test_failed_migration_leaves_worker_stopped(self):
        result, commands = self.run_wrapper("run")
        self.assertEqual(result.returncode, 23)
        self.assertEqual([c.split()[1] for c in commands], ["build", "stop", "run"])
        self.assertIn("旧库仅在完整校验成功后删除", result.stderr)

    def test_failed_build_does_not_stop_running_old_worker(self):
        result, commands = self.run_wrapper("build")
        self.assertEqual(result.returncode, 23)
        self.assertEqual(commands, ["compose build collector-console"])


if __name__ == "__main__":
    unittest.main()
