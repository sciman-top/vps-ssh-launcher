"""Local-side transport for audit tools that run themselves on a VPS.

Three audit entrypoints (``cpa_status.py``, ``cpa_error_dump_forensics.py``,
``cpa_natural_traffic_audit.py``) each hand-assembled the same two things:
a base64 payload that must fit one Windows ``CreateProcess`` argv element, and
the ``ssh_tool.py ... run --command`` invocation that carries it. The pieces had
drifted -- ``cpa_error_dump_forensics.py`` had no size guard at all, so a future
expansion would have failed as the opaque ``WinError 206`` instead of a local
error. This module is the single source for both decisions.

Scope rules:

* **Local only.** Nothing here is projected to a host, and no remote runtime
  may import it; ``cpa_natural_traffic_audit.py`` keeps its own inline copy on
  purpose because its staged upload must execute with no repository imports.
* **Pure builders.** No socket, no subprocess, no reading of ``target.json``.
  Callers pass the payload and keep their own ``subprocess.run`` seam, so
  existing per-module patch targets stay valid.
* **Fail locally.** An oversized payload raises before any SSH process starts.
"""

from __future__ import annotations

import base64
import re
import zlib
from pathlib import Path

# Windows CreateProcess caps one command line at 32767 characters. The whole
# remote command rides as ONE argv element, and the command line also carries
# the interpreter path, ssh_tool.py and the launcher flags, so the ceiling
# belongs to the assembled command plus that overhead -- not to the payload
# alone. Both bounds are checked: the payload pre-check fails cheaply with an
# operator-facing message before a multi-megabyte string is built, and the
# command check is the real ceiling.
WINDOWS_COMMAND_LINE_LIMIT = 32767
# Interpreter + entrypoint + flags for a checkout or an installed console
# script; generously sized because those are absolute host paths.
LAUNCHER_ARGV_RESERVE = 4096
COMMAND_ARGV_BUDGET = WINDOWS_COMMAND_LINE_LIMIT - LAUNCHER_ARGV_RESERVE
PAYLOAD_ARGV_BUDGET = 24000
# Remote interpreter used to run the uploaded file. The audit tools run on
# stock Ubuntu hosts where ``python3`` is the only guaranteed name.
REMOTE_PYTHON = "python3"
# Remote-side staging location: this is a path inside the VPS, never a local
# temp file. B108 models a local mkstemp/mktemp race, which does not apply --
# the remote ``mktemp`` creates the file with mode 600 under ``umask 077`` and
# the payload is removed by the shell trap. ``nosec`` is the repo's convention
# for a reviewed, knowingly-bounded scanner finding.
REMOTE_TEMP_ROOT = "/tmp"  # nosec B108
# Sibling of the session temp file is removed by the remote ``trap``; keep the
# pattern single-quoted so the remote shell, not PowerShell, expands it.
REMOTE_TEMP_SUFFIX = ".XXXXXXXXXX.py"
# Two payload encodings are supported because both are already deployed and
# verified on live hosts. New callers should use the compressed form; the plain
# form exists so a tool whose remote command shape is pinned by its own tests
# does not have to change hosts to benefit from the shared budget guard.
ENCODING_ZLIB = "zlib+base64"
ENCODING_BASE64 = "base64"
# One path segment: no separators, no whitespace, no shell metacharacters.
_TEMP_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def assert_payload_budget(payload: str) -> str:
    """Fail closed when the payload alone already exceeds the argv budget."""
    if len(payload) > PAYLOAD_ARGV_BUDGET:
        raise ValueError(
            "remote payload exceeds the Windows CreateProcess command-line "
            f"budget ({len(payload)} > {PAYLOAD_ARGV_BUDGET}); the transport "
            "needs chunking or stdin support before this file can grow further"
        )
    return payload


def assert_command_budget(command: str) -> str:
    """Fail closed when the assembled remote command no longer fits one argv.

    This is the boundary that actually matters: the payload is only part of the
    argument, and the staging scaffolding is fixed per call site.
    """
    if len(command) > COMMAND_ARGV_BUDGET:
        raise ValueError(
            "remote command exceeds the Windows CreateProcess command-line "
            f"budget for one argv element ({len(command)} > {COMMAND_ARGV_BUDGET} "
            f"after reserving {LAUNCHER_ARGV_RESERVE} for the launcher argv); "
            "shrink the tool or move the payload to a chunked transfer"
        )
    return command


def encode_payload(source: bytes, *, encoding: str = ENCODING_ZLIB) -> str:
    """Return a single-line, single-quote-safe payload for one argv element.

    Compression is not cosmetic: the raw base64 of these tools already sits
    close to the argv ceiling. Neither form contains a newline or a single
    quote, so both can be embedded in a ``printf %s '<payload>'`` safely.
    """
    if encoding == ENCODING_BASE64:
        return assert_payload_budget(base64.b64encode(source).decode("ascii"))
    if encoding == ENCODING_ZLIB:
        return assert_payload_budget(
            base64.b64encode(zlib.compress(source, level=9)).decode("ascii")
        )
    raise ValueError(f"Unsupported payload encoding: {encoding!r}")


def encode_source_payload(source: bytes) -> str:
    """Compressed payload; the default for new callers."""
    return encode_payload(source, encoding=ENCODING_ZLIB)


def decode_source_expression(encoding: str = ENCODING_ZLIB) -> str:
    """Remote stage that turns the decoded payload into the source bytes.

    Returned as a pipe stage, so callers always write the result onward with
    ``> "$tmp"`` instead of special-casing each encoding.
    """
    if encoding == ENCODING_BASE64:
        return "cat"
    if encoding == ENCODING_ZLIB:
        return (
            f"{REMOTE_PYTHON} -c 'import sys,zlib;"
            "sys.stdout.buffer.write(zlib.decompress(sys.stdin.buffer.read()))'"
        )
    raise ValueError(f"Unsupported payload encoding: {encoding!r}")


def launcher_argv(
    *,
    repo_root: Path,
    command: str,
    profile: str | None = None,
    config: Path | None = None,
    idle_timeout_seconds: int | None = None,
    hard_timeout_seconds: int | None = None,
) -> list[str]:
    """Build the ``ssh_tool.py`` invocation for one remote command.

    Options that the launcher accepts only ``run`` are emitted after the
    subcommand; unset timeouts are omitted so the CLI defaults apply.
    """
    argv = [str(Path(repo_root) / "ssh_tool.py")]
    if config is not None:
        argv.extend(["--config", str(config)])
    if profile:
        argv.extend(["--profile", profile])
    # Pin the strict host-key policy explicitly. The launcher default is already
    # strict, but an audit tool must not inherit a future default flip silently:
    # every remote read here would keep working against an unverified host.
    argv.append("--strict-host-key-checking")
    argv.extend(["run", "--command", command])
    if idle_timeout_seconds is not None:
        argv.extend(["--command-timeout", str(idle_timeout_seconds)])
    if hard_timeout_seconds is not None:
        argv.extend(["--command-hard-timeout", str(hard_timeout_seconds)])
    return argv


def remote_temp_python_command(
    *,
    payload: str,
    temp_prefix: str,
    action: str,
    encoding: str = ENCODING_ZLIB,
) -> str:
    """Return the remote one-liner that stages and runs an uploaded script.

    ``umask 077`` plus the ``trap`` keep the payload private and remove it on
    every exit path, including a failing script. ``action`` is the command that
    runs the staged file (for example ``python3 "$tmp" --remote --json``).
    """
    assert_payload_budget(payload)
    if not _TEMP_PREFIX_RE.fullmatch(temp_prefix):
        # The prefix is interpolated into a remote shell word; admitting a
        # slash or space would let a caller name a path outside the staging
        # area or split the command.
        raise ValueError(f"Invalid remote temp prefix: {temp_prefix!r}")
    temp = f"{REMOTE_TEMP_ROOT}/{temp_prefix}{REMOTE_TEMP_SUFFIX}"
    # One decode for the plain base64 form; the compressed form adds a second,
    # zlib-aware stage. Both write through the same redirect so the trap and
    # the private temp file behave identically for every caller.
    decode = decode_source_expression(encoding)
    command = (
        f"umask 077; tmp=$(mktemp {temp}) || exit 1; "
        "trap 'rm -f -- \"$tmp\"' EXIT; "
        f"printf %s '{payload}' | base64 -d | {decode} > \"$tmp\" && {action}"
    )
    # The payload passed its own bound; this catches the assembled command,
    # where a real host path in the action or a longer staging prefix can
    # still push the single argv element over the ceiling.
    return assert_command_budget(command)
