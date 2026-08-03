from __future__ import annotations

import unittest
from datetime import UTC, datetime

from quercus_tool.canvas import Quercus, parse_duration
from quercus_tool.errors import UsageError


class FakeClient:
    def __init__(self):
        self.calls = []

    def collect(self, url, *, params=None, limit):
        self.calls.append((url, params, limit))
        if url == "/api/v1/courses":
            state = dict(params)["enrollment_state"]
            if state == "active":
                return [
                    {"id": 10, "name": "PUMP II", "course_code": "PUMP", "workflow_state": "available", "enrollments": [{"enrollment_state": "active"}]},
                    {"id": 11, "name": "PUMP Tutorial", "course_code": "PUMPT", "workflow_state": "available"},
                ][:limit]
            return []
        if url.endswith("/enrollments"):
            return [{
                "id": 9, "type": "StudentEnrollment", "enrollment_state": "active",
                "grades": {
                    "current_grade": "A", "current_score": 94, "current_points": 47,
                    "final_grade": "A-", "final_score": 90,
                    "unposted_current_score": 100, "unposted_final_grade": "A+",
                },
            }]
        if url.endswith("/assignments"):
            return [{
                "id": 5, "name": "PS1", "due_at": "2026-08-01T00:00:00Z", "points_possible": 10,
                "published": True,
                "submission": {
                    "id": 6, "workflow_state": "graded", "score": 9, "grade": "9",
                    "unposted_grade": "10", "late": False,
                },
            }]
        if url.endswith("/files"):
            return [
                {
                    "id": 1,
                    "display_name": "new.pdf",
                    "updated_at": "2026-08-02T00:00:00Z",
                    "url": "https://q.utoronto.ca/files/1/download?verifier=private",
                },
                {"id": 2, "display_name": "old.pdf", "updated_at": "2026-07-01T00:00:00Z"},
            ]
        return []

    def get_json(self, url, *, params=None):
        self.calls.append((url, params, None))
        if url == "/api/v1/courses/10":
            return {"id": 10, "name": "PUMP II", "course_code": "PUMP", "workflow_state": "available"}
        raise AssertionError(url)


class CanvasTests(unittest.TestCase):
    def test_duration_is_exact_and_bounded(self):
        now = datetime(2026, 8, 3, tzinfo=UTC)
        self.assertEqual(parse_duration("2d", now=now), datetime(2026, 8, 1, tzinfo=UTC))
        for invalid in ("", "0d", "2 days", "-1h", "100001w"):
            with self.subTest(invalid=invalid), self.assertRaises(UsageError):
                parse_duration(invalid, now=now)

    def test_numeric_course_resolution_is_direct(self):
        client = FakeClient()
        result = Quercus(client).resolve_course("10")
        self.assertEqual(result["courseCode"], "PUMP")
        self.assertEqual(client.calls[0][0], "/api/v1/courses/10")

    def test_unique_and_ambiguous_course_aliases(self):
        quercus = Quercus(FakeClient())
        self.assertEqual(quercus.resolve_course("PUMPT")["id"], 11)
        with self.assertRaises(UsageError) as caught:
            quercus.resolve_course("pum")
        self.assertEqual(len(caught.exception.details["candidates"]), 2)

    def test_file_since_filter_requires_valid_recent_timestamp(self):
        cutoff = datetime(2026, 8, 1, tzinfo=UTC)
        rows = Quercus(FakeClient()).files(10, limit=10, since=cutoff)
        self.assertEqual([row["id"] for row in rows], [1])
        self.assertNotIn("verifier", repr(rows))
        self.assertNotIn("downloadUrl", rows[0])

    def test_grades_never_project_unposted_fields(self):
        result = Quercus(FakeClient()).grades(10, limit=10)
        serialized = repr(result).casefold()
        self.assertNotIn("unposted", serialized)
        self.assertEqual(result["enrollments"][0]["currentScore"], 94)
        self.assertEqual(result["assignments"][0]["submission"]["score"], 9)


if __name__ == "__main__":
    unittest.main()
