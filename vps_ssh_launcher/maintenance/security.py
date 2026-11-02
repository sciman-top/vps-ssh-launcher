"""Local evidence redaction for maintenance errors and receipts."""

from __future__ import annotations

import re
from typing import Any

from ..config import user_config_path


_SENSITIVE_FIELD = r"(?:password|passwd|token|api[_-]?key|secret|private[_-]?key)"
_SENSITIVE_ASSIGNMENT = re.compile(
    rf"(?i)(\b{_SENSITIVE_FIELD}\b\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)(?:[ \t,}}])?"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/@\s]+@")
_ENV_VALUE = re.compile(r"(?i)(\bpassword_env\b\s*[:=]\s*)([A-Za-z_][A-Za-z0-9_]*)")


def _redact_text(value: str) -> str:
    def replace_assignment(match: re.Match[str]) -> str:
        trailing = match.group(0)[-1]
        suffix = trailing if trailing in " \t,}" else ""
        return match.group(1) + "<redacted>" + suffix

    value = _SENSITIVE_ASSIGNMENT.sub(replace_assignment, value)
    value = _BEARER.sub(r"\1<redacted>", value)
    value = _URL_USERINFO.sub(r"\1<redacted>@", value)
    value = _ENV_VALUE.sub(r"\1<redacted>", value)
    # A target path itself is not a credential, but exposing a complete user
    # profile path in shared receipts and logs needlessly discloses the host
    # layout. The exact local target path is replaced by a stable label.
    target_path = str(user_config_path().expanduser())
    value = value.replace(target_path, "<private-target-config>")
    return value


def redact_evidence(value: Any) -> str:
    """Return user-visible maintenance evidence without raw exception text."""
    if isinstance(value, BaseException):
        detail = _redact_text(str(value))
    else:
        detail = _redact_text(str(value))
    return detail[:4096]
