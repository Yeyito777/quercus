from __future__ import annotations

import unittest
from datetime import UTC, datetime

from quercus_tool.canvas import (
    MODULE_DISCOVERY_MAX_METADATA_REQUESTS,
    Quercus,
    parse_duration,
)
from quercus_tool.errors import NetworkError, SessionRejectedError, UsageError


class FakeClient:
    def __init__(self):
        self.calls = []

    def collect(self, url, *, params=None, limit, max_pages=None):
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


class DisabledCollectionClient:
    def __init__(self, *, files_error=None, pages_error=None, modules=None, objects=None):
        self.files_error = files_error
        self.pages_error = pages_error
        self.module_rows = modules or []
        self.objects = objects or {}
        self.calls = []

    def collect(self, url, *, params=None, limit, max_pages=None):
        self.calls.append((url, params, limit, max_pages))
        if url.endswith("/files") and self.files_error:
            raise self.files_error
        if url.endswith("/pages") and self.pages_error:
            raise self.pages_error
        if url.endswith("/modules"):
            return self.module_rows[:limit]
        if "/modules/" in url and url.endswith("/items"):
            module_id = int(url.split("/")[-2])
            return self.objects.get(("module", module_id), [])[:limit]
        return []

    def get_json(self, url, *, params=None):
        self.calls.append((url, params, None, None))
        return self.objects[url]


def disabled_files_error():
    return NetworkError(
        "Quercus returned HTTP 403: user not authorized to perform that action",
        status_code=403,
        response_detail="user not authorized to perform that action",
    )


def disabled_pages_error():
    return NetworkError(
        "Quercus returned HTTP 404: That page has been disabled for this course",
        status_code=404,
        response_detail="That page has been disabled for this course",
    )


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
        self.assertEqual(rows.discovery, {"source": "collection", "complete": True})

    def test_disabled_files_fall_back_to_module_references_in_stable_deduplicated_order(self):
        client = DisabledCollectionClient(
            files_error=disabled_files_error(),
            modules=[
                {"id": 1, "items_count": 3, "items": [
                    {"type": "File", "content_id": 11, "title": "First"},
                    {"type": "File", "content_id": 12, "title": "Assignment 2"},
                    {"type": "File", "content_id": 11, "title": "Duplicate"},
                ]},
                {"id": 2, "items_count": 1, "items": [
                    {"type": "File", "content_id": 13, "title": "Third"},
                ]},
            ],
            objects={
                "/api/v1/courses/10/files/11": {"id": 11, "display_name": "first.pdf", "updated_at": "2026-08-03T00:00:00Z"},
                "/api/v1/courses/10/files/12": {"id": 12, "display_name": "assignment-2.pdf", "updated_at": "2026-08-02T00:00:00Z"},
                "/api/v1/courses/10/files/13": {"id": 13, "display_name": "third.pdf", "updated_at": "2026-08-01T00:00:00Z"},
            },
        )
        rows = Quercus(client).files(10, limit=3)
        self.assertEqual([row["id"] for row in rows], [11, 12, 13])
        self.assertFalse(rows.discovery["complete"])
        self.assertEqual(rows.discovery["source"], "modules")
        self.assertIn("disabled", rows.discovery["reason"])
        self.assertEqual(sum(call[0].endswith("/files/11") for call in client.calls), 1)

    def test_module_file_fallback_preserves_search_since_and_limit(self):
        client = DisabledCollectionClient(
            files_error=disabled_files_error(),
            modules=[{"id": 1, "items_count": 3, "items": [
                {"type": "File", "content_id": 11, "title": "old assignment"},
                {"type": "File", "content_id": 12, "title": "Assignment 2"},
                {"type": "File", "content_id": 13, "title": "Assignment 3"},
            ]}],
            objects={
                "/api/v1/courses/10/files/11": {"id": 11, "display_name": "old-assignment.pdf", "updated_at": "2026-07-01T00:00:00Z"},
                "/api/v1/courses/10/files/12": {"id": 12, "display_name": "assignment-2.pdf", "updated_at": "2026-08-02T00:00:00Z"},
                "/api/v1/courses/10/files/13": {"id": 13, "display_name": "assignment-3.pdf", "updated_at": "2026-08-03T00:00:00Z"},
            },
        )
        rows = Quercus(client).files(
            10,
            limit=1,
            search="assignment",
            since=datetime(2026, 8, 1, tzinfo=UTC),
        )
        self.assertEqual([row["id"] for row in rows], [12])

    def test_disabled_pages_use_only_explicit_slugs_and_apply_search(self):
        client = DisabledCollectionClient(
            pages_error=disabled_pages_error(),
            modules=[{"id": 1, "items_count": 3, "items": [
                {"type": "Page", "page_url": "overview", "title": "Overview"},
                {"type": "Page", "page_url": "recordings-2026", "title": "Zoom Links and Recordings 2026"},
                {"type": "Page", "page_url": "recordings-2026", "title": "Duplicate"},
            ]}],
            objects={
                "/api/v1/courses/10/pages/overview": {"page_id": 1, "url": "overview", "title": "Overview"},
                "/api/v1/courses/10/pages/recordings-2026": {
                    "page_id": 2, "url": "recordings-2026", "title": "Zoom Links and Recordings 2026",
                },
            },
        )
        rows = Quercus(client).pages(10, limit=1, search="recording")
        self.assertEqual([(row["id"], row["url"]) for row in rows], [(2, "recordings-2026")])
        self.assertEqual(rows.discovery["source"], "modules")
        object_calls = [call[0] for call in client.calls if "/pages/" in call[0]]
        self.assertEqual(object_calls, ["/api/v1/courses/10/pages/recordings-2026"])

    def test_module_discovery_has_explicit_metadata_and_item_call_budgets(self):
        items = [{"type": "File", "content_id": value, "title": str(value)} for value in range(1, 41)]
        objects = {
            f"/api/v1/courses/10/files/{value}": {"id": value, "display_name": f"{value}.pdf"}
            for value in range(1, 41)
        }
        client = DisabledCollectionClient(
            files_error=disabled_files_error(),
            modules=[{"id": 1, "items_count": 40, "items": items}],
            objects=objects,
        )
        rows = Quercus(client).files(10, limit=100)
        self.assertEqual(len(rows), MODULE_DISCOVERY_MAX_METADATA_REQUESTS)
        metadata_calls = [call for call in client.calls if "/files/" in call[0]]
        self.assertEqual(len(metadata_calls), MODULE_DISCOVERY_MAX_METADATA_REQUESTS)
        module_call = next(call for call in client.calls if call[0].endswith("/modules"))
        self.assertEqual(module_call[3], 1)

    def test_module_item_collection_calls_are_bounded(self):
        modules = [{"id": value, "items_count": 1} for value in range(1, 11)]
        objects = {
            ("module", value): [{"type": "File", "content_id": value, "title": str(value)}]
            for value in range(1, 11)
        }
        objects.update({
            f"/api/v1/courses/10/files/{value}": {"id": value, "display_name": f"{value}.pdf"}
            for value in range(1, 9)
        })
        client = DisabledCollectionClient(
            files_error=disabled_files_error(), modules=modules, objects=objects,
        )
        rows = Quercus(client).files(10, limit=100)
        self.assertEqual([row["id"] for row in rows], list(range(1, 9)))
        item_calls = [call for call in client.calls if "/modules/" in call[0] and call[0].endswith("/items")]
        self.assertEqual(len(item_calls), 8)
        self.assertTrue(all(call[3] == 1 for call in item_calls))

    def test_only_exact_disabled_collection_errors_trigger_fallback(self):
        unrelated = NetworkError(
            "Quercus returned HTTP 403: forbidden",
            status_code=403,
            response_detail="forbidden",
        )
        for error in (unrelated, SessionRejectedError("rejected")):
            client = DisabledCollectionClient(files_error=error)
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                Quercus(client).files(10, limit=10)
            self.assertFalse(any(call[0].endswith("/modules") for call in client.calls))

    def test_grades_never_project_unposted_fields(self):
        result = Quercus(FakeClient()).grades(10, limit=10)
        serialized = repr(result).casefold()
        self.assertNotIn("unposted", serialized)
        self.assertEqual(result["enrollments"][0]["currentScore"], 94)
        self.assertEqual(result["assignments"][0]["submission"]["score"], 9)


if __name__ == "__main__":
    unittest.main()
