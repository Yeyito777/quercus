from __future__ import annotations


class QuercusError(Exception):
    exit_code = 1

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class UsageError(QuercusError):
    exit_code = 2


class SessionRequiredError(QuercusError):
    exit_code = 3


class SessionRejectedError(QuercusError):
    exit_code = 4


class NetworkError(QuercusError):
    exit_code = 5

    def __init__(
        self,
        message: str,
        *,
        details: dict | None = None,
        status_code: int | None = None,
        response_detail: str = "",
    ):
        super().__init__(message, details=details)
        self.status_code = status_code
        self.response_detail = response_detail


class UnsafeFileError(QuercusError):
    exit_code = 6


class BrowserImportError(QuercusError):
    exit_code = 7
