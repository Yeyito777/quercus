from __future__ import annotations

import unittest

from helpers import FakeResponse, FakeTransport, session

from quercus_tool.client import CanvasClient
from quercus_tool.errors import NetworkError, SessionRejectedError


class ClientTests(unittest.TestCase):
    def test_api_get_is_cookie_authenticated_and_redirects_disabled(self):
        transport = FakeTransport([FakeResponse(200, {"id": 42})])
        client = CanvasClient(session(), transport=transport)
        self.assertEqual(client.profile()["id"], 42)
        url, kwargs = transport.calls[0]
        self.assertEqual(url, "https://q.utoronto.ca/api/v1/users/self/profile")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")

    def test_mutating_or_outside_routes_are_rejected_before_network(self):
        client = CanvasClient(session(), transport=FakeTransport([]))
        for url in (
            "https://evil.example/api/v1/courses",
            "/api/v1/courses/1/submissions",
            "/api/v1/courses/1/assignments/2",
            "/api/v1/courses/1/pages/../assignments",
        ):
            with self.subTest(url=url), self.assertRaises(NetworkError):
                client.get_json(url)

    def test_pagination_follows_only_revalidated_next_link(self):
        transport = FakeTransport([
            FakeResponse(200, [{"id": 1}], headers={
                "Link": '<https://q.utoronto.ca/api/v1/courses?page=2>; rel="next"'
            }),
            FakeResponse(200, [{"id": 2}]),
        ])
        rows = CanvasClient(session(), transport=transport).collect("/api/v1/courses", limit=2)
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertIsNone(transport.calls[1][1]["params"])

    def test_evil_pagination_link_is_rejected(self):
        transport = FakeTransport([FakeResponse(200, [], headers={
            "Link": '<https://evil.example/api/v1/courses?page=2>; rel="next"'
        })])
        with self.assertRaises(NetworkError):
            CanvasClient(session(), transport=transport).collect("/api/v1/courses", limit=2)

    def test_api_redirect_is_session_rejection(self):
        transport = FakeTransport([FakeResponse(302, b"", headers={"Location": "/login"})])
        with self.assertRaises(SessionRejectedError):
            CanvasClient(session(), transport=transport).profile()

    def test_retry_is_bounded_and_honors_capped_delay(self):
        delays = []
        transport = FakeTransport([
            FakeResponse(503, {}, headers={"Retry-After": "100"}),
            FakeResponse(200, {"id": 42}),
        ])
        result = CanvasClient(session(), transport=transport, sleeper=delays.append).profile()
        self.assertEqual(result["id"], 42)
        self.assertEqual(delays, [10.0])

    def test_download_follows_only_approved_storage_without_redirect_magic(self):
        transport = FakeTransport([
            FakeResponse(302, b"", headers={"Location": "https://bucket.s3.amazonaws.com/signed/file"}),
            FakeResponse(200, b"pdf", headers={"Content-Type": "application/pdf"}),
        ])
        result = CanvasClient(session(), transport=transport).get_bytes("/files/12/download")
        self.assertEqual(result.content, b"pdf")
        self.assertEqual(transport.calls[1][0], "https://bucket.s3.amazonaws.com/signed/file")
        self.assertFalse(transport.calls[1][1]["allow_redirects"])

    def test_uoft_canvas_user_content_storage_is_approved(self):
        value = CanvasClient.validate_external_download_url(
            "https://a12345-67890.cluster75.canvas-user-content.com/courses/file.pdf?sig=example"
        )
        self.assertIn("canvas-user-content.com", value)

    def test_instructure_cloud_gate_storage_is_approved(self):
        value = CanvasClient.validate_external_download_url(
            "https://cdn.inst-fs-yul-prod.inscloudgate.net/file.pdf?sig=example"
        )
        self.assertIn("inscloudgate.net", value)

    def test_download_rejects_unapproved_redirect(self):
        transport = FakeTransport([
            FakeResponse(302, b"", headers={"Location": "https://evil.example/file"}),
        ])
        with self.assertRaises(NetworkError):
            CanvasClient(session(), transport=transport).get_bytes("/files/12/download")

    def test_oversized_content_length_is_rejected(self):
        transport = FakeTransport([
            FakeResponse(200, b"x", headers={"Content-Length": str(101 * 1024 * 1024)}),
        ])
        with self.assertRaises(NetworkError):
            CanvasClient(session(), transport=transport).get_bytes("/files/12/download")


if __name__ == "__main__":
    unittest.main()
