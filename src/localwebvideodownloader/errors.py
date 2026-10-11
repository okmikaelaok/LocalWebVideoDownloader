"""User-facing application errors.

Raw downloader errors can contain signed URLs, cookies, and other secrets.  These
exceptions keep the safe message separate from an optional redacted diagnostic.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for an expected, user-facing application failure."""

    exit_code = 1

    def __init__(self, message: str, *, diagnostic: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.diagnostic = diagnostic


class InvalidUrlError(AppError):
    exit_code = 2


class DependencyError(AppError):
    exit_code = 3


class NoMediaError(AppError):
    exit_code = 4


class ProtectedMediaError(AppError):
    exit_code = 5


class LiveStreamError(AppError):
    exit_code = 6


class AccessDeniedError(AppError):
    exit_code = 7


class NetworkError(AppError):
    exit_code = 8


class FormatUnavailableError(AppError):
    exit_code = 9


class DownloadCancelled(AppError):
    exit_code = 10

    def __init__(self) -> None:
        super().__init__("The download was cancelled. A resumable .part file may remain.")


class InspectionError(AppError):
    exit_code = 11


class DownloadError(AppError):
    exit_code = 12


class ValidationError(AppError):
    exit_code = 13
