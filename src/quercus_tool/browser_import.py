from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .client import CanvasClient
from .errors import (
    BrowserImportError,
    NetworkError,
    SessionRejectedError,
    SessionRequiredError,
)
from .paths import vimbrowser_cli, vimbrowser_context
from .session import CANVAS_BASE_URL, CANVAS_HOST, MAX_COOKIES, CanvasSession

CHROMIUM_EPOCH_OFFSET_SECONDS = 11_644_473_600
CANVAS_URL = CANVAS_BASE_URL + "/"
MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
MAX_AUTH_SECONDS = 600.0
MAX_AUTH_POLLS = 600
CONTEXT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")


@dataclass(frozen=True)
class BrowserTab:
    id: int
    url: str
    active: bool
    context: str | None = None


class VimbrowserAuthenticator:
    """Import and renew Canvas sessions through exact vimbrowser tabs."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        context: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        profile_loader: Callable[[CanvasSession], dict[str, Any]] | None = None,
    ):
        self.executable = executable or vimbrowser_cli()
        self.context = context or vimbrowser_context()
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper
        self.profile_loader = profile_loader or (lambda session: CanvasClient(session).profile())

    def _run(self, *arguments: str, timeout: float = 20) -> str:
        try:
            completed = self.runner(
                [self.executable, *arguments],
                text=True,
                capture_output=True,
                timeout=min(max(timeout, 0.1), 30.0),
                check=False,
            )
        except FileNotFoundError:
            raise BrowserImportError("vimbrowser-cli is not installed or discoverable") from None
        except subprocess.TimeoutExpired:
            raise BrowserImportError(f"vimbrowser command timed out: {arguments[0]}") from None
        if completed.returncode != 0:
            # Cookie output and command diagnostics can contain credentials. Never
            # include stdout or stderr in an exception, even for non-cookie calls.
            raise BrowserImportError(
                f"vimbrowser command failed: {arguments[0]} (exit {completed.returncode})"
            )
        if len(completed.stdout) > MAX_COMMAND_OUTPUT:
            raise BrowserImportError(f"vimbrowser returned too much data for {arguments[0]}")
        return completed.stdout

    @staticmethod
    def _json(raw: str, operation: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise BrowserImportError(f"vimbrowser returned invalid JSON for {operation}") from None
        if not isinstance(value, dict):
            raise BrowserImportError(f"vimbrowser returned an invalid result for {operation}")
        return value

    @staticmethod
    def _tab_id(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

    @staticmethod
    def _is_canvas_url(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "https"
            and parsed.hostname == CANVAS_HOST
            and port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )

    def _tabs_payload(self) -> dict[str, Any]:
        payload = self._json(self._run("tabs", "--json"), "tabs")
        if not isinstance(payload.get("tabs"), list):
            raise BrowserImportError("vimbrowser did not return its tab list")
        return payload

    def tabs(self) -> tuple[int | None, list[BrowserTab]]:
        payload = self._tabs_payload()
        active_id = self._tab_id(payload.get("active_tabid"))
        result: list[BrowserTab] = []
        for row in payload["tabs"]:
            if not isinstance(row, dict) or not self._is_canvas_url(row.get("url")):
                continue
            identifier = self._tab_id(row.get("id"))
            if identifier is None:
                continue
            context = row.get("context")
            result.append(
                BrowserTab(
                    id=identifier,
                    url=str(row["url"]),
                    active=identifier == active_id or bool(row.get("active")),
                    context=context if isinstance(context, str) else None,
                )
            )
        return active_id, result

    def choose_tab(self, requested: int | None) -> BrowserTab:
        _, tabs = self.tabs()
        if requested is not None:
            matches = [tab for tab in tabs if tab.id == requested]
            if len(matches) != 1:
                raise BrowserImportError(
                    f"tab {requested} is not a Quercus tab; choose one shown by `vimbrowser-cli tabs`"
                )
            return matches[0]
        active = [tab for tab in tabs if tab.active]
        if len(active) == 1:
            return active[0]
        if len(tabs) == 1:
            return tabs[0]
        ids = ", ".join(str(tab.id) for tab in tabs) or "none"
        raise BrowserImportError(
            f"multiple or no Quercus tabs are available ({ids}); pass `--tab TAB_ID` explicitly"
        )

    @staticmethod
    def _expiry(row: dict[str, Any]) -> float:
        if row.get("has_expires") is False:
            return -1
        value = row.get("expires", -1)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return -1
        if value > 10**13:
            return float(value) / 1_000_000 - CHROMIUM_EPOCH_OFFSET_SECONDS
        return float(value)

    @classmethod
    def _cookie(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": row.get("name"),
            "value": row.get("value"),
            "domain": row.get("domain"),
            "path": row.get("path", "/"),
            "expires": cls._expiry(row),
            "httpOnly": bool(row.get("httponly", row.get("httpOnly", False))),
            "secure": bool(row.get("secure", False)),
            "sameSite": row.get("same_site", row.get("sameSite", "Unspecified")),
        }

    def _read_session(
        self,
        tab_id: int,
        *,
        source: str,
        renewal_mode: str,
        tolerate_cookie_command_failure: bool = False,
    ) -> CanvasSession | None:
        try:
            raw = self._run("cookies", str(tab_id), CANVAS_URL)
        except BrowserImportError:
            # open-context can return the newly allocated exact tab before its
            # browser backend is ready to service cookie-manager commands. This
            # is a normal bounded startup race during acquisition, but an
            # explicit one-shot import must continue to fail immediately.
            if tolerate_cookie_command_failure:
                return None
            raise
        payload = self._json(raw, "cookies")
        rows = payload.get("cookies")
        if not isinstance(rows, list):
            raise BrowserImportError("vimbrowser did not return its cookie list")
        if not rows:
            return None
        if len(rows) > MAX_COOKIES:
            raise BrowserImportError("vimbrowser returned too many Quercus cookies")
        cookies = [self._cookie(row) for row in rows if isinstance(row, dict)]
        try:
            temporary = CanvasSession.from_browser(
                cookies,
                {"id": 1, "name": "unvalidated import"},
                source="vimbrowser-unvalidated",
                renewal_mode="none",
            )
            profile = self.profile_loader(temporary)
            return CanvasSession.from_browser(
                cookies,
                profile,
                source=source,
                renewal_mode=renewal_mode,
            )
        except (NetworkError, SessionRejectedError):
            # Authentication redirects, an incomplete cookie jar, and transient
            # Canvas failures are normal while the interactive tab is signing in.
            return None

    def import_session(self, *, tab_id: int | None = None) -> CanvasSession:
        """Compatibility import from one existing, unambiguous Quercus tab."""
        tab = self.choose_tab(tab_id)
        session = self._read_session(
            tab.id,
            source=f"vimbrowser-tab:{tab.id}",
            renewal_mode="none",
        )
        if session is None:
            raise BrowserImportError("the selected Quercus tab has no valid importable session")
        return session

    def _open_context_tab(self) -> tuple[int | None, int]:
        if CONTEXT_PATTERN.fullmatch(self.context) is None:
            raise BrowserImportError(
                "the configured vimbrowser context must be 1-48 lowercase letters, numbers, '_' or '-'"
            )
        before = self._tabs_payload()
        existing_ids = {
            identifier
            for row in before["tabs"]
            if isinstance(row, dict)
            for identifier in [self._tab_id(row.get("id"))]
            if identifier is not None
        }
        active_candidate = self._tab_id(before.get("active_tabid"))
        original_active = active_candidate if active_candidate in existing_ids else None
        opened = self._json(
            self._run("open-context", self.context, CANVAS_URL),
            "open-context",
        )
        tab_id = self._tab_id(opened.get("active_tabid"))
        if (
            tab_id is None
            or tab_id in existing_ids
            or opened.get("context") != self.context
            or not self._is_canvas_url(opened.get("url"))
        ):
            # Do not act on an unverified ID: closing it could destroy a user tab.
            raise BrowserImportError(
                "vimbrowser did not confirm the exact newly opened Quercus context tab"
            )
        return original_active, tab_id

    def _restore_tabs(self, opened_tab: int, original_active: int | None) -> None:
        try:
            self._run("close-tab", str(opened_tab))
        except BrowserImportError:
            pass
        if original_active is not None and original_active != opened_tab:
            try:
                self._run("focus", str(original_active))
            except BrowserImportError:
                pass

    def _acquire(
        self,
        *,
        interactive: bool,
        expected_user_id: int | None,
        timeout_seconds: float | None,
    ) -> CanvasSession:
        original_active, opened_tab = self._open_context_tab()
        default_timeout = MAX_AUTH_SECONDS if interactive else 75.0
        requested_timeout = default_timeout if timeout_seconds is None else float(timeout_seconds)
        timeout = min(max(requested_timeout, 0.0), MAX_AUTH_SECONDS)
        deadline = self.clock() + timeout
        try:
            for _ in range(MAX_AUTH_POLLS):
                session = self._read_session(
                    opened_tab,
                    source=f"vimbrowser-context:{self.context}",
                    renewal_mode="vimbrowser",
                    tolerate_cookie_command_failure=True,
                )
                if session is not None:
                    if expected_user_id is not None and session.user["id"] != expected_user_id:
                        raise SessionRejectedError(
                            "the Quercus vimbrowser context is signed into a different account"
                        )
                    return session
                if self.clock() >= deadline:
                    break
                self.sleeper(1.0)
        finally:
            self._restore_tabs(opened_tab, original_active)
        if interactive:
            raise SessionRequiredError(
                "persistent login did not finish; rerun `quercus login --persistent` and complete U of T sign-in and Duo"
            )
        raise SessionRequiredError(
            "the vimbrowser Quercus context needs human authentication; run `quercus login --persistent`"
        )

    def acquire(
        self,
        *,
        interactive: bool,
        expected_user_id: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CanvasSession:
        """Open one transient named-context tab and import a renewable session."""
        try:
            return self._acquire(
                interactive=interactive,
                expected_user_id=expected_user_id,
                timeout_seconds=timeout_seconds,
            )
        except BrowserImportError:
            if interactive:
                raise
            raise SessionRequiredError(
                "automatic renewal could not use vimbrowser; run `quercus login --persistent`"
            ) from None


# Keep the old import name for callers of the short-lived compatibility flow.
VimbrowserImporter = VimbrowserAuthenticator
