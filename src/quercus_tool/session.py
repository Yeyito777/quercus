from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import SessionRejectedError, SessionRequiredError
from .paths import lock_path, session_path
from .storage import atomic_write_json, exclusive_lock, read_private_json

CANVAS_BASE_URL = "https://q.utoronto.ca"
CANVAS_HOST = "q.utoronto.ca"
SESSION_VERSION = 1
RENEWAL_MODES = {"none", "vimbrowser"}
LEGACY_RENEWAL_MODE = "persistent-browser"
MAX_COOKIES = 200


def utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def _string(value: Any, *, field: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SessionRejectedError(f"the saved Quercus {field} is invalid")
    if (not value and not allow_empty) or len(value) > maximum:
        raise SessionRejectedError(f"the saved Quercus {field} is invalid")
    return value


def normalize_profile(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SessionRejectedError("Quercus did not return a valid account profile")
    identifier = raw.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        raise SessionRejectedError("Quercus returned an account profile without a valid user ID")
    result: dict[str, Any] = {"id": identifier}
    mappings = {
        "name": "name",
        "short_name": "shortName",
        "sortable_name": "sortableName",
        "login_id": "loginId",
        "primary_email": "primaryEmail",
        "time_zone": "timeZone",
        "locale": "locale",
    }
    for source, target in mappings.items():
        value = raw.get(source, raw.get(target))
        result[target] = str(value)[:1000] if value is not None else None
    return result


def normalize_cookie(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SessionRejectedError("the saved Quercus browser cookie is invalid")
    name = _string(raw.get("name"), field="cookie name", maximum=4096)
    value = _string(raw.get("value"), field="cookie value", maximum=128 * 1024, allow_empty=True)
    domain = _string(raw.get("domain"), field="cookie domain", maximum=253).lower()
    bare_domain = domain.lstrip(".")
    if CANVAS_HOST != bare_domain and not CANVAS_HOST.endswith("." + bare_domain):
        raise SessionRejectedError("the saved Quercus cookie is outside the allowed U of T domain")
    path = _string(raw.get("path", "/"), field="cookie path", maximum=4096)
    if not path.startswith("/"):
        raise SessionRejectedError("the saved Quercus cookie has an invalid path")
    expires_raw = raw.get("expires", -1)
    if isinstance(expires_raw, bool) or not isinstance(expires_raw, (int, float)):
        raise SessionRejectedError("the saved Quercus cookie has an invalid expiry")
    same_site_raw = str(raw.get("sameSite", raw.get("same_site", "Unspecified")) or "Unspecified")
    same_site = {
        "strict": "Strict",
        "lax": "Lax",
        "none": "None",
        "unspecified": "Unspecified",
    }.get(same_site_raw.casefold())
    if same_site is None:
        raise SessionRejectedError("the saved Quercus cookie has an invalid SameSite value")
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": float(expires_raw),
        "httpOnly": bool(raw.get("httpOnly", raw.get("httponly", False))),
        "secure": bool(raw.get("secure", False)),
        "sameSite": same_site,
    }


@dataclass(frozen=True)
class CanvasSession:
    version: int
    base_url: str
    imported_at: int
    source: str
    renewal_mode: str
    user: dict[str, Any]
    cookies: list[dict[str, Any]]

    @classmethod
    def from_browser(
        cls,
        cookies: list[dict[str, Any]],
        profile: dict[str, Any],
        *,
        source: str,
        renewal_mode: str,
    ) -> CanvasSession:
        if renewal_mode not in RENEWAL_MODES:
            raise SessionRejectedError("the Quercus session has an invalid renewal mode")
        normalized = [normalize_cookie(cookie) for cookie in cookies]
        if not normalized or len(normalized) > MAX_COOKIES:
            raise SessionRejectedError("Quercus did not provide a valid bounded cookie session")
        # Cookie names are unique per domain/path. Last occurrence wins, as in a
        # browser cookie store, without exposing names in public output.
        deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
        for cookie in normalized:
            deduplicated[(cookie["domain"], cookie["path"], cookie["name"])] = cookie
        return cls(
            version=SESSION_VERSION,
            base_url=CANVAS_BASE_URL,
            imported_at=int(time.time()),
            source=_string(source, field="session source", maximum=128),
            renewal_mode=renewal_mode,
            user=normalize_profile(profile),
            cookies=list(deduplicated.values()),
        )

    @classmethod
    def from_dict(cls, raw: Any) -> CanvasSession:
        if not isinstance(raw, dict):
            raise SessionRejectedError("the saved Quercus session has an invalid format")
        try:
            renewal_mode = str(raw["renewal_mode"])
            source = str(raw["source"])
            if renewal_mode == LEGACY_RENEWAL_MODE:
                renewal_mode = "vimbrowser"
                if source == LEGACY_RENEWAL_MODE:
                    source = "vimbrowser"
            session = cls(
                version=int(raw["version"]),
                base_url=str(raw["base_url"]),
                imported_at=int(raw["imported_at"]),
                source=source,
                renewal_mode=renewal_mode,
                user=normalize_profile(raw["user"]),
                cookies=[normalize_cookie(value) for value in raw["cookies"]],
            )
        except (KeyError, TypeError, ValueError):
            raise SessionRejectedError("the saved Quercus session has an invalid format") from None
        if session.version != SESSION_VERSION or session.base_url != CANVAS_BASE_URL:
            raise SessionRejectedError("the saved Quercus session has an unsupported format")
        if session.renewal_mode not in RENEWAL_MODES:
            raise SessionRejectedError("the saved Quercus session has an invalid renewal mode")
        if not session.cookies or len(session.cookies) > MAX_COOKIES:
            raise SessionRejectedError("the saved Quercus session has an invalid cookie set")
        _string(session.source, field="session source", maximum=128)
        return session

    def assert_account(self, raw_profile: Any) -> dict[str, Any]:
        profile = normalize_profile(raw_profile)
        if profile["id"] != self.user["id"]:
            raise SessionRejectedError("the renewed Quercus session belongs to a different account")
        return profile

    def public(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "source": self.source,
            "importedAt": utc_iso(self.imported_at),
            "automaticRenewal": self.renewal_mode == "vimbrowser",
            "renewalMode": self.renewal_mode,
            "cookieCount": len(self.cookies),
            "user": dict(self.user),
        }


def save_session(session: CanvasSession, *, path: Path | None = None, lock: Path | None = None) -> None:
    target = path or session_path()
    lock_target = lock or lock_path()
    with exclusive_lock(lock_target):
        atomic_write_json(target, asdict(session))


def load_session(*, path: Path | None = None) -> CanvasSession:
    target = path or session_path()
    try:
        raw = read_private_json(target)
    except FileNotFoundError:
        raise SessionRequiredError("Quercus is not logged in; run `quercus login --persistent`") from None
    return CanvasSession.from_dict(raw)


def delete_session(*, path: Path | None = None, lock: Path | None = None) -> bool:
    target = path or session_path()
    lock_target = lock or lock_path()
    with exclusive_lock(lock_target):
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return False
