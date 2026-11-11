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
import sys
from pathlib import Path
from typing import Any, Sequence, cast
from urllib.parse import urlsplit

from scripts.cockpit_file_ops import (
    atomic_write_json,
    create_cockpit_backup,
    read_json,
    restore_cockpit_backup,
    running_cockpit_processes,
)

OK = 0
FINDINGS = 1
CANNOT_CHECK = 2
BLOCKED = 3

DEFAULT_COCKPIT_DIR = Path.home() / ".antigravity_cockpit"
DEFAULT_PROVIDER_POLICY = Path(__file__).parent / "cockpit_local_provider_policy.json"
DEFAULT_BACKUP_ROOT = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "vps-ssh-launcher"
    / "backups"
)


def load_retired_hosts(path: Path) -> set[str]:
    policy = read_json(path)
    if not isinstance(policy, dict):
        raise ValueError("provider policy must be a mapping")
    raw = policy.get("retired_hosts")
    if not isinstance(raw, list):
        raise ValueError("provider policy retired_hosts must be a list")
    hosts = {
        value.strip().lower()
        for value in raw
        if isinstance(value, str) and value.strip()
    }
    if len(hosts) != len(raw):
        raise ValueError("provider policy retired_hosts must contain unique strings")
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


def safe_provider_label(provider: dict[str, Any]) -> str:
    name = provider.get("name")
    if isinstance(name, str) and name.strip():
        return " ".join(name.split())[:80]
    return "?"


def create_backup(path: Path, backup_root: Path) -> Path:
    backup_dir: Path = create_cockpit_backup(
        [path], backup_root, "cockpit-provider-registry"
    )
    return backup_dir / path.name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cockpit-dir",
        type=Path,
        default=DEFAULT_COCKPIT_DIR,
        help="Cockpit data directory",
    )
    parser.add_argument(
        "--provider-policy",
        type=Path,
        default=DEFAULT_PROVIDER_POLICY,
        help="local Cockpit provider retirement policy",
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
        retired_hosts = load_retired_hosts(args.provider_policy)
        registry = read_json(registry_path)
        if isinstance(registry, list):
            providers_obj: object = registry
        elif isinstance(registry, dict):
            providers_obj = registry.get("providers")
        else:
            providers_obj = None
        if not isinstance(providers_obj, list):
            raise ValueError("provider registry must be a list or providers list")
        providers = cast(list[Any], providers_obj)
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
        verify_obj: object = verify
    elif isinstance(verify, dict):
        verify_obj = verify.get("providers")
    else:
        verify_obj = None
    if not isinstance(verify_obj, list):
        restore_cockpit_backup([registry_path], backup.parent)
        print(
            "REFUSE post-write registry is unreadable; backup restored", file=sys.stderr
        )
        return FINDINGS
    verify_providers = cast(list[Any], verify_obj)
    remaining, _ = retired_providers(verify_providers, retired_hosts)
    if remaining:
        restore_cockpit_backup([registry_path], backup.parent)
        print("REFUSE retired providers remain; backup restored", file=sys.stderr)
        return FINDINGS

    print(f"BACKUP={backup}")
    print("COCKPIT_PROVIDER_PRUNE=APPLIED")
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
