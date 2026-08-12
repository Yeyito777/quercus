from __future__ import annotations

import json
import subprocess
import unittest

from quercus_tool.browser_import import (
    CANVAS_URL,
    CHROMIUM_EPOCH_OFFSET_SECONDS,
    VimbrowserAuthenticator,
)
from quercus_tool.errors import (
    BrowserImportError,
    SessionRejectedError,
    SessionRequiredError,
)

COOKIE = {
    "name": "canvas_session",
    "value": "credential-value",
    "domain": "q.utoronto.ca",
    "path": "/",
    "secure": True,
    "httponly": True,
    "same_site": "lax",
    "has_expires": False,
    "expires": 0,
}


class Runner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected command: {command}")
        expected, response = self.responses.pop(0)
        if command[1:] != expected:
            raise AssertionError(f"expected {expected!r}, got {command[1:]!r}")
        if isinstance(response, subprocess.CompletedProcess):
            return response
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(response), stderr="")


class Clock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def tabs(*rows, active=44):
    return {"active_tabid": active, "tabs": list(rows)}


class BrowserImportTests(unittest.TestCase):
    def test_persistent_login_uses_exact_named_context_tab_and_restores_focus(self):
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], {
                "active_tabid": 55,
                "url": CANVAS_URL,
                "context": "quercus-helper",
            }),
            (["cookies", "55", CANVAS_URL], {"cookies": [COOKIE]}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        auth = VimbrowserAuthenticator(
            runner=runner,
            profile_loader=lambda _: {"id": 42, "name": "Test Student"},
        )

        session = auth.acquire(interactive=True)

        self.assertEqual(session.renewal_mode, "vimbrowser")
        self.assertEqual(session.source, "vimbrowser-context:quercus-helper")
        self.assertEqual(session.user["id"], 42)
        self.assertEqual(runner.responses, [])
        for _, kwargs in runner.calls:
            self.assertLessEqual(kwargs["timeout"], 30)
            self.assertTrue(kwargs["capture_output"])

    def test_silent_renewal_is_bounded_and_gives_interactive_instruction(self):
        clock = Clock()
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], {
                "active_tabid": 55, "url": CANVAS_URL, "context": "quercus-helper",
            }),
            (["cookies", "55", CANVAS_URL], {"cookies": []}),
            (["cookies", "55", CANVAS_URL], {"cookies": []}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        auth = VimbrowserAuthenticator(runner=runner, clock=clock, sleeper=clock.sleep)

        with self.assertRaisesRegex(SessionRequiredError, r"quercus login --persistent"):
            auth.acquire(interactive=False, expected_user_id=42, timeout_seconds=1)

        self.assertEqual(clock.sleeps, [1.0])
        self.assertEqual(runner.responses, [])

    def test_context_acquisition_tolerates_cookie_manager_startup_race(self):
        command = ["vimbrowser-cli", "cookies", "55", CANVAS_URL]
        unavailable = subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERR tab has no browser",
        )
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], {
                "active_tabid": 55, "url": CANVAS_URL, "context": "quercus-helper",
            }),
            (["cookies", "55", CANVAS_URL], unavailable),
            (["cookies", "55", CANVAS_URL], {"cookies": [COOKIE]}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        clock = Clock()
        auth = VimbrowserAuthenticator(
            runner=runner,
            clock=clock,
            sleeper=clock.sleep,
            profile_loader=lambda _: {"id": 42, "name": "Test Student"},
        )

        session = auth.acquire(interactive=True)

        self.assertEqual(session.user["id"], 42)
        self.assertEqual(clock.sleeps, [1.0])
        self.assertEqual(runner.responses, [])

    def test_explicit_import_does_not_hide_cookie_command_failure(self):
        command = ["vimbrowser-cli", "cookies", "5", CANVAS_URL]
        unavailable = subprocess.CompletedProcess(command, 1, stdout="", stderr="ERR tab has no browser")
        runner = Runner([
            (["tabs", "--json"], tabs(
                {"id": 5, "url": "https://q.utoronto.ca/courses/1", "active": True},
                active=5,
            )),
            (["cookies", "5", CANVAS_URL], unavailable),
        ])

        with self.assertRaises(BrowserImportError):
            VimbrowserAuthenticator(runner=runner).import_session(tab_id=5)
        self.assertEqual(runner.responses, [])

    def test_account_mismatch_fails_without_guessing_and_still_cleans_up(self):
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], {
                "active_tabid": 55, "url": CANVAS_URL, "context": "quercus-helper",
            }),
            (["cookies", "55", CANVAS_URL], {"cookies": [COOKIE]}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        auth = VimbrowserAuthenticator(
            runner=runner,
            profile_loader=lambda _: {"id": 99, "name": "Other Student"},
        )

        with self.assertRaisesRegex(SessionRejectedError, "different account"):
            auth.acquire(interactive=False, expected_user_id=42)
        self.assertEqual(runner.responses, [])

    def test_open_context_response_must_confirm_new_id_context_and_https_host(self):
        cases = [
            {"active_tabid": 44, "url": CANVAS_URL, "context": "quercus-helper"},
            {"active_tabid": 55, "url": CANVAS_URL, "context": "other"},
            {"active_tabid": 55, "url": "https://q.utoronto.ca.evil.example/", "context": "quercus-helper"},
            {"active_tabid": True, "url": CANVAS_URL, "context": "quercus-helper"},
        ]
        for opened in cases:
            with self.subTest(opened=opened):
                runner = Runner([
                    (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
                    (["open-context", "quercus-helper", CANVAS_URL], opened),
                ])
                with self.assertRaises(BrowserImportError):
                    VimbrowserAuthenticator(runner=runner).acquire(interactive=True)
                # An unverified response ID is never used to close or focus a tab.
                self.assertEqual(runner.responses, [])

    def test_short_lived_tab_choice_never_guesses_among_multiple(self):
        runner = Runner([(["tabs", "--json"], tabs(
            {"id": 1, "url": "https://q.utoronto.ca/courses/1"},
            {"id": 2, "url": "https://q.utoronto.ca/courses/2"},
            active=99,
        ))])
        with self.assertRaises(BrowserImportError):
            VimbrowserAuthenticator(runner=runner).choose_tab(None)

    def test_short_lived_import_uses_only_requested_exact_tab(self):
        runner = Runner([
            (["tabs", "--json"], tabs(
                {"id": 5, "url": "https://q.utoronto.ca/courses/1", "active": False},
                {"id": 6, "url": "https://q.utoronto.ca/courses/2", "active": False},
            )),
            (["cookies", "5", CANVAS_URL], {"cookies": [COOKIE]}),
        ])
        auth = VimbrowserAuthenticator(
            runner=runner,
            profile_loader=lambda _: {"id": 42, "name": "Test Student"},
        )
        session = auth.import_session(tab_id=5)
        self.assertEqual(session.renewal_mode, "none")
        self.assertEqual(session.source, "vimbrowser-tab:5")

    def test_command_failures_never_echo_captured_credentials(self):
        secret = "credential-value-and-token"
        command = ["vimbrowser-cli", "cookies"]
        failed = subprocess.CompletedProcess(command, 9, stdout=secret, stderr="token=" + secret)
        runner = Runner([(["cookies"], failed)])
        with self.assertRaises(BrowserImportError) as raised:
            VimbrowserAuthenticator(runner=runner)._run("cookies")
        self.assertNotIn(secret, str(raised.exception))

    def test_context_name_is_validated_before_open(self):
        auth = VimbrowserAuthenticator(context="Not Allowed", runner=Runner([]))
        with self.assertRaises(BrowserImportError):
            auth.acquire(interactive=True)

    def test_chromium_expiry_conversion(self):
        unix = 1_800_000_000
        chromium = (unix + CHROMIUM_EPOCH_OFFSET_SECONDS) * 1_000_000
        self.assertEqual(VimbrowserAuthenticator._expiry({"expires": chromium, "has_expires": True}), unix)
        self.assertEqual(VimbrowserAuthenticator._expiry({"expires": chromium, "has_expires": False}), -1)


if __name__ == "__main__":
    unittest.main()
