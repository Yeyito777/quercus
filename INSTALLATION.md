# Local Exocortex installation

Installed September 20, 2026 from the existing UofT workspace source.
This is an independent source repository/runtime; no credentials are committed.

- Manifest command: `quercus`.
- Python 3.13 isolated environment, installed from this project's pyproject.
- Default state remains `~/Desktop/uoft/.private/quercus` to avoid divergent credentials.
- `UOFT_ROOT`, `QUERCUS_SESSION_FILE`, and `QUERCUS_SESSION_LOCK_FILE` can override it.
- Browser operations use **local** `vimbrowser-cli`, not the old whale wrapper.
- Existing imported session was renewed from authenticated local tab 84.
- Imported sessions do not auto-renew. Use `login --from-vimbrowser --tab ID`,
  or explicitly initialize `login --persistent` for a renewable isolated context.
- There are no background checks or new reminders.

Tests: `.venv/bin/python -m unittest discover -s tests`.

## October 15 upstream authentication update

- Upstream `447e946` integrated; existing local installation checkpointed at
  `local/pre-auth-update-20261015` (`6e18961`).
- Upstream now supports bounded automatic U of T re-login for **persistent**
  sessions, using Secrets and Duo's `own-login` protocol. This is not enabled
  merely by importing cookies from an ordinary authenticated tab.
- Upstream re-login is user-visible (focuses a helper tab) and performs MFA;
  do not invoke it when focus changes or that Duo protocol are disallowed.
  `QUERCUS_AUTOLOGIN=0` disables that fallback for safe silent-only probes.
- Local fix: silent context renewal closes its own helper tab without restoring
  stale focus; interactive login retains upstream focus behavior.
- Existing private session and state paths are unchanged. No daemon, watcher,
  enrollment, credential migration, or browser restart was installed.
