from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quercus_tool.browser_import import (
    CANVAS_URL,
    CHROMIUM_EPOCH_OFFSET_SECONDS,
    SIGN_IN_SETTLE_POLLS,
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


WEBLOGIN = "https://idpz.utorauth.utoronto.ca/idp/profile/SAML2/Redirect/SSO?execution=e1s1"
_state = tempfile.TemporaryDirectory()
_environment = patch.dict(os.environ, {"XDG_STATE_HOME": _state.name})


def setUpModule():
    # The helper-tab ledger defaults to the state directory; keep it private to the tests.
    _environment.start()


def tearDownModule():
    _environment.stop()
    _state.cleanup()


def opened_helper(url=CANVAS_URL, *, loading=False):
    return {"id": 55, "url": url, "active": False, "loading": loading, "context": "quercus-helper"}


class BrowserImportTests(unittest.TestCase):
    def test_compact_open_response_fetches_exact_context_tab(self):
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], {"tabs": 2, "active_tabid": 55}),
            (["tabs", "--json"], tabs(
                {"id": 44, "url": "https://example.com/", "active": False},
                {"id": 55, "url": CANVAS_URL, "active": True, "context": "quercus-helper"},
                active=55,
            )),
            (["focus", "55"], {}),
            (["cookies", "55", CANVAS_URL], {"cookies": [COOKIE]}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        auth = VimbrowserAuthenticator(runner=runner, profile_loader=lambda _: {"id": 42})
        session = auth.acquire(interactive=True)
        self.assertEqual(session.source, "vimbrowser-context:quercus-helper")
        self.assertEqual(runner.responses, [])

    def test_persistent_login_uses_exact_named_context_tab_and_restores_focus(self):
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], tabs(
                {"id": 44, "url": "https://example.com/", "active": True},
                {"id": 55, "url": CANVAS_URL, "active": False, "context": "quercus-helper"},
            )),
            (["focus", "55"], {}),
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
            (["open-context", "quercus-helper", CANVAS_URL], tabs(
                {"id": 44, "url": "https://example.com/", "active": True},
                {"id": 55, "url": CANVAS_URL, "active": False, "context": "quercus-helper"},
            )),
            (["cookies", "55", CANVAS_URL], {"cookies": []}),
            (["tabs", "--json"], tabs(opened_helper())),
            (["cookies", "55", CANVAS_URL], {"cookies": []}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        auth = VimbrowserAuthenticator(runner=runner, clock=clock, sleeper=clock.sleep)

        with self.assertRaisesRegex(SessionRequiredError, r"quercus login --persistent"):
            auth.acquire(interactive=False, expected_user_id=42, timeout_seconds=1)

        self.assertEqual(clock.sleeps, [1.0])
        self.assertEqual(runner.responses, [])

    def test_silent_renewal_stops_early_once_resting_on_weblogin(self):
        clock = Clock()
        polls = []
        # Loading or Canvas pages reset the count; only settled sign-in pages count.
        states = [opened_helper(WEBLOGIN, loading=True), opened_helper()] + [opened_helper(WEBLOGIN)] * SIGN_IN_SETTLE_POLLS
        for state in states:
            polls += [(["cookies", "55", CANVAS_URL], {"cookies": []}), (["tabs", "--json"], tabs(state))]
        runner = Runner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], tabs(
                {"id": 44, "url": "https://example.com/", "active": True}, opened_helper(),
            )),
            *polls,
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        auth = VimbrowserAuthenticator(runner=runner, clock=clock, sleeper=clock.sleep)

        with self.assertRaisesRegex(SessionRequiredError, r"needs human authentication"):
            auth.acquire(interactive=False, expected_user_id=42)

        self.assertEqual(len(clock.sleeps), len(states) - 1)
        self.assertLess(clock.value, 75)
        self.assertEqual(runner.responses, [])

    def test_termination_signal_still_closes_the_helper_tab(self):
        def terminate():
            os.kill(os.getpid(), signal.SIGTERM)
            return {"cookies": []}

        class SignallingRunner(Runner):
            def __call__(self, command, **kwargs):
                if command[1] == "cookies":
                    self.calls.append((command, kwargs))
                    self.responses.pop(0)
                    terminate()
                return super().__call__(command, **kwargs)

        runner = SignallingRunner([
            (["tabs", "--json"], tabs({"id": 44, "url": "https://example.com/", "active": True})),
            (["open-context", "quercus-helper", CANVAS_URL], tabs(
                {"id": 44, "url": "https://example.com/", "active": True}, opened_helper(),
            )),
            (["cookies", "55", CANVAS_URL], {}),
            (["close-tab", "55"], {}),
            (["focus", "44"], {}),
        ])
        previous = signal.getsignal(signal.SIGTERM)
        try:
            with self.assertRaises(SystemExit) as raised:
                VimbrowserAuthenticator(runner=runner).acquire(interactive=False)
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assertEqual(raised.exception.code, 128 + signal.SIGTERM)
        self.assertEqual(runner.responses, [])

    def test_leaked_helper_tabs_from_dead_runs_are_closed_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "helper-tabs.json"
            dead = 2**22 + 12345  # above pid_max defaults, so never alive
            now = time.time()
            ledger.write_text(json.dumps({"tabs": [
                {"id": 10, "context": "quercus-helper", "pid": dead, "openedAt": now - 60},  # leaked on weblogin
                {"id": 11, "context": "quercus-helper", "pid": dead, "openedAt": now - 60},  # owner navigated to a course
                {"id": 12, "context": "quercus-helper", "pid": os.getppid(), "openedAt": now},  # its run is alive
                {"id": 13, "context": "other", "pid": dead, "openedAt": now - 60},  # another context's run
                {"id": 14, "context": "quercus-helper", "pid": dead, "openedAt": now - 60},  # already gone
            ]}))
            before = tabs(
                {"id": 44, "url": "https://example.com/", "active": True},
                {"id": 10, "url": WEBLOGIN, "context": "quercus-helper"},
                {"id": 11, "url": "https://q.utoronto.ca/courses/1", "context": "quercus-helper"},
                {"id": 12, "url": WEBLOGIN, "context": "quercus-helper"},
                {"id": 13, "url": WEBLOGIN, "context": "other"},
                {"id": 20, "url": WEBLOGIN, "context": "quercus-helper"},  # never recorded: not ours
            )
            runner = Runner([
                (["tabs", "--json"], before),
                (["close-tab", "10"], {}),
                (["open-context", "quercus-helper", CANVAS_URL], {"tabs": 7}),
                (["tabs", "--json"], tabs(*before["tabs"], opened_helper())),
                (["focus", "55"], {}),
                (["cookies", "55", CANVAS_URL], {"cookies": [COOKIE]}),
                (["close-tab", "55"], {}),
                (["focus", "44"], {}),
            ])
            auth = VimbrowserAuthenticator(runner=runner, profile_loader=lambda _: {"id": 42}, tab_ledger=ledger)
            auth.acquire(interactive=True)
            self.assertEqual(runner.responses, [])
            remaining = sorted(entry["id"] for entry in json.loads(ledger.read_text())["tabs"])
            self.assertEqual(remaining, [12, 13])

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
            (["open-context", "quercus-helper", CANVAS_URL], tabs(
                {"id": 44, "url": "https://example.com/", "active": True},
                {"id": 55, "url": CANVAS_URL, "active": False, "context": "quercus-helper"},
            )),
            (["focus", "55"], {}),
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
            (["open-context", "quercus-helper", CANVAS_URL], tabs(
                {"id": 44, "url": "https://example.com/", "active": True},
                {"id": 55, "url": CANVAS_URL, "active": False, "context": "quercus-helper"},
            )),
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

    def test_open_context_response_must_confirm_one_new_exact_https_context_tab(self):
        cases = [
            tabs({"id": 44, "url": CANVAS_URL, "context": "quercus-helper"}),
            tabs({"id": 55, "url": CANVAS_URL, "context": "other"}),
            tabs({"id": 55, "url": "http://q.utoronto.ca/", "context": "quercus-helper"}),
            tabs(
                {"id": 55, "url": CANVAS_URL, "context": "quercus-helper"},
                {"id": 56, "url": CANVAS_URL, "context": "quercus-helper"},
            ),
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
