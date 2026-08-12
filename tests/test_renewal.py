from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import session

from quercus_tool.errors import SessionRejectedError, SessionRequiredError
from quercus_tool.renewal import load_or_refresh_session
from quercus_tool.session import load_session, save_session


class VimbrowserAuthenticator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def acquire(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RenewalTests(unittest.TestCase):
    def test_valid_session_does_not_launch_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.json"
            lock = root / "lock"
            current = session(renewal_mode="vimbrowser")
            save_session(current, path=path, lock=lock)
            browser = VimbrowserAuthenticator(session(renewal_mode="vimbrowser"))
            result, _, profile = load_or_refresh_session(
                path=path,
                lock=lock,
                validator=lambda value: (object(), value.user),
                vimbrowser_authenticator=browser,
            )
            self.assertEqual(result.user["id"], current.user["id"])
            self.assertEqual(profile["id"], current.user["id"])
            self.assertEqual(browser.calls, [])

    def test_imported_expired_session_requires_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.json"
            lock = root / "lock"
            save_session(session(renewal_mode="none"), path=path, lock=lock)

            def rejected(_):
                raise SessionRejectedError("expired")

            with self.assertRaises(SessionRequiredError):
                load_or_refresh_session(path=path, lock=lock, validator=rejected)

    def test_persistent_session_refreshes_and_saves_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.json"
            lock = root / "lock"
            old = session(renewal_mode="vimbrowser", user_id=42)
            renewed = session(renewal_mode="vimbrowser", user_id=42)
            save_session(old, path=path, lock=lock)
            browser = VimbrowserAuthenticator(renewed)
            calls = 0

            def validate(value):
                nonlocal calls
                calls += 1
                if value.imported_at == old.imported_at and calls <= 2:
                    raise SessionRejectedError("expired")
                return object(), value.user

            result, _, _ = load_or_refresh_session(
                path=path,
                lock=lock,
                validator=validate,
                vimbrowser_authenticator=browser,
            )
            self.assertEqual(result.user["id"], 42)
            self.assertEqual(browser.calls, [{"interactive": False, "expected_user_id": 42}])
            self.assertEqual(load_session(path=path).user["id"], 42)


if __name__ == "__main__":
    unittest.main()
