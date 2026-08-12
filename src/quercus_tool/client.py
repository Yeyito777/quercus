from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import requests

from .errors import NetworkError, SessionRejectedError
from .session import CANVAS_BASE_URL, CANVAS_HOST, CanvasSession

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PAGES = 10
MAX_ITEMS = 500
MAX_REQUESTS = 50
RETRY_STATUSES = {429, 502, 503, 504}

_API_PATHS = (
    re.compile(r"^/api/v1/users/self/profile$"),
    re.compile(r"^/api/v1/courses$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/assignments$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/modules$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/modules/[1-9][0-9]*/items$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/files$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/files/[1-9][0-9]*$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/enrollments$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/pages$"),
    re.compile(r"^/api/v1/courses/[1-9][0-9]*/pages/[^/]+$"),
    re.compile(r"^/api/v1/announcements$"),
)
_DOWNLOAD_PATHS = (
    re.compile(r"^/files/[1-9][0-9]*/download$"),
    re.compile(r"^/courses/[1-9][0-9]*/files/[1-9][0-9]*/download$"),
)
_DOWNLOAD_HOST_SUFFIXES = (
    ".canvas-user-content.com",
    ".inscloudgate.net",
    ".instructure.com",
    ".instructuremedia.com",
    ".amazonaws.com",
    ".cloudfront.net",
)
_LINK_RE = re.compile(r'<([^>]*)>\s*;\s*rel="?([^";,]+)"?', re.IGNORECASE)


@dataclass(frozen=True)
class BinaryResponse:
    content: bytes
    content_type: str | None
    content_disposition: str | None
    final_url: str


class CanvasClient:
    def __init__(
        self,
        canvas_session: CanvasSession,
        *,
        transport: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.canvas_session = canvas_session
        self.transport = transport or requests.Session()
        self.sleeper = sleeper
        self.request_count = 0
        if transport is None:
            self._install_cookies()

    def _install_cookies(self) -> None:
        now = time.time()
        for cookie in self.canvas_session.cookies:
            expires = float(cookie.get("expires", -1))
            if expires > 0 and expires <= now:
                continue
            self.transport.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
                secure=bool(cookie.get("secure")),
                expires=int(expires) if expires > 0 else None,
            )

    @staticmethod
    def _base_headers(*, json_response: bool = True) -> dict[str, str]:
        return {
            "Accept": "application/json" if json_response else "*/*",
            "User-Agent": "quercus-readonly-helper/0.2",
        }

    @staticmethod
    def _parse_url(value: str) -> tuple[str, str]:
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise NetworkError("Quercus returned an invalid URL") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != CANVAS_HOST
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise NetworkError("refused a Quercus URL outside the allowed origin")
        try:
            decoded_path = unquote(parsed.path, errors="strict")
        except (UnicodeError, ValueError):
            raise NetworkError("Quercus returned an invalid URL path") from None
        if "\x00" in decoded_path or "/../" in decoded_path or decoded_path.endswith("/.."):
            raise NetworkError("refused a Quercus URL with an unsafe path")
        return value, decoded_path

    @classmethod
    def validate_api_url(cls, value: str) -> str:
        if value.startswith("/api/"):
            value = CANVAS_BASE_URL + value
        value, decoded_path = cls._parse_url(value)
        if not any(pattern.fullmatch(decoded_path) for pattern in _API_PATHS):
            raise NetworkError("refused a Quercus URL outside the allowlisted read-only API")
        return value

    @classmethod
    def validate_canvas_download_url(cls, value: str) -> str:
        if value.startswith("/"):
            value = CANVAS_BASE_URL + value
        value, decoded_path = cls._parse_url(value)
        if not any(pattern.fullmatch(decoded_path) for pattern in _DOWNLOAD_PATHS):
            raise NetworkError("refused a URL outside the allowlisted Quercus download routes")
        return value

    @staticmethod
    def validate_external_download_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise NetworkError("Quercus returned an invalid file-storage redirect") from None
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or not any(host.endswith(suffix) for suffix in _DOWNLOAD_HOST_SUFFIXES)
        ):
            raise NetworkError("refused a Quercus download redirect outside approved storage hosts")
        return value

    def _consume_budget(self) -> None:
        self.request_count += 1
        if self.request_count > MAX_REQUESTS:
            raise NetworkError("Quercus request count exceeded the safety limit")

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                delay = float(2**attempt)
        return min(max(delay, 0.0), 10.0)

    @staticmethod
    def _read_bounded(response: requests.Response, maximum: int) -> bytes:
        content_length = response.headers.get("Content-Length")
        try:
            if content_length is not None and int(content_length) > maximum:
                raise NetworkError("Quercus response exceeded the configured safety limit")
        except ValueError:
            pass
        data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > maximum:
                raise NetworkError("Quercus response exceeded the configured safety limit")
        return bytes(data)

    @staticmethod
    def _error_detail(data: bytes) -> str:
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeError):
            return ""
        values: list[str] = []
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str):
                values.append(message)
            errors = payload.get("errors")
            if isinstance(errors, list):
                for error in errors[:3]:
                    if isinstance(error, dict) and isinstance(error.get("message"), str):
                        values.append(error["message"])
                    elif isinstance(error, str):
                        values.append(error)
        return "; ".join(values)[:300]

    def _request_api(
        self,
        url: str,
        *,
        params: list[tuple[str, Any]] | dict[str, Any] | None = None,
    ) -> tuple[requests.Response, bytes]:
        url = self.validate_api_url(url)
        for attempt in range(3):
            self._consume_budget()
            try:
                response = self.transport.get(
                    url,
                    params=params,
                    headers=self._base_headers(),
                    timeout=(10, 30),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                if attempt < 2:
                    self.sleeper(float(2**attempt))
                    continue
                raise NetworkError("could not reach Quercus") from None
            if 300 <= response.status_code < 400:
                response.close()
                raise SessionRejectedError(
                    "the Quercus session redirected to sign-in; automatic renewal could not use it"
                )
            retryable = response.status_code in RETRY_STATUSES or (
                response.status_code == 403 and "Retry-After" in response.headers
            )
            if retryable and attempt < 2:
                delay = self._retry_delay(response, attempt)
                response.close()
                self.sleeper(delay)
                continue
            try:
                data = self._read_bounded(response, MAX_JSON_BYTES)
            finally:
                response.close()
            if response.status_code == 401:
                raise SessionRejectedError("the saved Quercus session was rejected")
            if response.status_code >= 400:
                detail = self._error_detail(data)
                raise NetworkError(
                    f"Quercus returned HTTP {response.status_code}" + (f": {detail}" if detail else ""),
                    status_code=response.status_code,
                    response_detail=detail,
                )
            return response, data
        raise NetworkError("Quercus request exhausted its retry budget")

    def get_json(
        self,
        url: str,
        *,
        params: list[tuple[str, Any]] | dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        _, data = self._request_api(url, params=params)
        try:
            value = json.loads(data)
        except (json.JSONDecodeError, UnicodeError):
            raise NetworkError("Quercus returned an unexpected non-JSON response") from None
        if not isinstance(value, (dict, list)):
            raise NetworkError("Quercus returned an unexpected JSON response")
        return value

    @staticmethod
    def next_link(raw: str | None) -> str | None:
        if not raw:
            return None
        for url, relation in _LINK_RE.findall(raw):
            if relation.casefold() == "next":
                return url
        return None

    def collect(
        self,
        url: str,
        *,
        params: list[tuple[str, Any]] | dict[str, Any] | None = None,
        limit: int,
        max_pages: int = MAX_PAGES,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_ITEMS:
            raise NetworkError(f"item limit must be between 1 and {MAX_ITEMS}")
        if not 1 <= max_pages <= MAX_PAGES:
            raise NetworkError(f"page limit must be between 1 and {MAX_PAGES}")
        rows: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params = params
        pages = 0
        while next_url and len(rows) < limit:
            pages += 1
            if pages > max_pages:
                raise NetworkError("Quercus pagination exceeded the safety limit")
            response, data = self._request_api(next_url, params=next_params)
            next_params = None
            try:
                payload = json.loads(data)
            except (json.JSONDecodeError, UnicodeError):
                raise NetworkError("Quercus returned an unexpected non-JSON collection") from None
            if not isinstance(payload, list):
                raise NetworkError("Quercus returned an invalid result collection")
            rows.extend(value for value in payload if isinstance(value, dict))
            candidate = self.next_link(response.headers.get("Link"))
            next_url = self.validate_api_url(candidate) if candidate else None
        return rows[:limit]

    def profile(self) -> dict[str, Any]:
        value = self.get_json("/api/v1/users/self/profile")
        if not isinstance(value, dict):
            raise NetworkError("Quercus returned an invalid account profile")
        return value

    def get_bytes(self, url: str, *, max_bytes: int = MAX_FILE_BYTES) -> BinaryResponse:
        current = self.validate_canvas_download_url(url)
        external = False
        for _ in range(4):
            self._consume_budget()
            try:
                response = self.transport.get(
                    current,
                    headers=self._base_headers(json_response=False),
                    timeout=(10, 60),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                raise NetworkError("could not download the selected Quercus file") from None
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise NetworkError("Quercus returned a download redirect without a destination")
                candidate = urljoin(current, location)
                try:
                    parsed = urlsplit(candidate)
                except ValueError:
                    raise NetworkError("Quercus returned an invalid download redirect") from None
                if parsed.hostname == CANVAS_HOST:
                    # A redirect to /login is a rejected session, not file content.
                    try:
                        current = self.validate_canvas_download_url(candidate)
                    except NetworkError:
                        raise SessionRejectedError("the Quercus file download redirected to sign-in") from None
                else:
                    current = self.validate_external_download_url(candidate)
                    external = True
                continue
            try:
                data = self._read_bounded(response, max_bytes)
            finally:
                response.close()
            if response.status_code in (401, 403) and not external:
                raise SessionRejectedError("the Quercus session could not access that file")
            if response.status_code >= 400:
                raise NetworkError(f"Quercus file storage returned HTTP {response.status_code}")
            return BinaryResponse(
                content=data,
                content_type=response.headers.get("Content-Type"),
                content_disposition=response.headers.get("Content-Disposition"),
                final_url=current,
            )
        raise NetworkError("Quercus file download exceeded the redirect safety limit")
