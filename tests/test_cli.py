from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from quercus_tool.canvas import DiscoveryList
from quercus_tool.cli import command_files, command_pages

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

    def test_file_json_marks_normal_collection_complete(self):
        quercus = Mock()
        quercus.files.return_value = DiscoveryList([{"id": 1, "name": "one.pdf"}])
        output = io.StringIO()
        args = Namespace(course="10", limit=10, since=None, search=None, json=True)
        with (
            patch("quercus_tool.cli.course_context", return_value=({"id": 10, "name": "Course"}, quercus)),
            redirect_stdout(output),
        ):
            command_files(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["data"]["discovery"], {"source": "collection", "complete": True})
        self.assertEqual(payload["data"]["files"][0]["id"], 1)

    def test_page_human_output_explicitly_warns_about_module_incompleteness(self):
        quercus = Mock()
        quercus.pages.return_value = DiscoveryList(
            [{"id": 2, "url": "recordings", "title": "Recordings"}],
            source="modules",
            complete=False,
            reason="course pages collection is disabled; only linked pages were discovered",
        )
        output = io.StringIO()
        args = Namespace(course="10", limit=10, search="recording", json=False)
        with (
            patch("quercus_tool.cli.course_context", return_value=({"id": 10, "name": "Course"}, quercus)),
            redirect_stdout(output),
        ):
            command_pages(args)
        rendered = output.getvalue()
        self.assertIn("Discovery: modules (incomplete", rendered)
        self.assertIn("collection is disabled", rendered)
        self.assertIn("Recordings", rendered)


if __name__ == "__main__":
    unittest.main()
