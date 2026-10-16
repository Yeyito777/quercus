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
