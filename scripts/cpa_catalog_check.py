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
lane, and each plugin realm maps to its own WorkBuddy lane), so that mapping
lives in ``LANE_SOURCES`` -- one table, validated in both directions, instead
of the same knowledge copied into four files.

Exit codes: 0 consistent, 1 drift found, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import ast
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
COCKPIT_POLICY = ROOT / "scripts" / "cockpit_local_provider_policy.json"
CONTEXT_OVERRIDES = ROOT / "config" / "codex-model-context-overrides.json"

# lane name -> (kind, source name, optional plugin realm)
LANE_SOURCES: dict[str, tuple[str, ...]] = {
    "chatgpt-oauth": ("oauth_route", "chatgpt-plus-oauth"),
    "antigravity-oauth": ("oauth_route", "antigravity-oauth"),
    "zhipu-coding-plan": ("provider", "zhipu-plan"),
    "zcode-start-plan": ("provider", "zcode-start-plan"),
    "workbuddy-oauth": ("plugin", "workbuddy-provider", "cn"),
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

# The deployed guardrail scripts each carry their own literal lane table. The
# checklist calls them an intentional independent oracle, which is fine -- but
# nothing verified that the two copies still agree with the manifest, so a
# catalog change that misses one of them fails only at -Apply or in the doctor,
# against a live host. This checker reads both copies and compares them here.
DEPLOYED_LANE_TABLES = (
    ROOT / "scripts" / "remote" / "cpa-guardrail-apply.sh",
    ROOT / "scripts" / "remote" / "cpa-guardrail-doctor.sh",
)

# The embedded Python block starts its lane table with this exact line.
_LANE_TABLE_START = "expected = {"


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        # Tests point CHOICE_SITES / MANIFEST at temp files outside the repo.
        return path.as_posix()


def _manifest_contexts(manifest: dict[str, Any]) -> dict[str, int]:
    """Resolve max-context-length pins per client-facing alias from manifest."""
    contexts: dict[str, int] = {}
    for provider in manifest.get("providers", []) or []:
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models", []) or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias") or model.get("name")
            window = model.get("max-context-length")
            if isinstance(alias, str) and alias and type(window) is int:
                contexts[alias] = window
    oauth_settings = manifest.get("oauth_settings")
    oauth_aliases_map = manifest.get("oauth_model_aliases") or {}
    if isinstance(oauth_settings, dict):
        for channel, entries in oauth_settings.items():
            if not isinstance(entries, list):
                continue
            channel_aliases = (
                oauth_aliases_map.get(channel, [])
                if isinstance(oauth_aliases_map, dict)
                else []
            )
            alias_by_source: dict[str, list[tuple[str, bool]]] = {}
            if isinstance(channel_aliases, list):
                for a_entry in channel_aliases:
                    if (
                        isinstance(a_entry, dict)
                        and isinstance(a_entry.get("name"), str)
                        and isinstance(a_entry.get("alias"), str)
                    ):
                        alias_by_source.setdefault(a_entry["name"].strip(), []).append(
                            (a_entry["alias"].strip(), bool(a_entry.get("fork")))
                        )
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                alias = entry.get("alias") or entry.get("name")
                window = entry.get("max-context-length")
                if isinstance(alias, str) and alias and type(window) is int:
                    if alias in alias_by_source:
                        for target_alias, forked in alias_by_source[alias]:
                            contexts[target_alias] = window
                            if forked:
                                contexts[alias] = window
                    else:
                        contexts[alias] = window
    return contexts


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


def plugin_lane_models(
    manifest: dict[str, Any], provider: str, realm: str
) -> list[str]:
    """Plugin pin list for one provider realm, in manifest order.

    These are the upstream IDs the plugin scheduler sees. Forked CPA client
    aliases live in each computed lane's ``client_aliases`` instead.
    """
    matches: list[list[str]] = []
    for route in manifest.get("plugin_routes", []) or []:
        if (
            not isinstance(route, dict)
            or route.get("provider") != provider
            or route.get("realm") != realm
        ):
            continue
        matches.append(
            [name for name in route.get("models", []) or [] if isinstance(name, str)]
        )
    return matches[0] if len(matches) == 1 else []


def plugin_lane_aliases(
    manifest: dict[str, Any], provider: str, models: list[str]
) -> list[str]:
    """Client-facing names this plugin pin set serves, in pin order.

    Mirrors the host behavior in CLIProxyAPI's applyOAuthModelAliasEntries:
    a pin with alias entries contributes each alias, and keeps the bare name
    only when an entry sets fork=true (fork aliases are added alongside the
    original id); a pin without entries keeps its bare name; an entry without
    fork replaces the bare name outright, so an upstream id whose client
    surface should only ever be reached through its alias stays unlisted.
    One pin may carry several entries (several aliases plus fork).
    """
    raw_aliases = manifest.get("oauth_model_aliases")
    entries = raw_aliases.get(provider, []) if isinstance(raw_aliases, dict) else []
    if not isinstance(entries, list):
        entries = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("alias"), str)
        ):
            continue
        by_source.setdefault(entry["name"].strip(), []).append(entry)
    out: list[str] = []
    for model in models:
        pin_entries = by_source.get(model, [])
        if not pin_entries:
            out.append(model)
            continue
        keep_original = False
        for entry in pin_entries:
            out.append(entry["alias"].strip())
            keep_original = keep_original or bool(entry.get("fork"))
        if keep_original:
            out.append(model)
    return out


def derive_lanes(manifest: dict[str, Any], errors: list[str]) -> dict[str, list[str]]:
    """Resolve every mapped admission lane to its manifest alias list."""
    oauth = _by_name(manifest.get("oauth_routes"))
    providers = _by_name(manifest.get("providers"))
    derived: dict[str, list[str]] = {}
    plugin_lanes: list[tuple[str, str, list[str], str]] = []
    for lane, source_spec in LANE_SOURCES.items():
        kind = source_spec[0]
        source = source_spec[1]
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
            if len(source_spec) != 3:
                errors.append(
                    f"LANE_SOURCES maps {lane!r} to a plugin source without "
                    "exactly one realm"
                )
                continue
            models = plugin_lane_models(manifest, source, source_spec[2])
            if not models:
                errors.append(
                    f"LANE_SOURCES maps {lane!r} to missing or duplicate plugin "
                    f"realm {source!r}/{source_spec[2]!r}"
                )
                continue
            plugin_lanes.append((source, lane, models, source_spec[2]))
        else:  # pragma: no cover - the table literal above is exhaustive
            errors.append(f"LANE_SOURCES has an unknown kind {kind!r} for {lane!r}")
    for provider, lane, models, _realm in plugin_lanes:
        # plugin_lane_aliases already unfolds every pin into its full
        # client-facing name set (aliases plus fork-kept bare names), so the
        # lane list is the unfolded sequence for both realms.
        derived[lane] = plugin_lane_aliases(manifest, provider, models)
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


def _embedded_lane_table_span(text: str) -> tuple[int, int] | None:
    """Locate the ``expected = {...}`` lane table literal span in a script."""
    anchor = f'expected = {{\n    "{LANE_SOURCES and next(iter(LANE_SOURCES))}":'
    start = text.find(anchor)
    if start < 0:
        start = text.find(_LANE_TABLE_START)
    if start < 0:
        return None
    depth = 0
    end = start
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    return (start, end) if depth == 0 and end > start else None


def _embedded_lane_table(text: str) -> dict[str, list[str]] | None:
    """Extract the lane table from a shell script's embedded Python block.

    The table is a Python dict literal inside a heredoc, so it is parsed with
    ast rather than imported: the surrounding script is a deploy transaction and
    must never be executed to check its contents. The block is located by the
    first lane *name* it must contain, because the same scripts also carry
    unrelated `expected = {...}` tables (port bindings, for example).
    """
    span = _embedded_lane_table_span(text)
    if span is None:
        return None
    start, end = span
    literal = text[start + len("expected = ") : end]
    try:
        parsed = ast.literal_eval(literal)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        str(lane): [str(name) for name in models]
        for lane, models in parsed.items()
        if isinstance(models, list)
    }


def check_deployed_lane_tables(
    derived: dict[str, list[str]], errors: list[str]
) -> None:
    """Every deployed guardrail copy must agree with the manifest-derived lanes."""
    for path in DEPLOYED_LANE_TABLES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{_relative(path)}: unreadable ({exc})")
            continue
        table = _embedded_lane_table(text)
        if table is None:
            errors.append(
                f"{_relative(path)}: no parseable 'expected = {{...}}' lane table"
            )
            continue
        for lane in sorted(set(table) | set(derived)):
            if table.get(lane) != derived.get(lane):
                errors.append(
                    f"{_relative(path)}: lane {lane!r} models {table.get(lane)} != "
                    f"manifest {derived.get(lane)}"
                )


def check() -> list[str]:
    errors: list[str] = []
    manifest = _load_json(MANIFEST, errors)
    if not manifest:
        if not errors:
            # _load_json returns {} for a syntactically valid but EMPTY object
            # without recording anything, and this early return would then
            # report CATALOG_CHECK=PASS with exit 0 -- the first gate in the
            # fixed order failing open on the one input that proves nothing.
            errors.append(
                f"{_relative(MANIFEST)}: manifest is empty; expected a non-empty "
                "JSON object"
            )
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

    # 3a. The deployed guardrail copies carry their own lane table.
    check_deployed_lane_tables(derived, errors)

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

    # 6. Local Cockpit retirement policy must never target a live CPA alias:
    # when a retired alias is re-admitted in the manifest (e.g.
    # deepseek-v4.1-flash-oc on 2026-11-14), leaving it in retired_models makes
    # cockpit_model_catalog_prune / cockpit_retired_models delete the live route.
    cockpit_policy = _load_json(COCKPIT_POLICY, errors)
    raw_local_retired = cockpit_policy.get("retired_models")
    if isinstance(raw_local_retired, list):
        live_marked_retired = sorted(
            {str(name) for name in raw_local_retired if isinstance(name, str)} & live
        )
        for name in live_marked_retired:
            errors.append(
                f"{_relative(COCKPIT_POLICY)}: retired_models contains live alias "
                f"{name!r}"
            )

    # 7. Desktop context overrides must only name live CPA aliases and agree
    # with the manifest's max-context-length pins.
    manifest_contexts = _manifest_contexts(manifest)
    overrides = _load_json(CONTEXT_OVERRIDES, errors)
    override_models = overrides.get("models")
    override_slugs: set[str] = set()
    if isinstance(override_models, list):
        for entry in override_models:
            if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
                continue
            slug = entry["slug"]
            override_slugs.add(slug)
            if slug not in live:
                errors.append(
                    f"{_relative(CONTEXT_OVERRIDES)}: {slug!r} is not a live "
                    "manifest alias"
                )
                continue
            expected_window = manifest_contexts.get(slug)
            if expected_window is None:
                errors.append(
                    f"{_relative(CONTEXT_OVERRIDES)}: {slug!r} has no "
                    "max-context-length in the manifest"
                )
                continue
            compact_ceiling = (expected_window * 9) // 10
            compact = entry.get("auto_compact_token_limit")
            if (
                entry.get("context_window") != expected_window
                or entry.get("max_context_window") != expected_window
            ):
                errors.append(
                    f"{_relative(CONTEXT_OVERRIDES)}: {slug!r} window "
                    f"({entry.get('context_window')}/{entry.get('max_context_window')}) "
                    f"!= manifest ({expected_window}/{expected_window})"
                )
            if type(compact) is not int or not 0 < compact <= compact_ceiling:
                errors.append(
                    f"{_relative(CONTEXT_OVERRIDES)}: {slug!r} "
                    f"auto_compact_token_limit={compact!r} must be an integer "
                    f"between 1 and {compact_ceiling}"
                )
        for slug in sorted((set(manifest_contexts) & live) - override_slugs):
            errors.append(
                f"{_relative(CONTEXT_OVERRIDES)}: live manifest model {slug!r} "
                f"(max-context-length={manifest_contexts[slug]}) is missing "
                "from desktop context overrides"
            )
    return errors


def _format_lane_table(derived: dict[str, list[str]], key_order: list[str]) -> str:
    ordered_keys = [k for k in key_order if k in derived] + [
        k for k in derived if k not in key_order
    ]
    lines = ["expected = {"]
    for lane in ordered_keys:
        models = derived[lane]
        if len(models) == 1 and len(lane) + len(models[0]) < 56:
            lines.append(f'    "{lane}": ["{models[0]}"],')
        else:
            lines.append(f'    "{lane}": [')
            for model in models:
                lines.append(f'        "{model}",')
            lines.append("    ],")
    lines.append("}")
    return "\n".join(lines)


def _format_py_list(name: str, items: list[str]) -> str:
    if not items:
        return f"{name}: list[str] = []"
    lines = [f"{name} = ["]
    for item in items:
        lines.append(f'    "{item}",')
    lines.append("]")
    return "\n".join(lines)


def _replace_python_assignment(text: str, name: str, replacement: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        target_id: str | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                target_id = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_id = node.target.id
        if target_id == name and node.end_lineno is not None:
            lines = text.splitlines(keepends=True)
            prefix = "".join(lines[: node.lineno - 1])
            suffix = "".join(lines[node.end_lineno :])
            return prefix + replacement + "\n" + suffix
    return text


def sync() -> list[Path]:
    """Synchronize derived catalog targets from the route manifest."""
    errors: list[str] = []
    manifest = _load_json(MANIFEST, errors)
    if errors or not manifest:
        raise ValueError("; ".join(errors) or "manifest is empty")
    derived = derive_lanes(manifest, errors)
    if errors:
        raise ValueError("; ".join(errors))
    live = live_aliases(manifest, derived)
    manifest_contexts = _manifest_contexts(manifest)
    changed: list[Path] = []

    def _write_if_changed(path: Path, new_text: str) -> None:
        old_text = path.read_text(encoding="utf-8")
        if new_text != old_text:
            path.write_text(new_text, encoding="utf-8", newline="\n")
            changed.append(path)

    # 1. scripts/remote/cpa-admission.json
    admission = _load_json(ADMISSION_CONFIG, errors)
    if not errors and isinstance(admission.get("lanes"), list):
        for lane in admission["lanes"]:
            if isinstance(lane, dict) and lane.get("name") in derived:
                lane["models"] = list(derived[lane["name"]])
        _write_if_changed(
            ADMISSION_CONFIG,
            json.dumps(admission, indent=2, ensure_ascii=False) + "\n",
        )

    # 2. config/codex-model-context-overrides.json
    if CONTEXT_OVERRIDES.exists():
        overrides = _load_json(CONTEXT_OVERRIDES, errors)
        existing_entries = {
            e["slug"]: e
            for e in (overrides.get("models") or [])
            if isinstance(e, dict) and isinstance(e.get("slug"), str)
        }
        ordered_slugs = [
            slug
            for slug in existing_entries
            if slug in live and slug in manifest_contexts
        ] + sorted((set(manifest_contexts) & live) - set(existing_entries))
        new_models: list[dict[str, Any]] = []
        for slug in ordered_slugs:
            window = manifest_contexts[slug]
            ceiling = (window * 9) // 10
            prev = existing_entries.get(slug, {})
            prev_compact = prev.get("auto_compact_token_limit")
            compact = (
                prev_compact
                if type(prev_compact) is int and 0 < prev_compact <= ceiling
                else min(320000, ceiling)
            )
            new_models.append(
                {
                    "slug": slug,
                    "context_window": window,
                    "max_context_window": window,
                    "auto_compact_token_limit": compact,
                }
            )
        overrides["models"] = new_models
        _write_if_changed(
            CONTEXT_OVERRIDES,
            json.dumps(overrides, indent=2, ensure_ascii=False) + "\n",
        )

    # 3. Deployed guardrail lane tables
    for path in DEPLOYED_LANE_TABLES:
        text = path.read_text(encoding="utf-8")
        span = _embedded_lane_table_span(text)
        current = _embedded_lane_table(text)
        if span is not None and current is not None:
            start, end = span
            formatted = _format_lane_table(derived, list(current.keys()))
            _write_if_changed(path, text[:start] + formatted + text[end:])

    # 4. tests/cpa_catalog_expectations.py
    if EXPECTATIONS.exists():
        exp_text = EXPECTATIONS.read_text(encoding="utf-8")
        oauth_by_name = _by_name(manifest.get("oauth_routes"))
        if "chatgpt-plus-oauth" in oauth_by_name:
            exp_text = _replace_python_assignment(
                exp_text,
                "OAUTH_ROUTE_ALIASES",
                _format_py_list(
                    "OAUTH_ROUTE_ALIASES",
                    _aliases(oauth_by_name["chatgpt-plus-oauth"]),
                ),
            )
        if "antigravity-oauth" in oauth_by_name:
            exp_text = _replace_python_assignment(
                exp_text,
                "ANTIGRAVITY_OAUTH_ROUTE_ALIASES",
                _format_py_list(
                    "ANTIGRAVITY_OAUTH_ROUTE_ALIASES",
                    _aliases(oauth_by_name["antigravity-oauth"]),
                ),
            )
        ag_excl = [
            str(x) for x in manifest.get("antigravity_oauth_exclusions", []) or []
        ]
        exp_text = _replace_python_assignment(
            exp_text,
            "ANTIGRAVITY_OAUTH_EXCLUSIONS",
            _format_py_list("ANTIGRAVITY_OAUTH_EXCLUSIONS", ag_excl),
        )
        oracle, _ = read_expectations(EXPECTATIONS)
        prev_lanes = oracle.get("ADMISSION_LANE_MODELS")
        lane_order = (
            list(prev_lanes.keys()) if isinstance(prev_lanes, dict) else list(derived)
        )
        ordered_lane_keys = [k for k in lane_order if k in derived] + [
            k for k in derived if k not in lane_order
        ]
        lane_lines = ["ADMISSION_LANE_MODELS = {"]
        for lane in ordered_lane_keys:
            if lane == "chatgpt-oauth":
                lane_lines.append('    "chatgpt-oauth": list(OAUTH_ROUTE_ALIASES),')
            elif lane == "antigravity-oauth":
                lane_lines.append(
                    '    "antigravity-oauth": list(ANTIGRAVITY_OAUTH_ROUTE_ALIASES),'
                )
            else:
                models = derived[lane]
                if len(models) == 1 and len(lane) + len(models[0]) < 56:
                    lane_lines.append(f'    "{lane}": ["{models[0]}"],')
                else:
                    lane_lines.append(f'    "{lane}": [')
                    for model in models:
                        lane_lines.append(f'        "{model}",')
                    lane_lines.append("    ],")
        lane_lines.append("}")
        exp_text = _replace_python_assignment(
            exp_text, "ADMISSION_LANE_MODELS", "\n".join(lane_lines)
        )
        _write_if_changed(EXPECTATIONS, exp_text)

    return changed


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
    parser.add_argument(
        "--sync",
        action="store_true",
        help="synchronize derived admission, overrides, guardrail tables, and test expectations from the manifest",
    )
    arguments = parser.parse_args(argv)
    if arguments.sync:
        try:
            changed = sync()
        except (OSError, ValueError) as exc:
            print(f"CATALOG_SYNC=ERROR {exc}")
            return 2
        print(f"CATALOG_SYNC=OK ({len(changed)} files updated)")
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
