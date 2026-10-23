# Installations

The same `main` runs on every machine. Machine-specific settings live in the
untracked `config/local.env`, a bash fragment that `./quercus` sources when it
exists. Write each setting as `VAR="${VAR:-value}"` so an explicit environment
variable still wins. Without the file, the defaults in this repository apply.

`manifest.json` is read only when this repo is installed under an Exocortex
`external-tools/` directory (the Mac). whale runs it as a helper tool from
`helper-tools/quercus`, where `exo-manifest.json` styles it.

## whale (primary)

No `config/local.env`. Session state uses the built-in defaults.

## macOS Desktop Exocortex

Installed September 20, 2026 from the existing UofT workspace source.
This is an independent source repository/runtime; no credentials are committed.

- Manifest command: `quercus`.
- Python 3.13 isolated environment, installed from this project's pyproject.
- State stays in `~/Desktop/uoft/.private/quercus` to avoid divergent
  credentials. `config/local.env` there:

  ```bash
  umask 077
  uoft="${UOFT_ROOT:-$HOME/Desktop/uoft}"
  QUERCUS_SESSION_FILE="${QUERCUS_SESSION_FILE:-$uoft/.private/quercus/session.json}"
  QUERCUS_SESSION_LOCK_FILE="${QUERCUS_SESSION_LOCK_FILE:-$uoft/.private/quercus/session.lock}"
  QUERCUS_VIMBROWSER_CLI="${QUERCUS_VIMBROWSER_CLI:-vimbrowser-cli}"
  ```

- Browser operations use **local** `vimbrowser-cli`, not the old whale wrapper.
- Imported sessions do not auto-renew. Use `login --from-vimbrowser --tab ID`,
  or explicitly initialize `login --persistent` for a renewable isolated context.
- There are no background checks or new reminders.

Tests: `.venv/bin/python -m unittest discover -s tests`.

### October 15 upstream authentication update

- Upstream `447e946` integrated; existing local installation checkpointed at
  `local/pre-auth-update-20261015` (`6e18961`).
- Upstream now supports bounded automatic U of T re-login for **persistent**
  sessions, using Secrets and Duo's `own-login` protocol. This is not enabled
  merely by importing cookies from an ordinary authenticated tab.
- Upstream re-login is user-visible (focuses a helper tab) and performs MFA;
  do not invoke it when focus changes or that Duo protocol are disallowed.
  `QUERCUS_AUTOLOGIN=0` disables that fallback for safe silent-only probes.
- Silent context renewal closes its own helper tab without restoring stale
  focus; interactive login keeps upstream focus behavior. (Now on `main`.)

### October 22 reconciliation

The Mac-only commits (backup branch `mac-local`, `0412633`) were folded into
`main`. The Mac's `bin/quercus` wrapper became `config/local.env`, and
`manifest.json` now points at `./quercus`.
