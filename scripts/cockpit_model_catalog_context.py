#!/usr/bin/env python3
"""Audit the context pins in Cockpit's authoritative model-catalog editor.

The managed desktop catalog is generated from
``.cockpit-experimental-model-catalog-config.json``.  Checking only the
generated catalog can therefore miss stale editor values that will return on
the next rebuild.  This tool performs no writes and never opens credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.cockpit_file_ops import (
        running_cockpit_processes,
        update_model_context_pins,
    )
except ModuleNotFoundError:
    if __package__:
        raise
    from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]

    ensure_repo_imports()
    from scripts import cockpit_file_ops  # type: ignore[import-not-found]

    running_cockpit_processes = cockpit_file_ops.running_cockpit_processes
    update_model_context_pins = cockpit_file_ops.update_model_context_pins

OK = 0
FINDINGS = 1
CANNOT_CHECK = 2
BLOCKED = 3
FIELDS = ("context_window", "max_context_window", "auto_compact_token_limit")
DEFAULT_CODEX_DIR = Path.home() / ".codex"
DEFAULT_OVERRIDES = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "codex-model-context-overrides.json"
)
DEFAULT_BACKUP_ROOT = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "vps-ssh-launcher"
    / "backups"
)


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return value


def model_pins(document: dict[str, Any], id_key: str) -> dict[str, dict[str, Any]]:
    models = document.get("models")
    if not isinstance(models, list):
        raise ValueError(f"models must be a list, keyed by {id_key}")
    pins: dict[str, dict[str, Any]] = {}
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("model entries must be objects")
        model_id = model.get(id_key)
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError(f"model entries require a non-empty {id_key}")
        if model_id in pins:
            raise ValueError(f"duplicate model id: {model_id}")
        pins[model_id] = model
    return pins


def collect_findings(
    codex_dir: Path, overrides_path: Path = DEFAULT_OVERRIDES
) -> tuple[list[str], list[str]]:
    editor_path = codex_dir / ".cockpit-experimental-model-catalog-config.json"
    catalog_path = codex_dir / "cockpit-model-catalog.json"
    meta_path = codex_dir / "cockpit-model-catalog.meta.json"
    missing = [
        path.name
        for path in (editor_path, catalog_path, meta_path)
        if not path.is_file()
    ]
    if missing:
        return [], [f"missing required projection: {name}" for name in missing]
    editor = model_pins(read_object(editor_path), "model_id")
    catalog = model_pins(read_object(catalog_path), "slug")
    contract = model_pins(read_object(overrides_path), "slug")
    failures: list[str] = []
    for slug, expected in contract.items():
        for field in FIELDS:
            value = expected.get(field)
            if type(value) is not int or value <= 0:
                failures.append(f"{slug}: invalid contract {field}")
            for label, actual in (("editor", editor), ("catalog", catalog)):
                model = actual.get(slug)
                if model is None:
                    if field == FIELDS[0]:
                        failures.append(f"{slug}: missing {label} entry")
                    continue
                if label == "editor" and field == "max_context_window":
                    continue
                if type(model.get(field)) is not int or model.get(field) != value:
                    failures.append(
                        f"{slug}: {label} {field}={model.get(field)} expected={value}"
                    )
    catalog_bytes = catalog_path.read_bytes()
    metadata = read_object(meta_path)
    expected_hash = hashlib.sha256(catalog_bytes).hexdigest()
    if metadata.get("catalogHash") != expected_hash:
        failures.append("catalog metadata hash does not match catalog bytes")
    return sorted(editor), failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--codex-dir",
        type=Path,
        default=DEFAULT_CODEX_DIR,
        help="Codex home containing Cockpit's catalog projections",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=DEFAULT_OVERRIDES,
        help="repository context-pin contract",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "align the editor and generated catalog after an atomic backup; "
            "refuses while Cockpit is running"
        ),
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=DEFAULT_BACKUP_ROOT,
        help="private backup root used by --apply",
    )
    args = parser.parse_args(argv)
    if args.apply:
        try:
            if running_cockpit_processes():
                print(
                    "REFUSE Cockpit is running; close it normally before --apply",
                    file=sys.stderr,
                )
                return BLOCKED
            if not collect_findings(args.codex_dir, args.overrides)[1]:
                print("CONTEXT_PROJECTION_APPLY=NOOP")
            else:
                backup, editor_changed, catalog_changed = update_model_context_pins(
                    args.codex_dir / ".cockpit-experimental-model-catalog-config.json",
                    args.codex_dir / "cockpit-model-catalog.json",
                    args.codex_dir / "cockpit-model-catalog.meta.json",
                    args.overrides,
                    args.backup_root,
                )
                print(f"CONTEXT_PROJECTION_BACKUP={backup}")
                print(
                    f"CONTEXT_PROJECTION_APPLY=OK editor_changed={editor_changed} "
                    f"catalog_changed={catalog_changed}"
                )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"cannot apply context projection: {exc}", file=sys.stderr)
            return CANNOT_CHECK
    try:
        editor_ids, failures = collect_findings(args.codex_dir, args.overrides)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        RuntimeError,
        UnicodeError,
    ) as exc:
        print(f"cannot audit model catalog context: {exc}", file=sys.stderr)
        editor_ids = None
        failures = []
    try:
        sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    if editor_ids is None:
        print("COCKPIT_MODEL_CONTEXT=NOT_CHECKED")
        return CANNOT_CHECK
    print(f"EDITOR_MODEL_COUNT={len(editor_ids)}")
    for failure in failures:
        print(f"CONTEXT_PROJECTION_MISMATCH={failure}")
    print(
        "COCKPIT_MODEL_CONTEXT="
        + ("FAIL" if failures else "PASS")
        + " authority=editor+generated_catalog"
    )
    return FINDINGS if failures else OK


if __name__ == "__main__":
    raise SystemExit(main())
