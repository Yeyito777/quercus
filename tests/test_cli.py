from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        merged = os.environ.copy()
        merged["PYTHONPATH"] = str(ROOT / "src")
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, "-m", "quercus_tool.cli", *args],
            cwd=ROOT,
            env=merged,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_help_and_version(self):
        help_result = self.run_cli("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("strictly read-only", help_result.stdout)
        version = self.run_cli("--version")
        self.assertEqual(version.returncode, 0)
        self.assertIn("0.1.0", version.stdout)

    def test_missing_json_session_error_is_stable_and_secret_free(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(
                "status", "--json",
                env={
                    "QUERCUS_SESSION_FILE": str(Path(directory) / "missing.json"),
                    "QUERCUS_SESSION_LOCK_FILE": str(Path(directory) / "lock"),
                },
            )
        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["error"]["kind"], "SessionRequiredError")
        self.assertNotIn("cookie", result.stderr.casefold())

    def test_limit_validation_is_usage_error_before_auth(self):
        result = self.run_cli("courses", "--limit", "101", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["kind"], "UsageError")


if __name__ == "__main__":
    unittest.main()
