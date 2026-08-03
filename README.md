# quercus

An unofficial, user-local, strictly read-only CLI for the University of Toronto's Quercus (Canvas LMS) instance.

`quercus` owns a private persistent Chromium profile, reuses the resulting first-party Canvas cookie session, and calls a small allowlist of Canvas HTTPS `GET` endpoints. It can inspect courses, announcements, assignments, modules, files, pages, and the authenticated student's own grade/submission state. It can also download one explicitly selected course file.

> [!WARNING]
> This project is not affiliated with, supported by, or endorsed by the University of Toronto or Instructure. Use it only with a Quercus account you own and at human-scale request rates.

## Read-only boundary

The CLI intentionally contains **no** commands or network paths for:

- assignment/file uploads or submissions;
- quiz attempts or answers;
- discussion posts or announcement creation;
- marking module items complete;
- accepting or rejecting course invitations;
- course enrolment changes;
- grade changes;
- profile/settings changes;
- arbitrary Canvas API requests.

All direct Canvas API traffic is HTTPS `GET` to exact allowlisted paths on `https://q.utoronto.ca`. Redirects are disabled for JSON API calls. File download redirects are accepted only from an allowlisted Canvas route to HTTPS Canvas User Content/Instructure Cloud Gate/Instructure/AWS/CloudFront storage, and no Quercus cookies are sent to those storage hosts.

The saved browser profile and cookies nevertheless have the broader capabilities of a normal signed-in Quercus browser. Read-only behavior is enforced by this local CLI, not by server-side cookie scopes. See [SECURITY.md](SECURITY.md).

## Features

- Private persistent U of T/Quercus sign-in through Playwright Chromium.
- Best-effort headless session recovery through the helper-owned profile.
- Exact optional cookie import from one authenticated `vimbrowser` Quercus tab.
- Course aliases with ambiguity detection rather than first-match guessing.
- Announcement bodies and safe local link extraction.
- Assignment deadlines and only the current user's submission state.
- Module/item inspection with bounded fallback pagination.
- File/page discovery and explicit page-body retrieval. If an otherwise accessible course disables
  one of those collections, the corresponding list command can fall back to bounded, visible module
  links and explicitly marks that result incomplete; it never claims to enumerate unlinked objects.
- Current user's posted aggregate and per-assignment grade state; unposted grade fields are discarded.
- Safe, bounded, private downloads of explicitly selected files.
- Stable JSON output and stable exit-code classes.

## Requirements

- Linux (session storage uses `fcntl` locking)
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- X11 for initial interactive authentication
- A University of Toronto Quercus account

## Install

Clone the public repository and create its isolated environment:

```bash
git clone https://github.com/Yeyito777/quercus.git
cd quercus
uv sync --locked
uv run playwright install chromium
./quercus --help
```

The wrapper uses only the repository-local `.venv`.

## Login

Recommended renewable mode:

```bash
./quercus login --persistent
```

A dedicated Chromium window opens. Complete U of T sign-in and Duo normally. Once Canvas's current-user profile endpoint succeeds, the helper saves the relevant first-party cookie set and closes the window.

Normal commands first use the saved cookies directly. If Canvas redirects that session to sign-in, the helper starts its dedicated profile headlessly and attempts to renew through existing U of T browser state. If Duo or another human check is required, it fails with exit code 3 and asks for another interactive login.

Renewal is best-effort, not permanent. Password changes, explicit revocation, Conditional Access, MFA policy, inactivity, upstream changes, or U of T session limits can require human authentication.

### Short-lived vimbrowser import

If Quercus is already authenticated in `vimbrowser`:

```bash
./quercus login --from-vimbrowser --tab TAB_ID
```

This copies only cookies visible to `https://q.utoronto.ca/`, validates them against `/api/v1/users/self/profile`, and stores them privately. It does **not** copy U of T SSO state or enable automatic renewal. When several Quercus tabs exist, `--tab` is mandatory; the helper never guesses among them.

## Commands

```text
quercus login --persistent [--json]
quercus login --from-vimbrowser [--tab TAB_ID] [--json]
quercus logout [--json]
quercus status [--json]
quercus whoami [--json]
quercus courses [--state active|completed|pending|all] [--limit N] [--json]
quercus announcements COURSE [--since DURATION] [--limit N] [--json]
quercus assignments COURSE [--bucket BUCKET] [--details] [--limit N] [--json]
quercus modules COURSE [--limit N] [--json]
quercus files COURSE [--since DURATION] [--search TEXT] [--limit N] [--json]
quercus download COURSE FILE_ID --out DIRECTORY [--force] [--json]
quercus pages COURSE [--search TEXT] [--limit N] [--json]
quercus page COURSE PAGE_ID_OR_SLUG [--json]
quercus grades COURSE [--limit N] [--json]
```

`COURSE` may be a numeric Canvas course ID, an exact course code/name, or a unique case-insensitive name fragment. Ambiguous fragments fail and return candidate IDs instead of selecting the first match.

Examples:

```bash
./quercus status
./quercus courses --state all
./quercus announcements 12345 --since 7d
./quercus assignments MAT101 --bucket upcoming
./quercus assignments MAT101 --details --json
./quercus modules MAT101
./quercus files MAT101 --since 2d
./quercus pages MAT101 --search recording
./quercus page MAT101 lecture-recordings
./quercus grades MAT101
./quercus download MAT101 67890 --out ~/Downloads/quercus
```

`--since` accepts a positive integer followed by `m`, `h`, `d`, or `w`.

`files` and `pages` report a discovery source and completeness. Normal Canvas collection results are
`collection (complete)`. For the narrowly recognized Canvas responses indicating that a course has
disabled a collection, they inspect only explicit `File` or `Page` references in visible modules and
report `modules (incomplete)` with a reason. Module fallback is bounded and cannot discover unlinked
or orphaned course objects.

## Privacy and data minimization

- Course lists expose only the authenticated user's enrollments and course metadata.
- `assignments` asks Canvas only for the current API caller's `submission` projection.
- `grades` queries `user_id=self&type[]=StudentEnrollment` and emits only posted/current grade fields. Fields whose names represent unposted grades are never projected.
- Announcement/page HTML is converted locally to plain text. HTTP(S) links are extracted locally and never opened.
- File listings return metadata only. Content is fetched only by an explicit `download COURSE FILE_ID` command.
- Downloads are capped at 100 MiB, written with mode `0600` inside a mode-`0700` directory, reject symlink output paths, and refuse overwrite unless `--force` is passed.

## Read-only network allowlist

JSON access is limited to the following endpoint shapes:

```text
GET /api/v1/users/self/profile
GET /api/v1/courses
GET /api/v1/courses/:course_id
GET /api/v1/announcements
GET /api/v1/courses/:course_id/assignments
GET /api/v1/courses/:course_id/modules
GET /api/v1/courses/:course_id/modules/:module_id/items
GET /api/v1/courses/:course_id/files
GET /api/v1/courses/:course_id/files/:file_id
GET /api/v1/courses/:course_id/pages
GET /api/v1/courses/:course_id/pages/:page
GET /api/v1/courses/:course_id/enrollments
```

Canvas `Link: rel="next"` pagination is followed only after revalidating the exact host and endpoint allowlist. Request counts, pages, items, retries, response bytes, file bytes, and retry delays are bounded.

## Local state

Defaults:

```text
~/.local/state/quercus/session.json
~/.local/state/quercus/session.lock
~/.local/state/quercus/browser-profile/
```

Testing/isolated-deployment overrides:

```text
QUERCUS_SESSION_FILE
QUERCUS_SESSION_LOCK_FILE
QUERCUS_BROWSER_PROFILE
QUERCUS_VIMBROWSER_CLI
```

`quercus logout` removes helper-owned local cookies and the dedicated browser profile. It does not revoke U of T server-side sessions or sign out unrelated browsers.

## JSON and exit codes

Successful JSON has `schemaVersion: 1` and a `data` value. Structured errors use the same version and an `error` object. Credentials and cookie names/values are never included.

| Code | Meaning |
|---:|---|
| 0 | Success, including an empty result set |
| 1 | Unexpected helper failure |
| 2 | Invalid CLI usage or ambiguous course reference |
| 3 | Login/session interaction required |
| 4 | Session rejected or belongs to another account |
| 5 | Quercus/network/protocol failure |
| 6 | Unsafe or invalid local file operation |
| 7 | Optional vimbrowser import failure |

## Development

```bash
uv sync --locked
uv run playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
