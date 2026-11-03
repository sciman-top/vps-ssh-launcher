#!/usr/bin/env python3
"""Prune Cockpit providers whose host is retired by the CPA route manifest.

Dry-run is the default. ``--apply`` refuses while Cockpit is running, writes a
private backup outside the active registry, and removes only complete provider
entries whose hostname is listed in ``retired_hosts``. API keys and endpoint
paths are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

OK = 0
FINDINGS = 1
CANNOT_CHECK = 2
BLOCKED = 3

DEFAULT_COCKPIT_DIR = Path.home() / ".antigravity_cockpit"
DEFAULT_ROUTES = Path(__file__).parent / "remote" / "cpa_provider_routes.json"
DEFAULT_BACKUP_ROOT = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "vps-ssh-launcher"
    / "backups"
)
COCKPIT_PROCESS_NAMES = frozenset({"cockpit-tools.exe", "cockpit-cliproxy.exe"})


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_retired_hosts(path: Path) -> set[str]:
    routes = read_json(path)
    if not isinstance(routes, dict):
        raise ValueError("route manifest must be a mapping")
    raw = routes.get("retired_hosts")
    if not isinstance(raw, list):
        raise ValueError("route manifest retired_hosts must be a list")
    hosts = {
        value.strip().lower()
        for value in raw
        if isinstance(value, str) and value.strip()
    }
    if len(hosts) != len(raw):
        raise ValueError("route manifest retired_hosts must contain unique strings")
    return hosts


def provider_host(provider: dict[str, Any]) -> str | None:
    base_url = provider.get("baseUrl")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    host = urlsplit(base_url.strip()).hostname
    return host.lower() if host else None


def retired_providers(
    providers: list[Any], retired_hosts: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    removed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for provider in providers:
        if not isinstance(provider, dict):
            kept.append(provider)
            continue
        host = provider_host(provider)
        if host is not None and host in retired_hosts:
            removed.append(provider)
        else:
            kept.append(provider)
    return removed, kept


def running_cockpit_processes() -> list[str]:
    """Return only known Cockpit executable names, never full process output."""

    if os.name != "nt":
        return []
    try:
        # Fixed executable and argument vector; shell execution is disabled.
        result = subprocess.run(  # nosec B603 B607
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("cannot inspect running Cockpit processes") from exc
    if result.returncode != 0:
        raise RuntimeError("cannot inspect running Cockpit processes")
    running: list[str] = []
    for line in result.stdout.splitlines():
        name = line.split(",", 1)[0].strip().strip('"').lower()
        if name in COCKPIT_PROCESS_NAMES:
            running.append(name)
    return sorted(set(running))


def safe_provider_label(provider: dict[str, Any]) -> str:
    name = provider.get("name")
    if isinstance(name, str) and name.strip():
        return " ".join(name.split())[:80]
    return "?"


def atomic_write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def create_backup(path: Path, backup_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = backup_root / f"cockpit-provider-registry-{stamp}"
    backup_dir.mkdir(parents=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    backup = backup_dir / path.name
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    return backup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cockpit-dir",
        type=Path,
        default=DEFAULT_COCKPIT_DIR,
        help="Cockpit data directory",
    )
    parser.add_argument(
        "--routes",
        type=Path,
        default=DEFAULT_ROUTES,
        help="CPA route manifest containing retired_hosts",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=DEFAULT_BACKUP_ROOT,
        help="private backup root used by --apply",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="remove matching providers after creating a backup",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry_path = args.cockpit_dir / "codex_model_providers.json"
    try:
        retired_hosts = load_retired_hosts(args.routes)
        registry = read_json(registry_path)
        if isinstance(registry, list):
            providers = registry
        elif isinstance(registry, dict):
            providers = registry.get("providers")
        else:
            providers = None
        if not isinstance(providers, list):
            raise ValueError("provider registry must be a list or providers list")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"cannot read inputs: {exc}", file=sys.stderr)
        return CANNOT_CHECK

    removed, kept = retired_providers(providers, retired_hosts)
    for provider in removed:
        print(
            f"RETIRED_PROVIDER name={safe_provider_label(provider)} "
            f"host={provider_host(provider)}"
        )
    print(f"RETIRED_PROVIDERS count={len(removed)} total={len(providers)}")
    if not removed:
        print("COCKPIT_PROVIDER_PRUNE=NOOP")
        return OK
    if not args.apply:
        print("COCKPIT_PROVIDER_PRUNE=DRY_RUN")
        return OK

    try:
        running = running_cockpit_processes()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return BLOCKED
    if running:
        print(
            "REFUSE Cockpit is running; close it normally before --apply "
            f"processes={','.join(running)}",
            file=sys.stderr,
        )
        return BLOCKED

    backup = create_backup(registry_path, args.backup_root)
    if isinstance(registry, list):
        registry = kept
    else:
        registry["providers"] = kept
    atomic_write_json(registry_path, registry)

    verify = read_json(registry_path)
    if isinstance(verify, list):
        verify_providers = verify
    elif isinstance(verify, dict):
        verify_providers = verify.get("providers")
    else:
        verify_providers = None
    if not isinstance(verify_providers, list):
        shutil.copy2(backup, registry_path)
        print(
            "REFUSE post-write registry is unreadable; backup restored", file=sys.stderr
        )
        return FINDINGS
    remaining, _ = retired_providers(verify_providers, retired_hosts)
    if remaining:
        shutil.copy2(backup, registry_path)
        print("REFUSE retired providers remain; backup restored", file=sys.stderr)
        return FINDINGS

    print(f"BACKUP={backup}")
    print("COCKPIT_PROVIDER_PRUNE=APPLIED")
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
