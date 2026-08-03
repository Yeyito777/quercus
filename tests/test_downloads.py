from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from quercus_tool.downloads import (
    prepare_output_directory,
    private_write,
    safe_filename,
)
from quercus_tool.errors import UnsafeFileError


class DownloadTests(unittest.TestCase):
    def test_filename_is_basename_and_control_safe(self):
        self.assertEqual(safe_filename("../../bad\x00.pdf"), "bad_.pdf")
        self.assertEqual(safe_filename(".."), "quercus-file")

    def test_private_write_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            root = prepare_output_directory(Path(directory) / "downloads")
            path = root / "file.pdf"
            private_write(path, b"one", force=False)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            with self.assertRaises(UnsafeFileError):
                private_write(path, b"two", force=False)
            private_write(path, b"two", force=True)
            self.assertEqual(path.read_bytes(), b"two")

    def test_symlink_output_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(UnsafeFileError):
                prepare_output_directory(link)


if __name__ == "__main__":
    unittest.main()
