#!/usr/bin/env python3
"""Prune retired model IDs from active Cockpit and Codex catalog projections.

Dry-run is the default. ``--apply`` refuses while Cockpit is running, creates
private backups, removes only exact retired model IDs from catalog fields, and
atomically rewrites changed JSON files. The managed desktop catalog hash is
updated when that projection changes. Credentials, provider entries, account
records, and unrelated models are never removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

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
DEFAULT_CODEX_DIR = Path.home() / ".codex"
DEFAULT_PROVIDER_POLICY = Path(__file__).parent / "cockpit_local_provider_policy.json"
DEFAULT_BACKUP_ROOT = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "vps-ssh-launcher"
    / "backups"
)
MODEL_LIST_KEYS = frozenset({"modelCatalog", "api_model_catalog"})
MODEL_MAP_KEYS = frozenset(
    {"modelCapabilities", "api_model_vision_support", "api_model_context_windows"}
)
MODEL_OBJECT_ID_KEYS = ("model_id", "slug", "id", "display_name")

COCKPIT_FILES = ("codex_model_providers.json", "codex_accounts.json")
CODEX_FILES = (
    ".cockpit-experimental-model-catalog-config.json",
    ".codex-global-state.json",
    "cockpit-model-catalog.json",
)
CATALOG_META = "cockpit-model-catalog.meta.json"


def load_retired_models(path: Path) -> set[str]:
    policy = read_json(path)
    if not isinstance(policy, dict):
        raise ValueError("provider policy must be a mapping")
    raw = policy.get("retired_models", [])
    if not isinstance(raw, list):
        raise ValueError("provider policy retired_models must be a list")
    models = {
        value.strip() for value in raw if isinstance(value, str) and value.strip()
    }
    if len(models) != len(raw):
        raise ValueError("provider policy retired_models must contain unique strings")
    return models


def _prune_model_object_list(
    items: list[Any], retired_models: set[str]
) -> tuple[list[Any], int]:
    kept: list[Any] = []
    removed = 0
    for item in items:
        if isinstance(item, dict) and any(
            item.get(key) in retired_models for key in MODEL_OBJECT_ID_KEYS
        ):
            removed += 1
        else:
            kept.append(item)
    return kept, removed


def prune_json(value: Any, retired_models: set[str]) -> tuple[Any, int]:
    """Remove exact retired IDs from known catalog fields in one JSON tree."""

    removed = 0
    if isinstance(value, list):
        kept: list[Any] = []
        for item in value:
            pruned, count = prune_json(item, retired_models)
            removed += count
            kept.append(pruned)
        return kept, removed
    if not isinstance(value, dict):
        return value, 0

    out: dict[str, Any] = {}
    for key, child in value.items():
        if key in MODEL_LIST_KEYS and isinstance(child, list):
            kept = [item for item in child if item not in retired_models]
            removed += len(child) - len(kept)
            out[key] = kept
            continue
        if key == "models" and isinstance(child, list):
            pruned_models, count = _prune_model_object_list(child, retired_models)
            removed += count
            out[key] = pruned_models
            continue
        if key in MODEL_MAP_KEYS and isinstance(child, dict):
            blocked = set(child) & retired_models
            removed += len(blocked)
            out[key] = {k: v for k, v in child.items() if k not in blocked}
            continue
        pruned, count = prune_json(child, retired_models)
        removed += count
        out[key] = pruned
    return out, removed


def active_paths(cockpit_dir: Path, codex_dir: Path) -> list[Path]:
    paths = [cockpit_dir / name for name in COCKPIT_FILES]
    paths.extend(codex_dir / name for name in CODEX_FILES)
    return [path for path in paths if path.is_file()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cockpit-dir",
        type=Path,
        default=DEFAULT_COCKPIT_DIR,
        help="Cockpit data directory",
    )
    parser.add_argument(
        "--codex-dir",
        type=Path,
        default=DEFAULT_CODEX_DIR,
        help="Codex data directory",
    )
    parser.add_argument(
        "--provider-policy",
        type=Path,
        default=DEFAULT_PROVIDER_POLICY,
        help="local Cockpit retirement policy",
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
        help="remove matching models after creating a backup",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        retired_models = load_retired_models(args.provider_policy)
        paths = active_paths(args.cockpit_dir, args.codex_dir)
        documents = {path: read_json(path) for path in paths}
        pruned: dict[Path, Any] = {}
        changes: dict[Path, int] = {}
        for path, document in documents.items():
            updated, count = prune_json(document, retired_models)
            if count:
                pruned[path] = updated
                changes[path] = count
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"cannot read inputs: {exc}", file=sys.stderr)
        return CANNOT_CHECK

    for path, count in sorted(changes.items()):
        print(f"RETIRED_MODEL_REFERENCE file={path.name} count={count}")
    print(
        f"RETIRED_MODEL_REFERENCES total={sum(changes.values())} "
        f"models={','.join(sorted(retired_models))}"
    )
    if not changes:
        print("COCKPIT_MODEL_CATALOG_PRUNE=NOOP")
        return OK
    if not args.apply:
        print("COCKPIT_MODEL_CATALOG_PRUNE=DRY_RUN")
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

    backup_paths = list(changes)
    meta_path = args.codex_dir / CATALOG_META
    catalog_path = args.codex_dir / "cockpit-model-catalog.json"
    if catalog_path in changes and meta_path.is_file():
        backup_paths.append(meta_path)
    backup_dir = create_cockpit_backup(
        backup_paths, args.backup_root, "cockpit-model-catalog"
    )

    published: list[Path] = []
    try:
        for path, document in pruned.items():
            atomic_write_json(path, document)
            published.append(path)

        if catalog_path in changes and meta_path.is_file():
            meta = read_json(meta_path)
            if not isinstance(meta, dict):
                raise ValueError("catalog metadata must be an object")
            meta["catalogHash"] = hashlib.sha256(
                catalog_path.read_bytes()
            ).hexdigest()
            atomic_write_json(meta_path, meta)
            published.append(meta_path)

        for path in changes:
            _, remaining = prune_json(read_json(path), retired_models)
            if remaining:
                raise ValueError("retired model references remain")
        if meta_path in published:
            expected_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
            if read_json(meta_path).get("catalogHash") != expected_hash:
                raise ValueError("catalog hash verification failed")
    except Exception as exc:
        # The name-keyed backups are exactly the files transformed above.
        # Restore the metadata too; an earlier implementation omitted it.
        try:
            restore_cockpit_backup(backup_paths, backup_dir)
            restored = True
        except Exception:
            restored = False
        print(
            "REFUSE catalog prune failed; backups restored"
            if restored
            else "REFUSE catalog prune failed; manual recovery from backup required",
            file=sys.stderr,
        )
        print(f"CATALOG_PRUNE_FAILURE={type(exc).__name__}", file=sys.stderr)
        return CANNOT_CHECK if not restored else FINDINGS

    print(f"BACKUP={backup_dir}")
    print("COCKPIT_MODEL_CATALOG_PRUNE=APPLIED")
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
