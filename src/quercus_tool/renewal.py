from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from .browser_import import VimbrowserAuthenticator
from .client import CanvasClient
from .errors import SessionRejectedError, SessionRequiredError
from .paths import lock_path, session_path
from .session import CanvasSession
from .storage import atomic_write_json, exclusive_lock, read_private_json


def validate_session(session: CanvasSession) -> tuple[CanvasClient, dict]:
    client = CanvasClient(session)
    profile = session.assert_account(client.profile())
    return client, profile


def _read_saved(path: Path) -> CanvasSession:
    try:
        raw = read_private_json(path)
    except FileNotFoundError:
        raise SessionRequiredError("Quercus is not logged in; run `quercus login --persistent`") from None
    return CanvasSession.from_dict(raw)


def load_or_refresh_session(
    *,
    path: Path | None = None,
    lock: Path | None = None,
    validator: Callable[[CanvasSession], tuple[CanvasClient, dict]] = validate_session,
    vimbrowser_authenticator: VimbrowserAuthenticator | None = None,
) -> tuple[CanvasSession, CanvasClient, dict]:
    target = path or session_path()
    lock_target = lock or lock_path()
    session = _read_saved(target)
    try:
        client, profile = validator(session)
        return session, client, profile
    except SessionRejectedError:
        pass

    with exclusive_lock(lock_target):
        # A concurrent process may already have refreshed and atomically replaced it.
        session = _read_saved(target)
        try:
            client, profile = validator(session)
            return session, client, profile
        except SessionRejectedError:
            if session.renewal_mode != "vimbrowser":
                raise SessionRequiredError(
                    "the imported Quercus session expired; run `quercus login --persistent`"
                ) from None
        authenticator = vimbrowser_authenticator or VimbrowserAuthenticator()
        renewed = authenticator.acquire(
            interactive=False,
            expected_user_id=int(session.user["id"]),
        )
        client, profile = validator(renewed)
        atomic_write_json(target, asdict(renewed))
        return renewed, client, profile
