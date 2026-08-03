from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .browser_import import VimbrowserImporter
from .canvas import Quercus, parse_duration, positive_id
from .client import CanvasClient
from .downloads import Downloads
from .errors import QuercusError, UsageError
from .persistent_browser import PersistentBrowserAuthenticator, delete_browser_profile
from .renewal import load_or_refresh_session
from .session import delete_session, save_session

SCHEMA_VERSION = 1
ASSIGNMENT_BUCKETS = {"past", "overdue", "undated", "ungraded", "unsubmitted", "upcoming", "future"}


def emit_json(value: Any) -> None:
    print(json.dumps({"schemaVersion": SCHEMA_VERSION, "data": value}, indent=2, ensure_ascii=False))


def emit_error(error: QuercusError, *, as_json: bool) -> None:
    if as_json:
        payload: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "error": {
                "kind": type(error).__name__,
                "message": error.message,
                "exitCode": error.exit_code,
            },
        }
        if error.details:
            payload["error"]["details"] = error.details
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"quercus: {error.message}", file=sys.stderr)


def context() -> tuple[Any, dict[str, Any], Quercus]:
    session, client, profile = load_or_refresh_session()
    return session, profile, Quercus(client)


def course_context(reference: str) -> tuple[dict[str, Any], Quercus]:
    _, _, quercus = context()
    return quercus.resolve_course(reference), quercus


def _course_title(course: dict[str, Any]) -> str:
    code = course.get("courseCode")
    name = course.get("name")
    if code and name and code != name:
        return f"{code} — {name}"
    return str(name or code or course.get("id") or "Course")


def command_login(args: argparse.Namespace) -> None:
    if args.persistent:
        if args.tab is not None:
            raise UsageError("--tab is valid only with --from-vimbrowser")
        if not args.json:
            print("Opening the helper-owned Quercus session. Complete U of T sign-in and Duo if prompted.")
        session = PersistentBrowserAuthenticator().acquire(interactive=True)
    else:
        session = VimbrowserImporter().import_session(tab_id=args.tab)
    client = CanvasClient(session)
    profile = session.assert_account(client.profile())
    save_session(session)
    result = {**session.public(), "user": profile}
    if args.json:
        emit_json(result)
    else:
        if session.renewal_mode == "persistent-browser":
            print("Authenticated with a helper-owned renewable Quercus browser session.")
        else:
            print("Authenticated with an imported short-lived Quercus browser session.")
        print(f"  Name:    {profile.get('name') or '-'}")
        print(f"  Login:   {profile.get('loginId') or profile.get('primaryEmail') or '-'}")
        print(f"  User ID: {profile.get('id')}")


def command_logout(args: argparse.Namespace) -> None:
    removed = delete_session()
    browser_removed = delete_browser_profile()
    result = {"loggedOut": True, "sessionRemoved": removed, "browserProfileRemoved": browser_removed}
    if args.json:
        emit_json(result)
    else:
        print("Logged out locally and removed helper-owned Quercus credentials.")


def command_status(args: argparse.Namespace) -> None:
    session, profile, _ = context()
    result = {**session.public(), "user": profile}
    if args.json:
        emit_json(result)
    else:
        print("Authenticated.")
        print(f"  Name:     {profile.get('name') or '-'}")
        print(f"  Login:    {profile.get('loginId') or profile.get('primaryEmail') or '-'}")
        print(f"  User ID:  {profile.get('id')}")
        print(f"  Renewal:  {'automatic while U of T keeps the browser session valid' if result['automaticRenewal'] else 'none'}")


def command_whoami(args: argparse.Namespace) -> None:
    _, profile, _ = context()
    if args.json:
        emit_json(profile)
    else:
        print(f"Name:    {profile.get('name') or '-'}")
        print(f"Login:   {profile.get('loginId') or '-'}")
        print(f"Email:   {profile.get('primaryEmail') or '-'}")
        print(f"User ID: {profile.get('id')}")
        print(f"Zone:    {profile.get('timeZone') or '-'}")


def command_courses(args: argparse.Namespace) -> None:
    _, _, quercus = context()
    rows = quercus.courses(state=args.state, limit=args.limit)
    if args.json:
        emit_json(rows)
        return
    if not rows:
        print("No courses found.")
        return
    for row in rows:
        print(f"{_course_title(row)}")
        print(f"  ID:         {row.get('id')}")
        print(f"  Term:       {row.get('term', {}).get('name') or '-'}")
        print(f"  Enrollment: {row.get('enrollment', {}).get('state') or row.get('state') or '-'}")
        teachers = ", ".join(str(value.get("name")) for value in row.get("teachers", []) if value.get("name"))
        if teachers:
            print(f"  Teachers:   {teachers}")


def command_announcements(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    rows = quercus.announcements(
        int(course["id"]),
        limit=args.limit,
        since=parse_duration(args.since) if args.since else None,
    )
    result = {"course": course, "announcements": rows}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    if not rows:
        print("No announcements found.")
        return
    for index, row in enumerate(rows, 1):
        print(f"\n=== Announcement {index} of {len(rows)} ===")
        print(f"{row.get('postedAt') or '-'}  {row.get('title') or '(untitled)'}")
        if row.get("author"):
            print(f"By: {row['author']}")
        print(f"ID: {row.get('id')}")
        if row.get("body"):
            print("\n" + row["body"])
        if row.get("links"):
            print("\nLinks:")
            for link in row["links"]:
                print(f"  {link.get('url')}")


def _print_submission(value: dict[str, Any] | None) -> str:
    if not value:
        return "not submitted"
    state = str(value.get("workflowState") or "unknown")
    grade = value.get("grade")
    score = value.get("score")
    if grade is not None or score is not None:
        state += f", grade {grade if grade is not None else score}"
    if value.get("missing"):
        state += ", missing"
    if value.get("late"):
        state += ", late"
    return state


def command_assignments(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    rows = quercus.assignments(
        int(course["id"]),
        limit=args.limit,
        bucket=args.bucket,
        details=args.details,
    )
    result = {"course": course, "assignments": rows}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    if not rows:
        print("No assignments found.")
        return
    for row in rows:
        print(f"\n{row.get('dueAt') or 'undated'}  {row.get('name') or '(untitled)'}")
        print(f"  ID:         {row.get('id')}")
        print(f"  Points:     {row.get('pointsPossible') if row.get('pointsPossible') is not None else '-'}")
        print(f"  Submission: {_print_submission(row.get('submission'))}")
        if args.details and row.get("description"):
            print("\n" + row["description"])


def command_modules(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    rows = quercus.modules(int(course["id"]), limit=args.limit)
    result = {"course": course, "modules": rows}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    if not rows:
        print("No modules found.")
        return
    for module in rows:
        truncated = " (items truncated)" if module.get("itemsTruncated") else ""
        print(f"\n=== {module.get('position') or '-'}: {module.get('name') or '(untitled)'}{truncated} ===")
        for item in module.get("items", []):
            due = f"  due {item['dueAt']}" if item.get("dueAt") else ""
            locked = "  [locked]" if item.get("lockedForUser") else ""
            print(f"  {item.get('position') or '-'}  [{item.get('type') or '?'}] {item.get('title') or '(untitled)'}{due}{locked}")
            print(f"       Item ID: {item.get('id')}  Content ID: {item.get('contentId') or '-'}")


def command_files(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    rows = quercus.files(
        int(course["id"]),
        limit=args.limit,
        since=parse_duration(args.since) if args.since else None,
        search=args.search,
    )
    result = {"course": course, "files": rows}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    if not rows:
        print("No files found.")
        return
    for row in rows:
        print(f"{row.get('updatedAt') or '-'}  {row.get('name') or '(unnamed)'}")
        print(f"  ID: {row.get('id')}  Size: {row.get('size') or 0} bytes  Type: {row.get('contentType') or '-'}")


def command_download(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    result = Downloads(quercus).download(
        int(course["id"]),
        positive_id(args.file_id, "file ID"),
        output_directory=args.out,
        force=args.force,
    )
    payload = {"course": course, "file": result}
    if args.json:
        emit_json(payload)
    else:
        print(f"Downloaded privately: {result['path']} ({result['bytes']} bytes)")


def command_pages(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    rows = quercus.pages(int(course["id"]), limit=args.limit, search=args.search)
    result = {"course": course, "pages": rows}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    if not rows:
        print("No pages found.")
        return
    for row in rows:
        print(f"{row.get('updatedAt') or '-'}  {row.get('title') or '(untitled)'}")
        print(f"  ID: {row.get('id')}  URL: {row.get('url') or '-'}")


def command_page(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    row = quercus.page(int(course["id"]), args.page)
    result = {"course": course, "page": row}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    print(f"\n{row.get('title') or '(untitled)'}")
    print(f"Updated: {row.get('updatedAt') or '-'}")
    print(f"ID:      {row.get('id') or '-'}")
    if row.get("body"):
        print("\n" + row["body"])
    if row.get("links"):
        print("\nLinks:")
        for link in row["links"]:
            label = f" — {link['text']}" if link.get("text") else ""
            print(f"  {link.get('url')}{label}")


def command_grades(args: argparse.Namespace) -> None:
    course, quercus = course_context(args.course)
    grades = quercus.grades(int(course["id"]), limit=args.limit)
    result = {"course": course, **grades}
    if args.json:
        emit_json(result)
        return
    print(_course_title(course))
    if not grades["enrollments"]:
        print("No student enrollment/aggregate grade was returned.")
    for enrollment in grades["enrollments"]:
        print(f"Current: {enrollment.get('currentGrade') or '-'} ({enrollment.get('currentScore') if enrollment.get('currentScore') is not None else '-'}%)")
        print(f"Final:   {enrollment.get('finalGrade') or '-'} ({enrollment.get('finalScore') if enrollment.get('finalScore') is not None else '-'}%)")
    print("\nAssignment release status:")
    for row in grades["assignments"]:
        print(f"  {row.get('dueAt') or 'undated'}  {row.get('name') or '(untitled)'}")
        print(f"    {_print_submission(row.get('submission'))}")


def add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit stable JSON")


def add_limit(parser: argparse.ArgumentParser, default: int = 100) -> None:
    parser.add_argument("--limit", "-n", type=int, default=default, help="maximum items (1-100)")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="quercus",
        description="Unofficial strictly read-only Quercus/Canvas CLI using a private browser session.",
    )
    root.add_argument("--version", action="version", version=f"quercus {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="initialize or import a Quercus browser session")
    source = login.add_mutually_exclusive_group(required=True)
    source.add_argument("--persistent", action="store_true", help="initialize the helper-owned renewable browser session")
    source.add_argument("--from-vimbrowser", action="store_true", help="import cookies from one exact authenticated Quercus tab")
    login.add_argument("--tab", type=int, help="exact vimbrowser Quercus tab ID")
    add_json(login)
    login.set_defaults(func=command_login)

    logout = commands.add_parser("logout", help="delete only the helper's local session/profile")
    add_json(logout)
    logout.set_defaults(func=command_logout)
    status = commands.add_parser("status", help="validate the saved session and account")
    add_json(status)
    status.set_defaults(func=command_status)
    whoami = commands.add_parser("whoami", help="show the authenticated Quercus account")
    add_json(whoami)
    whoami.set_defaults(func=command_whoami)

    courses = commands.add_parser("courses", help="list the current user's courses")
    courses.add_argument("--state", choices=["active", "completed", "pending", "all"], default="active")
    add_limit(courses)
    add_json(courses)
    courses.set_defaults(func=command_courses)

    announcements = commands.add_parser("announcements", help="list course announcements including readable bodies")
    announcements.add_argument("course", help="numeric ID, course code, or unique name fragment")
    announcements.add_argument("--since", help="only announcements since a duration such as 7d")
    add_limit(announcements, 20)
    add_json(announcements)
    announcements.set_defaults(func=command_announcements)

    assignments = commands.add_parser("assignments", help="list assignments and the current user's submission status")
    assignments.add_argument("course")
    assignments.add_argument("--bucket", choices=sorted(ASSIGNMENT_BUCKETS))
    assignments.add_argument("--details", action="store_true", help="include descriptions and extracted links")
    add_limit(assignments)
    add_json(assignments)
    assignments.set_defaults(func=command_assignments)

    modules = commands.add_parser("modules", help="list modules and their items")
    modules.add_argument("course")
    add_limit(modules)
    add_json(modules)
    modules.set_defaults(func=command_modules)

    files = commands.add_parser("files", help="list course file metadata")
    files.add_argument("course")
    files.add_argument("--since", help="only files updated since a duration such as 2d")
    files.add_argument("--search", help="partial filename search")
    add_limit(files)
    add_json(files)
    files.set_defaults(func=command_files)

    download = commands.add_parser("download", help="download one explicitly selected course file privately")
    download.add_argument("course")
    download.add_argument("file_id")
    download.add_argument("--out", required=True, help="output directory")
    download.add_argument("--force", action="store_true", help="replace an existing regular file")
    add_json(download)
    download.set_defaults(func=command_download)

    pages = commands.add_parser("pages", help="list course pages")
    pages.add_argument("course")
    pages.add_argument("--search", help="partial page-title search")
    add_limit(pages)
    add_json(pages)
    pages.set_defaults(func=command_pages)

    page = commands.add_parser("page", help="show one explicit course page and its links")
    page.add_argument("course")
    page.add_argument("page", help="page ID or URL slug")
    add_json(page)
    page.set_defaults(func=command_page)

    grades = commands.add_parser("grades", help="show only the current user's aggregate and assignment grade status")
    grades.add_argument("course")
    add_limit(grades)
    add_json(grades)
    grades.set_defaults(func=command_grades)
    return root


def main() -> None:
    args = parser().parse_args()
    as_json = bool(getattr(args, "json", False))
    try:
        if hasattr(args, "limit") and not 1 <= args.limit <= 100:
            raise UsageError("--limit must be between 1 and 100")
        args.func(args)
    except QuercusError as error:
        emit_error(error, as_json=as_json)
        raise SystemExit(error.exit_code)


if __name__ == "__main__":
    main()
