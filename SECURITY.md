# Quercus helper security contract

`quercus` is an unofficial, user-session-authenticated, read-only client for a user-owned University of Toronto Canvas account.

## Authentication boundary

- The recommended `login --persistent` flow creates a dedicated Chromium profile beneath the private Quercus state directory. The account owner completes U of T sign-in and Duo in that profile when required.
- The profile stores Quercus, U of T identity-provider, and possibly Duo/browser state. These credentials have the practical sensitivity of a signed-in browser and may permit actions far beyond this CLI.
- `session.json` contains only the cookies currently applicable to `https://q.utoronto.ca/`, a minimal account identity projection, and renewal metadata. It does not contain a password or Duo secret.
- `login --from-vimbrowser` copies only cookies visible to the exact Quercus origin and does not copy the broad browser/SSO profile. It is short lived and cannot renew itself.
- Credentials are never accepted through argv, printed, logged, placed in errors, or emitted as JSON.
- The state root and browser profile are mode `0700`; `session.json` and its lock are mode `0600`. State is not separately encrypted at rest, so compromise of the owner's Unix account can expose it.
- `quercus logout` deletes helper-owned local credentials. It does not revoke U of T server-side sessions or affect unrelated browsers.

## Read-only application boundary

- The CLI exposes only inspection and explicit download commands.
- Direct Canvas calls use only HTTP `GET`.
- API paths are matched against a closed route allowlist. There is no generic URL, API, GraphQL, JavaScript, request-replay, or method escape hatch.
- POST, PUT, PATCH, DELETE, assignment submission, quiz participation, discussions, invitation acceptance, module completion, enrollment changes, and grade mutation are absent.
- The current user's course enrollment, assignment submission, and grade projections are used. The grade output allowlists posted/current fields and never forwards `unposted_*` grade fields.

## Network boundary

- JSON calls are restricted to exact HTTPS origin `https://q.utoronto.ca`, default port, no userinfo, and allowlisted decoded path shapes.
- API redirects are disabled. Canvas pagination URLs are revalidated against the same origin and path allowlist before use.
- File metadata is fetched through an allowlisted course-context endpoint before content download.
- The initial content request must use an allowlisted Quercus file-download route. Storage redirects are limited to HTTPS Canvas User Content, Instructure Cloud Gate, Instructure, Instructure Media, Amazon S3/AWS, or CloudFront host suffixes. Browser cookies are scoped to U of T and are not sent to those storage hosts.
- Request count, pages, result counts, retries, retry delays, JSON bytes, file bytes, redirects, and timeouts are bounded.

## Local and content boundary

- Structured credential files are private regular files and are replaced atomically under a process lock.
- Browser-profile deletion rejects symlinks and non-directories.
- Downloads require an explicit course and file ID, are capped at 100 MiB, use sanitized server-provided filenames, reject symlink output directories/files, use mode `0600`, and do not overwrite without `--force`.
- Announcement/page HTML conversion and link extraction are entirely local. Extracted links are not opened, resolved remotely, or fetched.
- This design does not protect against malware or another process already running as the account owner's Unix user.

## Support and revocation limits

Quercus's Canvas API and U of T browser-session behavior may change without notice. Use only the owner's account and human-scale rates. If compromise is suspected, use U of T account/session controls; local logout alone is not server-side revocation.
