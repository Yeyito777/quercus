from __future__ import annotations

import json

import requests

from quercus_tool.session import CanvasSession


def session(*, renewal_mode: str = "none", user_id: int = 42) -> CanvasSession:
    return CanvasSession.from_browser(
        [{
            "name": "test_session",
            "value": "example-cookie-value",
            "domain": "q.utoronto.ca",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }],
        {
            "id": user_id,
            "name": "Test Student",
            "short_name": "Test",
            "login_id": "student",
            "primary_email": "student@example.edu",
        },
        source="tests",
        renewal_mode=renewal_mode,
    )


class FakeResponse:
    def __init__(self, status: int, payload=b"", *, headers=None):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers or {"Content-Type": "application/json"})
        self._content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield self._content

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("fake transport exhausted")
        return self.responses.pop(0)
