from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from .client import CanvasClient
from .content import parse_html
from .errors import NetworkError, UsageError

_DURATION = re.compile(r"^([1-9][0-9]*)([mhdw])$")
COURSE_STATES = {"active", "completed", "pending", "all"}
CANVAS_ENROLLMENT_STATES = {
    "active": "active",
    "completed": "completed",
    "pending": "invited_or_pending",
}


def parse_duration(value: str, *, now: datetime | None = None) -> datetime:
    match = _DURATION.fullmatch(str(value).strip().casefold())
    if not match:
        raise UsageError("duration must be a positive integer followed by m, h, d, or w")
    number = int(match.group(1))
    if number > 100_000:
        raise UsageError("duration is unreasonably large")
    unit = match.group(2)
    seconds = number * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    current = now or datetime.now(UTC)
    return current - timedelta(seconds=seconds)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def positive_id(value: Any, label: str) -> int:
    text = str(value).strip()
    if not text.isdigit() or int(text) <= 0 or len(text) > 20:
        raise UsageError(f"{label} must be a positive numeric ID")
    return int(text)


def _teacher(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "id": raw.get("id"),
        "name": raw.get("display_name") or raw.get("name"),
    }


def project_course(raw: dict[str, Any]) -> dict[str, Any]:
    term = raw.get("term") if isinstance(raw.get("term"), dict) else {}
    enrollments = raw.get("enrollments") if isinstance(raw.get("enrollments"), list) else []
    enrollment = next((value for value in enrollments if isinstance(value, dict)), {})
    raw_teachers = raw.get("teachers") if isinstance(raw.get("teachers"), list) else []
    teachers = [value for value in (_teacher(row) for row in raw_teachers) if value]
    identifier = raw.get("id")
    return {
        "id": identifier,
        "name": raw.get("name"),
        "originalName": raw.get("original_name"),
        "courseCode": raw.get("course_code"),
        "state": raw.get("workflow_state"),
        "startAt": raw.get("start_at"),
        "endAt": raw.get("end_at"),
        "timeZone": raw.get("time_zone"),
        "accessRestrictedByDate": bool(raw.get("access_restricted_by_date")),
        "term": {
            "id": term.get("id"),
            "name": term.get("name"),
            "startAt": term.get("start_at"),
            "endAt": term.get("end_at"),
        },
        "teachers": teachers,
        "enrollment": {
            "type": enrollment.get("type"),
            "role": enrollment.get("role"),
            "state": enrollment.get("enrollment_state"),
        },
        "htmlUrl": f"https://q.utoronto.ca/courses/{identifier}" if identifier else None,
    }


class Quercus:
    def __init__(self, client: CanvasClient):
        self.client = client

    @staticmethod
    def _course_params(enrollment_state: str) -> list[tuple[str, Any]]:
        return [
            ("enrollment_state", enrollment_state),
            ("include[]", "term"),
            ("include[]", "teachers"),
            ("include[]", "concluded"),
            ("per_page", 100),
        ]

    def courses(self, *, state: str = "active", limit: int = 100) -> list[dict[str, Any]]:
        if state not in COURSE_STATES:
            raise UsageError("course state must be active, completed, pending, or all")
        states = ("active", "pending", "completed") if state == "all" else (state,)
        values: dict[int, dict[str, Any]] = {}
        for selected in states:
            rows = self.client.collect(
                "/api/v1/courses",
                params=self._course_params(CANVAS_ENROLLMENT_STATES[selected]),
                limit=min(limit, 100),
            )
            for row in rows:
                identifier = row.get("id")
                if isinstance(identifier, int):
                    values[identifier] = project_course(row)
            if len(values) >= limit:
                break
        return list(values.values())[:limit]

    def course(self, course_id: int) -> dict[str, Any]:
        value = self.client.get_json(
            f"/api/v1/courses/{course_id}",
            params=[("include[]", "term"), ("include[]", "teachers")],
        )
        if not isinstance(value, dict) or value.get("id") != course_id:
            raise NetworkError("Quercus returned an invalid course")
        return project_course(value)

    def resolve_course(self, reference: str) -> dict[str, Any]:
        reference = str(reference).strip()
        if not reference:
            raise UsageError("course reference may not be empty")
        if reference.isdigit():
            return self.course(positive_id(reference, "course ID"))
        courses = self.courses(state="all", limit=100)
        needle = reference.casefold()

        def labels(course: dict[str, Any]) -> list[str]:
            return [
                str(course.get("courseCode") or ""),
                str(course.get("name") or ""),
                str(course.get("originalName") or ""),
            ]

        exact = [course for course in courses if any(label.casefold() == needle for label in labels(course) if label)]
        matches = exact or [course for course in courses if any(needle in label.casefold() for label in labels(course))]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise UsageError(f"no enrolled Quercus course matched {reference!r}")
        candidates = [
            {"id": row.get("id"), "courseCode": row.get("courseCode"), "name": row.get("name")}
            for row in matches[:10]
        ]
        raise UsageError("course reference is ambiguous; use its numeric ID", details={"candidates": candidates})

    def announcements(
        self,
        course_id: int,
        *,
        limit: int,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [
            ("context_codes[]", f"course_{course_id}"),
            ("per_page", 100),
        ]
        if since:
            params.append(("start_date", iso_utc(since)))
        rows = self.client.collect("/api/v1/announcements", params=params, limit=100)
        result: list[dict[str, Any]] = []
        for row in rows:
            body, links = parse_html(row.get("message"))
            attachments = []
            raw_attachments = row.get("attachments") if isinstance(row.get("attachments"), list) else []
            for attachment in raw_attachments:
                if isinstance(attachment, dict):
                    attachments.append({
                        "id": attachment.get("id"),
                        "name": attachment.get("display_name") or attachment.get("filename"),
                        "contentType": attachment.get("content-type"),
                        "size": attachment.get("size"),
                    })
            result.append({
                "id": row.get("id"),
                "title": row.get("title"),
                "postedAt": row.get("posted_at"),
                "delayedPostAt": row.get("delayed_post_at"),
                "author": row.get("user_name"),
                "published": row.get("published"),
                "body": body,
                "links": links,
                "attachments": attachments,
                "htmlUrl": row.get("html_url"),
            })
        result.sort(key=lambda row: parse_time(row.get("postedAt")) or datetime.min.replace(tzinfo=UTC), reverse=True)
        return result[:limit]

    @staticmethod
    def _submission(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        return {
            "id": raw.get("id"),
            "workflowState": raw.get("workflow_state"),
            "submittedAt": raw.get("submitted_at"),
            "gradedAt": raw.get("graded_at"),
            "postedAt": raw.get("posted_at"),
            "score": raw.get("score"),
            "grade": raw.get("grade"),
            "attempt": raw.get("attempt"),
            "late": bool(raw.get("late")),
            "missing": bool(raw.get("missing")),
            "excused": bool(raw.get("excused")),
        }

    def assignments(
        self,
        course_id: int,
        *,
        limit: int,
        bucket: str | None = None,
        details: bool = False,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [
            ("include[]", "submission"),
            ("order_by", "due_at"),
            ("per_page", 100),
        ]
        if bucket:
            params.append(("bucket", bucket))
        rows = self.client.collect(
            f"/api/v1/courses/{course_id}/assignments",
            params=params,
            limit=limit,
        )
        result = []
        for row in rows:
            value = {
                "id": row.get("id"),
                "name": row.get("name"),
                "dueAt": row.get("due_at"),
                "unlockAt": row.get("unlock_at"),
                "lockAt": row.get("lock_at"),
                "pointsPossible": row.get("points_possible"),
                "gradingType": row.get("grading_type"),
                "submissionTypes": row.get("submission_types") if isinstance(row.get("submission_types"), list) else [],
                "published": row.get("published"),
                "lockedForUser": bool(row.get("locked_for_user")),
                "lockExplanation": row.get("lock_explanation"),
                "submission": self._submission(row.get("submission")),
                "htmlUrl": row.get("html_url"),
            }
            if details:
                body, links = parse_html(row.get("description"))
                value["description"] = body
                value["links"] = links
            result.append(value)
        return result

    @staticmethod
    def _module_item(raw: dict[str, Any]) -> dict[str, Any]:
        details = raw.get("content_details") if isinstance(raw.get("content_details"), dict) else {}
        completion = raw.get("completion_requirement") if isinstance(raw.get("completion_requirement"), dict) else None
        return {
            "id": raw.get("id"),
            "moduleId": raw.get("module_id"),
            "position": raw.get("position"),
            "indent": raw.get("indent"),
            "title": raw.get("title"),
            "type": raw.get("type"),
            "contentId": raw.get("content_id"),
            "pageUrl": raw.get("page_url"),
            "apiUrl": raw.get("url"),
            "htmlUrl": raw.get("html_url"),
            "externalUrl": raw.get("external_url"),
            "dueAt": details.get("due_at"),
            "unlockAt": details.get("unlock_at"),
            "lockAt": details.get("lock_at"),
            "lockedForUser": bool(details.get("locked_for_user")),
            "lockExplanation": details.get("lock_explanation"),
            "completion": {
                "type": completion.get("type"),
                "completed": completion.get("completed"),
                "minimumScore": completion.get("min_score"),
            } if completion else None,
        }

    def modules(self, course_id: int, *, limit: int) -> list[dict[str, Any]]:
        rows = self.client.collect(
            f"/api/v1/courses/{course_id}/modules",
            params=[("include[]", "items"), ("include[]", "content_details"), ("per_page", 100)],
            limit=limit,
        )
        result = []
        for row in rows:
            raw_items = row.get("items")
            if not isinstance(raw_items, list):
                module_id = row.get("id")
                count = row.get("items_count")
                item_limit = min(int(count), 100) if isinstance(count, int) and count > 0 else 100
                raw_items = self.client.collect(
                    f"/api/v1/courses/{course_id}/modules/{positive_id(module_id, 'module ID')}/items",
                    params=[("include[]", "content_details"), ("per_page", 100)],
                    limit=item_limit,
                )
            items = [self._module_item(value) for value in raw_items if isinstance(value, dict)]
            count = row.get("items_count")
            result.append({
                "id": row.get("id"),
                "name": row.get("name"),
                "position": row.get("position"),
                "state": row.get("state"),
                "unlockAt": row.get("unlock_at"),
                "requireSequentialProgress": bool(row.get("require_sequential_progress")),
                "completedAt": row.get("completed_at"),
                "itemsCount": count,
                "itemsTruncated": isinstance(count, int) and len(items) < count,
                "items": items,
            })
        return result

    def files(
        self,
        course_id: int,
        *,
        limit: int,
        since: datetime | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("sort", "updated_at"), ("order", "desc"), ("per_page", 100)]
        if search:
            params.append(("search_term", search))
        candidate_limit = 100 if since else limit
        rows = self.client.collect(f"/api/v1/courses/{course_id}/files", params=params, limit=candidate_limit)
        result = []
        for row in rows:
            timestamp = parse_time(row.get("updated_at"))
            if since and (timestamp is None or timestamp < since):
                continue
            result.append(self.project_file(row))
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def project_file(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "folderId": row.get("folder_id"),
            "name": row.get("display_name") or row.get("filename"),
            "filename": row.get("filename"),
            "contentType": row.get("content-type"),
            "size": row.get("size"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "modifiedAt": row.get("modified_at"),
            "locked": bool(row.get("locked")),
            "hidden": bool(row.get("hidden")),
            "lockedForUser": bool(row.get("locked_for_user")),
            "lockExplanation": row.get("lock_explanation"),
        }

    def file(self, course_id: int, file_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        value = self.client.get_json(f"/api/v1/courses/{course_id}/files/{file_id}")
        if not isinstance(value, dict) or value.get("id") != file_id:
            raise NetworkError("Quercus returned invalid metadata for the selected file")
        return self.project_file(value), value

    def pages(self, course_id: int, *, limit: int, search: str | None = None) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("sort", "updated_at"), ("order", "desc"), ("per_page", 100)]
        if search:
            params.append(("search_term", search))
        rows = self.client.collect(f"/api/v1/courses/{course_id}/pages", params=params, limit=limit)
        return [{
            "id": row.get("page_id"),
            "url": row.get("url"),
            "title": row.get("title"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "published": row.get("published"),
            "frontPage": bool(row.get("front_page")),
            "htmlUrl": row.get("html_url"),
        } for row in rows]

    def page(self, course_id: int, reference: str) -> dict[str, Any]:
        reference = str(reference).strip()
        if not reference or len(reference) > 500:
            raise UsageError("page reference is empty or too long")
        path_ref = f"page_id:{reference}" if reference.isdigit() else reference
        value = self.client.get_json(
            f"/api/v1/courses/{course_id}/pages/{quote(path_ref, safe='')}",
        )
        if not isinstance(value, dict):
            raise NetworkError("Quercus returned an invalid page")
        body, links = parse_html(value.get("body"))
        return {
            "id": value.get("page_id"),
            "url": value.get("url"),
            "title": value.get("title"),
            "createdAt": value.get("created_at"),
            "updatedAt": value.get("updated_at"),
            "published": value.get("published"),
            "frontPage": bool(value.get("front_page")),
            "body": body,
            "links": links,
            "htmlUrl": value.get("html_url"),
        }

    def grades(self, course_id: int, *, limit: int) -> dict[str, Any]:
        enrollments = self.client.collect(
            f"/api/v1/courses/{course_id}/enrollments",
            params=[
                ("user_id", "self"),
                ("type[]", "StudentEnrollment"),
                ("include[]", "current_points"),
                ("per_page", 10),
            ],
            limit=10,
        )
        safe_enrollments = []
        for row in enrollments:
            grades = row.get("grades") if isinstance(row.get("grades"), dict) else {}
            safe_enrollments.append({
                "id": row.get("id"),
                "state": row.get("enrollment_state"),
                "type": row.get("type"),
                "currentGrade": grades.get("current_grade"),
                "currentScore": grades.get("current_score"),
                "currentPoints": grades.get("current_points"),
                "finalGrade": grades.get("final_grade"),
                "finalScore": grades.get("final_score"),
                "htmlUrl": grades.get("html_url"),
            })
        assignments = self.assignments(course_id, limit=limit)
        grade_rows = [{
            "assignmentId": row.get("id"),
            "name": row.get("name"),
            "dueAt": row.get("dueAt"),
            "pointsPossible": row.get("pointsPossible"),
            "submission": row.get("submission"),
            "htmlUrl": row.get("htmlUrl"),
        } for row in assignments]
        return {"enrollments": safe_enrollments, "assignments": grade_rows}
