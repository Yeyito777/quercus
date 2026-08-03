from __future__ import annotations

import json
import subprocess
import unittest

from quercus_tool.browser_import import (
    CHROMIUM_EPOCH_OFFSET_SECONDS,
    VimbrowserImporter,
)
from quercus_tool.errors import BrowserImportError


class Runner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=self.outputs.pop(0), stderr="")


class BrowserImportTests(unittest.TestCase):
    def test_tab_choice_never_guesses_among_multiple(self):
        runner = Runner([json.dumps({
            "active_tabid": 99,
            "tabs": [
                {"id": 1, "url": "https://q.utoronto.ca/courses/1"},
                {"id": 2, "url": "https://q.utoronto.ca/courses/2"},
            ],
        })])
        with self.assertRaises(BrowserImportError):
            VimbrowserImporter(runner=runner).choose_tab(None)

    def test_chromium_expiry_conversion(self):
        unix = 1_800_000_000
        chromium = (unix + CHROMIUM_EPOCH_OFFSET_SECONDS) * 1_000_000
        self.assertEqual(VimbrowserImporter._expiry({"expires": chromium, "has_expires": True}), unix)
        self.assertEqual(VimbrowserImporter._expiry({"expires": chromium, "has_expires": False}), -1)


if __name__ == "__main__":
    unittest.main()
