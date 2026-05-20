from __future__ import annotations


class ProjectFlowError(Exception):
    """Base exception for ProjectFlow."""


class ConfigError(ProjectFlowError):
    """Raised when configuration is missing or invalid."""


class AuthError(ProjectFlowError):
    """Raised when Microsoft authentication cannot complete."""


class GraphError(ProjectFlowError):
    """Raised when Microsoft Graph rejects an operation."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LockError(ProjectFlowError):
    """Raised when a local file cannot be edited because it is locked."""


class OutlookError(ProjectFlowError):
    """Raised when local Outlook automation fails."""


class ProjectNumberError(ProjectFlowError, ValueError):
    """Raised when a project number is malformed."""


class ProjectCreationError(ProjectFlowError):
    """Raised when project creation cannot complete."""
