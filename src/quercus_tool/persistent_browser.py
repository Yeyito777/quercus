from __future__ import annotations

import json
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import SessionRejectedError, SessionRequiredError, UnsafeFileError
from .paths import browser_profile_path
from .session import CANVAS_BASE_URL, CANVAS_HOST, CanvasSession, normalize_profile
from .storage import ensure_private_directory

PROFILE_URL = f"{CANVAS_BASE_URL}/api/v1/users/self/profile"
MAX_PROFILE_BYTES = 1024 * 1024
PAGE_PROFILE_JS = """
() => {
  const environment = window.ENV || {};
  const user = environment.current_user || {};
  return {
    id: environment.current_user_id ?? user.id ?? null,
    name: user.display_name ?? user.name ?? environment.current_user_display_name ?? null,
    short_name: user.short_name ?? null,
    sortable_name: user.sortable_name ?? null,
    login_id: user.login_id ?? null,
    primary_email: user.email ?? null,
    time_zone: environment.TIMEZONE ?? environment.timezone ?? null,
    locale: environment.LOCALE ?? environment.locale ?? null
  };
}
""".strip()


class PersistentBrowserAuthenticator:
    def __init__(
        self,
        *,
        profile: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.profile = (profile or browser_profile_path()).expanduser()
        self.clock = clock
        self.sleeper = sleeper

    def _prepare_profile(self) -> None:
        ensure_private_directory(self.profile)
        self.profile.chmod(0o700)

    @staticmethod
    def _playwright():
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SessionRequiredError(
                "persistent Quercus authentication support is not installed; run `uv sync` in the Quercus helper"
            ) from None
        return sync_playwright, PlaywrightError

    @staticmethod
    def _profile_from_response(response: Any) -> dict[str, Any] | None:
        if response.status != 200:
            return None
        try:
            body = response.body()
            if len(body) > MAX_PROFILE_BYTES:
                return None
            payload = json.loads(body)
            return normalize_profile(payload)
        except (json.JSONDecodeError, UnicodeError, SessionRejectedError, TypeError, ValueError):
            # This is a polling probe. Authentication pages and transient Canvas
            # responses are expected until sign-in finishes.
            return None

    @staticmethod
    def _profile_from_page(page: Any) -> dict[str, Any] | None:
        try:
            raw = page.evaluate(PAGE_PROFILE_JS)
            if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"].isdigit():
                raw["id"] = int(raw["id"])
            return normalize_profile(raw)
        except (SessionRejectedError, TypeError, ValueError):
            return None

    def acquire(
        self,
        *,
        interactive: bool,
        expected_user_id: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CanvasSession:
        if not interactive and not self.profile.exists():
            raise SessionRequiredError("the helper-owned Quercus browser session has not been initialized")
        self._prepare_profile()
        sync_playwright, playwright_error = self._playwright()
        timeout = timeout_seconds if timeout_seconds is not None else (600.0 if interactive else 75.0)
        deadline = self.clock() + timeout
        last_url = ""
        try:
            with sync_playwright() as engine:
                context = engine.chromium.launch_persistent_context(
                    str(self.profile),
                    headless=not interactive,
                    viewport={"width": 1280, "height": 900},
                )
                try:
                    pages = context.pages
                    page = pages[0] if pages else context.new_page()
                    try:
                        page.goto(CANVAS_BASE_URL + "/", wait_until="domcontentloaded", timeout=30_000)
                    except playwright_error:
                        # U of T auth redirects and Duo may outlive DOMContentLoaded.
                        pass
                    while self.clock() < deadline:
                        live_pages = list(context.pages)
                        if live_pages:
                            last_url = live_pages[-1].url or last_url
                        profile = None
                        for candidate in live_pages:
                            try:
                                host = urlsplit(candidate.url).hostname
                            except ValueError:
                                continue
                            if host != CANVAS_HOST:
                                continue
                            try:
                                profile = self._profile_from_page(candidate)
                            except playwright_error:
                                profile = None
                            if profile is not None:
                                break
                        response = None
                        if profile is None:
                            try:
                                response = context.request.get(
                                    PROFILE_URL,
                                    timeout=15_000,
                                    max_redirects=0,
                                    fail_on_status_code=False,
                                )
                                profile = self._profile_from_response(response)
                            except playwright_error:
                                profile = None
                            finally:
                                if response is not None:
                                    try:
                                        response.dispose()
                                    except playwright_error:
                                        pass
                        if profile is not None:
                            if expected_user_id is not None and profile["id"] != expected_user_id:
                                raise SessionRejectedError(
                                    "the helper-owned Quercus browser is signed into a different account"
                                )
                            cookies = context.cookies([CANVAS_BASE_URL + "/"])
                            return CanvasSession.from_browser(
                                cookies,
                                profile,
                                source="persistent-browser",
                                renewal_mode="persistent-browser",
                            )
                        self.sleeper(0.5)
                finally:
                    context.close()
        except (SessionRequiredError, SessionRejectedError):
            raise
        except playwright_error:
            raise SessionRequiredError(
                "the helper-owned Quercus browser could not start; rerun `quercus login --persistent` interactively"
            ) from None
        try:
            host = urlsplit(last_url).hostname or ""
        except ValueError:
            host = ""
        location = "U of T sign-in/Duo" if host != CANVAS_HOST else "Quercus"
        if interactive:
            raise SessionRequiredError(
                f"persistent login did not finish at {location}; rerun `quercus login --persistent` and complete sign-in"
            )
        raise SessionRequiredError(
            "the helper-owned Quercus session needs human reauthentication; run `quercus login --persistent`"
        )


def delete_browser_profile(*, profile: Path | None = None) -> bool:
    target = (profile or browser_profile_path()).expanduser()
    try:
        info = target.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafeFileError("refused to delete a non-directory Quercus browser profile")
    shutil.rmtree(target)
    return True
