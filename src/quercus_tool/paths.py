from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "quercus"


def session_path() -> Path:
    override = os.environ.get("QUERCUS_SESSION_FILE")
    return Path(override).expanduser() if override else state_dir() / "session.json"


def lock_path() -> Path:
    override = os.environ.get("QUERCUS_SESSION_LOCK_FILE")
    return Path(override).expanduser() if override else state_dir() / "session.lock"


def vimbrowser_cli() -> str:
    return os.environ.get("QUERCUS_VIMBROWSER_CLI", "vimbrowser-cli")


def vimbrowser_context() -> str:
    return os.environ.get("QUERCUS_VIMBROWSER_CONTEXT", "quercus-helper")
