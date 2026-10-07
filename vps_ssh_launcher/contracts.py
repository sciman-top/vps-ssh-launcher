"""Shared launcher defaults, exit codes and remote-client interfaces."""

from __future__ import annotations

from typing import Any, Protocol

CONNECT_TIMEOUT = 8  # TCP + SSH handshake + auth (seconds)
CMD_TIMEOUT = 60  # Remote command timeout (seconds)
KEEPALIVE_INTERVAL = 30  # SSH keep-alive (seconds), 0 = disabled
CONNECT_RETRIES = 2  # Extra retries for transient errors
MIN_PORT = 1
MAX_PORT = 65535
MAX_REMOTE_EXIT_CODE = 255
DEFAULT_RUN_ALL_MAX_WORKERS = 32
RUN_ALL_OUTPUT_LIMIT = 64 * 1024
CHANNEL_POLL_INTERVAL = 0.01
CHANNEL_READ_BURST = 16
EXIT_OK = 0
EXIT_SSH_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_TIMEOUT = 3
EXIT_NETWORK_ERROR = 4
EXIT_CMD_ERROR = 5
__version__ = "1.1.1"


class RemoteCommandClient(Protocol):
    def exec_command(self, command: str) -> tuple[Any, Any, Any]: ...


class ClosableRemoteCommandClient(RemoteCommandClient, Protocol):
    def close(self) -> None: ...
