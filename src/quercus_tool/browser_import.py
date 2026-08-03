from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .client import CanvasClient
from .errors import BrowserImportError
from .paths import vimbrowser_cli
from .session import CANVAS_HOST, CanvasSession

CHROMIUM_EPOCH_OFFSET_SECONDS = 11_644_473_600


@dataclass(frozen=True)
class BrowserTab:
    id: int
    url: str
    active: bool


class VimbrowserImporter:
    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.executable = executable or vimbrowser_cli()
        self.runner = runner

    def _run(self, *arguments: str, timeout: float = 20) -> str:
        try:
            completed = self.runner(
                [self.executable, *arguments],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            raise BrowserImportError("vimbrowser-cli is not installed or discoverable") from None
        except subprocess.TimeoutExpired:
            raise BrowserImportError(f"vimbrowser command timed out: {arguments[0]}") from None
        if completed.returncode != 0:
            # Cookie output contains credentials and must never be echoed.
            raise BrowserImportError(
                f"vimbrowser command failed: {arguments[0]} (exit {completed.returncode})"
            )
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

    def tabs(self) -> tuple[int | None, list[BrowserTab]]:
        payload = self._json(self._run("tabs", "--json"), "tabs")
        rows = payload.get("tabs")
        if not isinstance(rows, list):
            raise BrowserImportError("vimbrowser did not return its tab list")
        active_id = payload.get("active_tabid")
        result: list[BrowserTab] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "")
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if parsed.scheme == "https" and parsed.hostname == CANVAS_HOST:
                identifier = row.get("id")
                if isinstance(identifier, int):
                    result.append(
                        BrowserTab(
                            id=identifier,
                            url=url,
                            active=identifier == active_id or bool(row.get("active")),
                        )
                    )
        return int(active_id) if isinstance(active_id, int) else None, result

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

    def import_session(self, *, tab_id: int | None = None) -> CanvasSession:
        tab = self.choose_tab(tab_id)
        payload = self._json(
            self._run("cookies", str(tab.id), "https://q.utoronto.ca/"),
            "cookies",
        )
        rows = payload.get("cookies")
        if not isinstance(rows, list) or not rows:
            raise BrowserImportError("the selected Quercus tab has no importable session cookies")
        cookies = [self._cookie(row) for row in rows if isinstance(row, dict)]
        temporary = CanvasSession.from_browser(
            cookies,
            {"id": 1, "name": "unvalidated import"},
            source=f"vimbrowser-tab:{tab.id}",
            renewal_mode="none",
        )
        client = CanvasClient(temporary)
        profile = client.profile()
        return CanvasSession.from_browser(
            cookies,
            profile,
            source=f"vimbrowser-tab:{tab.id}",
            renewal_mode="none",
        )
