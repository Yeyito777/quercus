from __future__ import annotations

import json
import stat
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from helpers import session

from quercus_tool.errors import SessionRejectedError, UnsafeFileError
from quercus_tool.persistent_browser import PersistentBrowserAuthenticator
from quercus_tool.session import CanvasSession, load_session, save_session


class SessionStorageTests(unittest.TestCase):
    def test_browser_page_profile_accepts_canvas_string_user_id(self):
        class Page:
            @staticmethod
            def evaluate(_):
                return {"id": "123456", "name": "Test Student", "primary_email": "student@example.edu"}

        profile = PersistentBrowserAuthenticator._profile_from_page(Page())
        self.assertEqual(profile["id"], 123456)
        self.assertEqual(profile["primaryEmail"], "student@example.edu")

    def test_public_projection_never_contains_cookie_material(self):
        current = session(renewal_mode="persistent-browser")
        serialized = json.dumps(current.public())
        self.assertNotIn("example-cookie-value", serialized)
        self.assertNotIn("test_session", serialized)
        self.assertTrue(current.public()["automaticRenewal"])

    def test_cookie_outside_utoronto_is_rejected(self):
        with self.assertRaises(SessionRejectedError):
            CanvasSession.from_browser(
                [{
                    "name": "x", "value": "y", "domain": "evil.example", "path": "/",
                    "expires": -1, "secure": True, "sameSite": "Lax",
                }],
                {"id": 1}, source="test", renewal_mode="none",
            )

    def test_save_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "session.json"
            lock = Path(directory) / "state" / "lock"
            current = session()
            save_session(current, path=path, lock=lock)
            loaded = load_session(path=path)
            self.assertEqual(loaded.user["id"], current.user["id"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_symlink_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.json"
            real.write_text(json.dumps(asdict(session())))
            link = root / "session.json"
            link.symlink_to(real)
            with self.assertRaises(UnsafeFileError):
                load_session(path=link)

    def test_account_switch_is_rejected(self):
        with self.assertRaises(SessionRejectedError):
            session(user_id=42).assert_account({"id": 99, "name": "Other"})


if __name__ == "__main__":
    unittest.main()
