"""Fail-closed catalog consistency checks for the CPA route manifest.

**Source of truth**: ``scripts/remote/cpa_provider_routes.json``. Several
enforcement points must agree with it:

* ``scripts/remote/cpa-admission.json`` -- the deployable lane config
  (``-Apply`` refuses on a mismatch, but only after a full gate run and an
  SSH round trip);
* ``tests/cpa_catalog_expectations.py`` -- the test oracle;
* the entry points that default to a model name (a retired name there fails
  at runtime, on the host, during an incident).

``docs/runbooks/cpa-catalog-change-checklist.md`` lists those places as a
manual six-step edit. This checker answers the same question locally in
milliseconds, so the hand edits become "run this, then fix what it prints".
It reads repository files only: no network, no remote host, no credential.

The manifest does not name admission lanes directly (``chatgpt-plus-oauth``
feeds the ``chatgpt-oauth`` lane, ``zhipu-plan`` the ``zhipu-coding-plan``
lane, ``plugin_routes`` the ``workbuddy-oauth`` lane), so that mapping lives
in ``LANE_SOURCES`` -- one table, validated in both directions, instead of the
same knowledge copied into four files.

Exit codes: 0 consistent, 1 drift found, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "remote" / "cpa_provider_routes.json"
ADMISSION_CONFIG = ROOT / "scripts" / "remote" / "cpa-admission.json"
EXPECTATIONS = ROOT / "tests" / "cpa_catalog_expectations.py"

# lane name -> (kind, source name in the manifest)
LANE_SOURCES: dict[str, tuple[str, str]] = {
    "chatgpt-oauth": ("oauth_route", "chatgpt-plus-oauth"),
    "antigravity-oauth": ("oauth_route", "antigravity-oauth"),
    "zhipu-coding-plan": ("provider", "zhipu-plan"),
    "workbuddy-oauth": ("plugin", "workbuddy"),
}

TOMBSTONE_KEYS = (
    "oauth_exclusions",
    "codex_api_key_exclusions",
    "claude_oauth_exclusions",
)

# The admission lane whose aliases the operator entry points must be able to
# select. Other lanes are reached through their own tooling.
CHOICE_LANE = "chatgpt-oauth"

# Entry points whose advertised model choices must stay live aliases.
CHOICE_SITES = (
    ROOT / "scripts" / "cpa_recovery_workflow.ps1",
    ROOT / "scripts" / "cpa_reset_event_bridge.ps1",
    ROOT / "scripts" / "hg03_contract.py",
    ROOT / "scripts" / "cpa_stream_acceptance.py",
)


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        # Tests point CHOICE_SITES / MANIFEST at temp files outside the repo.
        return path.as_posix()


def _load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{_relative(path)}: unreadable ({exc})")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"{_relative(path)}: invalid JSON ({exc})")
        return {}
    if not isinstance(loaded, dict):
        errors.append(f"{_relative(path)}: expected a JSON object")
        return {}
    return loaded


def _aliases(route: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for model in route.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        name = model.get("alias") or model.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _by_name(entries: Any) -> dict[str, dict[str, Any]]:
    return {
        entry["name"]: entry
        for entry in entries or []
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


def plugin_lane_models(manifest: dict[str, Any], provider: str) -> list[str]:
    """Widest realm model list for a plugin provider, in manifest order.

    The admission lane serves every realm, so the canonical lane list is the
    widest realm (intl supersets cn); taking a union instead would invent an
    order the admission config never uses.
    """
    widest: list[str] = []
    for route in manifest.get("plugin_routes", []) or []:
        if not isinstance(route, dict) or route.get("provider") != provider:
            continue
        models = [
            name for name in route.get("models", []) or [] if isinstance(name, str)
        ]
        if len(models) > len(widest):
            widest = models
    return widest


def derive_lanes(manifest: dict[str, Any], errors: list[str]) -> dict[str, list[str]]:
    """Resolve every mapped admission lane to its manifest alias list."""
    oauth = _by_name(manifest.get("oauth_routes"))
    providers = _by_name(manifest.get("providers"))
    derived: dict[str, list[str]] = {}
    for lane, (kind, source) in LANE_SOURCES.items():
        if kind == "oauth_route":
            route = oauth.get(source)
            if route is None:
                errors.append(
                    f"LANE_SOURCES maps {lane!r} to missing oauth route {source!r}"
                )
                continue
            derived[lane] = _aliases(route)
        elif kind == "provider":
            route = providers.get(source)
            if route is None:
                errors.append(
                    f"LANE_SOURCES maps {lane!r} to missing provider {source!r}"
                )
                continue
            derived[lane] = _aliases(route)
        elif kind == "plugin":
            models = plugin_lane_models(manifest, source)
            if not models:
                errors.append(
                    f"LANE_SOURCES maps {lane!r} to missing plugin provider {source!r}"
                )
                continue
            derived[lane] = models
        else:  # pragma: no cover - the table literal above is exhaustive
            errors.append(f"LANE_SOURCES has an unknown kind {kind!r} for {lane!r}")
    return derived


def live_aliases(manifest: dict[str, Any], derived: dict[str, list[str]]) -> set[str]:
    """Every model name the manifest actually serves."""
    live = {name for models in derived.values() for name in models}
    for key in (
        "oauth_routes",
        "providers",
        "codex_api_key_routes",
        "claude_api_key_routes",
    ):
        for route in manifest.get(key, []) or []:
            if isinstance(route, dict):
                live.update(_aliases(route))
    live.update(str(name) for name in manifest.get("pooled_aliases", []) or [])
    return live


def tombstones(manifest: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in TOMBSTONE_KEYS:
        names.update(str(name) for name in manifest.get(key, []) or [])
    return names


def read_expectations(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Load the oracle module in its own namespace, without pytest.

    The module is a literal table with a few list()/sum() wrappers, so AST
    literal_eval is not enough. It is imported under a private module name so
    the checker works from any working directory and never touches the pytest
    session that also imports it.
    """
    spec = importlib.util.spec_from_file_location("_cpa_catalog_expectations", path)
    if spec is None or spec.loader is None:
        return {}, [f"{_relative(path)}: cannot be loaded"]
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive
        return {}, [f"{_relative(path)}: cannot be evaluated ({exc})"]
    return dict(vars(module)), []


def check() -> list[str]:
    errors: list[str] = []
    manifest = _load_json(MANIFEST, errors)
    if not manifest:
        return errors
    admission = _load_json(ADMISSION_CONFIG, errors)
    derived = derive_lanes(manifest, errors)
    live = live_aliases(manifest, derived)
    retired = tombstones(manifest)

    # 1. The deployable admission config carries exactly the derived lanes.
    deployed = {
        lane["name"]: list(lane.get("models", []))
        for lane in admission.get("lanes", []) or []
        if isinstance(lane, dict) and isinstance(lane.get("name"), str)
    }
    for lane in sorted(set(deployed) | set(derived)):
        want = derived.get(lane)
        got = deployed.get(lane)
        if want is None:
            errors.append(
                f"{_relative(ADMISSION_CONFIG)}: lane {lane!r} is not derived from "
                "the manifest (add it to LANE_SOURCES or drop it)"
            )
        elif got != want:
            errors.append(
                f"{_relative(ADMISSION_CONFIG)}: lane {lane!r} models {got} != "
                f"manifest {want}"
            )

    # 2. Cross-lane tombstones stay honest: a name served by one lane may
    # legitimately be excluded from another (that is the whole point of the
    # API-key exclusions), but a lane may never both serve and exclude a name.
    def aliases_of(key: str) -> set[str]:
        names: set[str] = set()
        for route in manifest.get(key, []) or []:
            if isinstance(route, dict):
                names.update(_aliases(route))
        return names

    lane_pairs = (
        ("oauth_routes", "oauth_exclusions"),
        ("codex_api_key_routes", "codex_api_key_exclusions"),
        ("claude_api_key_routes", "claude_oauth_exclusions"),
    )
    for served_key, excluded_key in lane_pairs:
        clash = aliases_of(served_key) & {
            str(name) for name in manifest.get(excluded_key, []) or []
        }
        for name in sorted(clash):
            errors.append(
                f"{served_key} serves {name!r} while {excluded_key} tombstones it"
            )

    # 3. The test oracle still describes the manifest it checks.
    oracle, oracle_errors = read_expectations(EXPECTATIONS)
    errors.extend(oracle_errors)
    oracle_lanes = oracle.get("ADMISSION_LANE_MODELS")
    if isinstance(oracle_lanes, dict):
        for lane in sorted(set(oracle_lanes) | set(derived)):
            if oracle_lanes.get(lane) != derived.get(lane):
                errors.append(
                    f"{_relative(EXPECTATIONS)}: ADMISSION_LANE_MODELS[{lane!r}] "
                    f"{oracle_lanes.get(lane)} != manifest {derived.get(lane)}"
                )
    else:
        errors.append(f"{_relative(EXPECTATIONS)}: ADMISSION_LANE_MODELS is missing")

    # 4. Entry points may not advertise a retired model name ...
    quoted = re.compile(r'"([A-Za-z0-9][A-Za-z0-9._-]{3,})"')
    for path in CHOICE_SITES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{_relative(path)}: unreadable ({exc})")
            continue
        for match in quoted.finditer(text):
            name = match.group(1)
            if name in retired and name not in live:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{_relative(path)}:{line}: retired model {name!r} is still "
                    "offered as a live choice"
                )

    # 5. ... and, in the other direction, they must offer every live alias of
    # CHOICE_LANE. Only this half was missing: when gpt-6.1-sol/terra were
    # revived on 2026-11-14 the manifest gained two aliases and the three
    # operator entry points silently kept offering the previous two, so an
    # operator could not select a name the gateway was serving.
    wanted = [name for name in derived.get(CHOICE_LANE, [])]
    for path in CHOICE_SITES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue  # already reported above
        offered = {match.group(1) for match in quoted.finditer(text)}
        missing = [name for name in wanted if name not in offered]
        if missing:
            errors.append(
                f"{_relative(path)}: does not offer live {CHOICE_LANE} alias(es) "
                f"{missing}; the manifest serves {wanted}"
            )
    return errors


def render(manifest: dict[str, Any]) -> str:
    derived = derive_lanes(manifest, [])
    lines = ["CATALOG_LANES (derived from the manifest):"]
    for lane in sorted(derived):
        lines.append(f"  {lane}: {derived[lane]}")
    lines.append("CATALOG_OAUTH_ROUTES:")
    for name, route in sorted(_by_name(manifest.get("oauth_routes")).items()):
        lines.append(f"  {name}: {_aliases(route)}")
    lines.append("CATALOG_PROVIDERS:")
    for name, route in sorted(_by_name(manifest.get("providers")).items()):
        lines.append(f"  {name}: {_aliases(route)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--print-derived",
        action="store_true",
        help="print the manifest-derived lanes and routes after the check",
    )
    arguments = parser.parse_args(argv)
    try:
        errors = check()
    except OSError as exc:  # pragma: no cover - defensive
        print(f"CATALOG_CHECK=ERROR {exc}")
        return 2
    if arguments.print_derived:
        print(render(_load_json(MANIFEST, [])))
    if errors:
        for error in errors:
            print(error)
        print(f"CATALOG_CHECK=FAIL ({len(errors)} issues)")
        return 1
    print("CATALOG_CHECK=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
