#!/usr/bin/env python3
"""Plan exact retired-name cleanup across Cockpit and Codex catalog fields.

This complements ``cockpit_model_catalog_prune``: this tool is the only writer
for active provider/key catalogs and owns backup, closed-Cockpit admission and
atomic publication.  It never treats a retired name as sufficient reason to
remove an entire provider entry, account or key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.cockpit_file_ops import (
        atomic_write_json,
        create_cockpit_backup,
        restore_cockpit_backup,
        running_cockpit_processes,
    )
    from scripts.cockpit_model_catalog_prune import (
        COCKPIT_FILES,
        MODEL_LIST_KEYS,
        MODEL_MAP_KEYS,
        MODEL_OBJECT_ID_KEYS,
        load_retired_models,
        read_json,
    )
except ModuleNotFoundError:
    if __package__:
        raise
    from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]

    ensure_repo_imports()
    from scripts import cockpit_file_ops  # type: ignore[import-not-found]
    from scripts import (  # type: ignore[import-not-found]
        cockpit_model_catalog_prune as pruner,
    )

    atomic_write_json = cockpit_file_ops.atomic_write_json
    create_cockpit_backup = cockpit_file_ops.create_cockpit_backup
    restore_cockpit_backup = cockpit_file_ops.restore_cockpit_backup
    running_cockpit_processes = cockpit_file_ops.running_cockpit_processes
    COCKPIT_FILES = pruner.COCKPIT_FILES
    MODEL_LIST_KEYS = pruner.MODEL_LIST_KEYS
    MODEL_MAP_KEYS = pruner.MODEL_MAP_KEYS
    MODEL_OBJECT_ID_KEYS = pruner.MODEL_OBJECT_ID_KEYS
    load_retired_models = pruner.load_retired_models
    read_json = pruner.read_json

OK = 0
FINDINGS = 1
CANNOT_CHECK = 2
BLOCKED = 3
DEFAULT_PROVIDER_POLICY = Path(__file__).parent / "cockpit_local_provider_policy.json"
DEFAULT_BACKUP_ROOT = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "vps-ssh-launcher"
    / "backups"
)


def plan_json(value: Any, retired: set[str]) -> tuple[Any, dict[str, int]]:
    """Compute the exact provider/model prune subset without mutating input."""
    counts: dict[str, int] = {}

    def hit(model: str) -> None:
        if model not in retired:
            return
        counts[model] = counts.get(model, 0) + 1

    def walk(child: Any) -> Any:
        if isinstance(child, list):
            return [walk(item) for item in child]
        if not isinstance(child, dict):
            return child
        result: dict[str, Any] = {}
        for key, item in child.items():
            if key in MODEL_LIST_KEYS and isinstance(item, list):
                kept: list[Any] = []
                for value in item:
                    if isinstance(value, str) and value in retired:
                        hit(value)
                    else:
                        kept.append(value)
                result[key] = kept
            elif key == "models" and isinstance(item, list):
                kept_objects: list[Any] = []
                for value in item:
                    model = (
                        next(
                            (
                                value[id_key]
                                for id_key in MODEL_OBJECT_ID_KEYS
                                if isinstance(value.get(id_key), str)
                                and value[id_key] in retired
                            ),
                            None,
                        )
                        if isinstance(value, dict)
                        else None
                    )
                    if model is not None:
                        hit(model)
                    else:
                        kept_objects.append(walk(value))
                result[key] = kept_objects
            elif key in MODEL_MAP_KEYS and isinstance(item, dict):
                result[key] = {}
                for model_id, metadata in item.items():
                    if model_id in retired:
                        hit(model_id)
                    else:
                        result[key][model_id] = walk(metadata)
            else:
                result[key] = walk(item)
        return result

    return walk(value), counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cockpit-dir", type=Path, default=Path.home() / ".antigravity_cockpit"
    )
    parser.add_argument("--codex-dir", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--provider-policy", type=Path, default=DEFAULT_PROVIDER_POLICY)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "remove exact retired names after a private backup; refuses while "
            "Cockpit is running"
        ),
    )
    args = parser.parse_args(argv)
    try:
        retired = load_retired_models(args.provider_policy)
        paths = [
            args.cockpit_dir / name
            for name in COCKPIT_FILES
            if (args.cockpit_dir / name).is_file()
        ]
        active = {path: read_json(path) for path in paths}
        affected = {
            path: counts
            for path, document in active.items()
            if (counts := plan_json(document, retired)[1])
        }
        running = bool(running_cockpit_processes()) if affected else False
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"cannot plan local retired-model cleanup: {exc}", file=sys.stderr)
        print("COCKPIT_RETIRED_MODELS=NOT_CHECKED")
        return CANNOT_CHECK
    for path, counts in sorted(affected.items()):
        print(
            f"RETIRED_MODEL_FILE={path.name} "
            f"count={sum(counts.values())} "
            f"models={','.join(sorted(counts))}"
        )
    print(f"RETIRED_MODEL_REFERENCES={sum(sum(c.values()) for c in affected.values())}")
    print("COCKPIT_RETIRED_MODELS=" + ("UPDATE_REQUIRED" if affected else "NOOP"))
    if affected and args.apply and running:
        print("REFUSE Cockpit is running; close it normally before --apply")
        print("COCKPIT_RETIRED_MODELS_APPLY=BLOCKED")
        return BLOCKED
    if affected and args.apply:
        catalog_paths = list(affected)
        try:
            backup = create_cockpit_backup(
                catalog_paths, args.backup_root, "cockpit-retired-models"
            )
            try:
                for path in catalog_paths:
                    original = active[path]
                    cleaned, _ = plan_json(original, retired)
                    atomic_write_json(path, cleaned)
                for path in catalog_paths:
                    _, remaining = plan_json(read_json(path), retired)
                    if remaining:
                        raise ValueError("retired references remain")
            except Exception:
                restore_cockpit_backup(catalog_paths, backup)
                raise
            print(f"RETIRED_MODEL_BACKUP={backup}")
            print("COCKPIT_RETIRED_MODELS_APPLY=APPLIED")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"cannot apply retired-model cleanup: {exc}", file=sys.stderr)
            print("COCKPIT_RETIRED_MODELS_APPLY=FAILED")
            return CANNOT_CHECK
    elif affected and running:
        print("COCKPIT_RETIRED_MODELS_APPLY=DEFERRED_COCKPIT_RUNNING")
    elif affected:
        print("COCKPIT_RETIRED_MODELS_APPLY=READY")
    else:
        print("COCKPIT_RETIRED_MODELS_APPLY=NOOP")
    return OK if affected and args.apply else (FINDINGS if affected else OK)


if __name__ == "__main__":
    raise SystemExit(main())
