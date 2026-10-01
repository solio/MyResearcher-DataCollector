"""The deployment wrapper stops before migration and never starts on failure."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class MigrationWrapperTests(unittest.TestCase):
    def run_wrapper(self, failing_command=None, args=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "migrate-storage.sh"
            shutil.copyfile(Path(__file__).resolve().parents[1] / wrapper.name, wrapper)
            docker = root / "docker"
            docker.write_text("""#!/usr/bin/env python3
import os, sys, json
arguments = sys.argv[1:]
index, profile = 1, None
while index < len(arguments) and arguments[index] == '-f':
    profile = arguments[index + 1]
    index += 2
command = arguments[index]
record = {'arguments': arguments, 'command': command, 'profile': profile, 'cwd': os.getcwd()}
if command == 'exec':
    record['stdin'] = sys.stdin.read()
with open(os.environ['MIGRATION_TEST_TRACE'], 'a') as stream:
    stream.write(json.dumps(record) + '\\n')
if command == os.environ.get('MIGRATION_TEST_FAIL'):
    raise SystemExit(23)
""")
            docker.chmod(0o700)
            trace = root / "trace"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "MIGRATION_TEST_TRACE": str(trace), "MIGRATION_TEST_FAIL": failing_command or ""}
            result = subprocess.run(["bash", str(wrapper), *args], cwd="/", env=env, text=True, capture_output=True)
            commands = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
            return result, commands

    def test_success_runs_offline_migration_between_stop_and_start(self):
        result, commands = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["command"] for c in commands], ["build", "stop", "run", "up", "exec"])
        self.assertEqual(commands[2]["arguments"], ["compose", "run", "--rm", "--no-deps", "collector-console", "python", "apps/http_backfill/migrate_storage.py", "--data-dir", "/data"])
        self.assertEqual(commands[3]["arguments"], ["compose", "up", "-d", "--no-build", "collector-console"])
        self.assertTrue(all(c["profile"] is None for c in commands))
        self.assertIn("http://127.0.0.1:8790/healthz", commands[4]["stdin"])

    def test_failed_migration_leaves_worker_stopped(self):
        result, commands = self.run_wrapper("run")
        self.assertEqual(result.returncode, 23)
        self.assertEqual([c["command"] for c in commands], ["build", "stop", "run"])
        self.assertIn("旧库仅在完整校验成功后删除", result.stderr)

    def test_failed_build_does_not_stop_running_old_worker(self):
        result, commands = self.run_wrapper("build")
        self.assertEqual(result.returncode, 23)
        self.assertEqual([c["arguments"] for c in commands], [["compose", "build", "collector-console"]])


    def test_node_preset_is_retained_for_build_stop_migrate_start_and_health(self):
        result, commands = self.run_wrapper(args=("--node",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["command"] for c in commands], ["build", "stop", "run", "up", "exec"])
        for command in commands:
            self.assertEqual(command["profile"], "compose.node.yml")
            self.assertEqual(command["arguments"][:3], ["compose", "-f", "compose.node.yml"])
            self.assertEqual(Path(command["cwd"]).name, Path(commands[0]["cwd"]).name)
        self.assertEqual(commands[2]["arguments"][3:], ["run", "--rm", "--no-deps", "collector-console", "python", "apps/http_backfill/migrate_storage.py", "--data-dir", "/data"])
        self.assertEqual(commands[3]["arguments"][3:], ["up", "-d", "--no-build", "collector-console"])
        self.assertIn("http://127.0.0.1:8790/healthz", commands[4]["stdin"])

    def test_failed_node_migration_never_restores_hub_or_starts_service(self):
        result, commands = self.run_wrapper("run", ("--node",))
        self.assertEqual(result.returncode, 23)
        self.assertEqual([c["command"] for c in commands], ["build", "stop", "run"])
        self.assertTrue(all(c["profile"] == "compose.node.yml" for c in commands))
        self.assertIn("bash migrate-storage.sh --node", result.stderr)

    def test_failed_node_build_does_not_stop_old_worker(self):
        result, commands = self.run_wrapper("build", ("--node",))
        self.assertEqual(result.returncode, 23)
        self.assertEqual([c["arguments"] for c in commands], [["compose", "-f", "compose.node.yml", "build", "collector-console"]])

    def test_unknown_or_extra_arguments_rejected_before_any_docker_command(self):
        for arguments in (("--hub",), ("node",), ("--node", "extra"), ("--node", "--node"), ("--port", "8791")):
            with self.subTest(arguments=arguments):
                result, commands = self.run_wrapper(args=arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(commands, [])
                self.assertIn("Usage: bash migrate-storage.sh [--node]", result.stderr)


if __name__ == "__main__":
    unittest.main()
