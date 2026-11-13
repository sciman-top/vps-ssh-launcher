"""Shared helpers for the split script-validation test modules."""

import base64
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from cpa_catalog_expectations import (
    PROVIDER_MATRIX_TAIL,
)


def repo_root() -> Path:
    """Repository root, resolved from this support module."""
    return Path(__file__).resolve().parents[1]


def _lane_script(root: Path, lane: str) -> Path:
    """Resolve a lane script by registry name."""
    return root / "scripts" / f"{lane}.ps1"


# gpt-6.1-sol-input retired 2026-11-11. 2026-11-14 re-admission: the aliased
# OpenCode slot entry relays the upstream body verbatim, so the probe
# response echoes the upstream model name.
CPA_TEST_PROVIDER_ALIASES: dict[str, str] = {
    "deepseek-v4.1-flash-oc": "deepseek-v4.1-flash",
}

# Codex-family routes answer over the Responses API, so health fixtures must
# shape their replies by protocol, not uniformly as chat completions.
# Empty since 2026-10-18: the muse-spark opencode.ai route retired with its
# channel; repopulate when a managed codex route returns.
CODEX_TEST_ROUTE_ALIASES: set[str] = set()
# Claude-family routes answer over the Anthropic Messages API.
CLAUDE_TEST_ROUTE_ALIASES = {"claude-haiku-5-5"}
# A claude-family group authenticates upstream with x-api-key, and the manifest
# carries this placeholder instead of the slot's secret; the projector (and
# cpa_policy.py) resolve it against the group's own credential.
SLOT_API_KEY_HEADER_TOKEN = "<slot-api-key>"

_EXPECTED_PAYLOAD_TOOL_RULES_CACHE: dict[str, Any] | None = None


def claude_route_headers(route: dict[str, Any], api_key: str) -> dict[str, str]:
    """Resolve a claude route's declared headers the way the projector does."""
    return {
        name: (api_key if value == SLOT_API_KEY_HEADER_TOKEN else value)
        for name, value in (route.get("headers") or {}).items()
    }


def _expected_payload_tool_rules() -> dict[str, Any]:
    """Deep copy of cpa_policy's pinned requests.payload for fixtures.

    Sourced from the policy module itself so the fixture cannot drift from
    the derivation; copied so tests that tamper with their config never
    poison the cache for other tests.
    """
    global _EXPECTED_PAYLOAD_TOOL_RULES_CACHE
    if _EXPECTED_PAYLOAD_TOOL_RULES_CACHE is None:
        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        _EXPECTED_PAYLOAD_TOOL_RULES_CACHE = cast(
            dict[str, Any], policy["EXPECTED_PAYLOAD_TOOL_RULES"]
        )
    return cast(
        dict[str, Any], json.loads(json.dumps(_EXPECTED_PAYLOAD_TOOL_RULES_CACHE))
    )


def cpa_test_generation_response(body: dict[str, Any]) -> dict[str, Any]:
    """Protocol-correct OK reply for a health probe request body."""
    model = body["model"]
    if model in CODEX_TEST_ROUTE_ALIASES:
        return {
            "model": model,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "OK"}],
                }
            ],
        }
    if model in CLAUDE_TEST_ROUTE_ALIASES:
        return {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
        }
    return {
        "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
    }


def cpa_test_probe_budget(body: dict[str, Any]) -> int:
    """Token budget of a probe request body across both protocols."""
    budget = body.get("max_tokens") or body.get("max_output_tokens")
    return int(budget or 0)


# Stand-in upstream catalog for cpa-health logic tests: every id must stay
# inside the manifest-derived allowed set or readiness fails closed (exit 20).
HEALTH_FIXTURE_CATALOG_IDS = list(dict.fromkeys(PROVIDER_MATRIX_TAIL))


def read_guardrail_source() -> str:
    """Resolve guardrail template loads for existing Bash fixture extractors."""
    root = Path(__file__).parents[1]
    source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
    paths = dict(
        re.findall(
            r'"([^"\n]+)" = "(scripts/remote/cpa-guardrail-[^"\n]+\.sh)"', source
        )
    )

    def expand(match: re.Match[str]) -> str:
        variable, name = match.groups()
        payload = (root / paths[name]).read_text(encoding="utf-8").removesuffix("\n")
        return f"${variable} = @'\n{payload}\n'@"

    return re.sub(
        r'\$(\w+) = Get-CpaGuardrailTemplate -Name "([^"\n]+)"', expand, source
    )


class ScriptValidationMixin:
    """Reusable helpers extracted from the original ScriptValidationTests."""

    BASH_HARNESS_TIMEOUT_SECONDS = 90

    # The write-safety shape every projected lane must keep: read-only unless
    # -Apply, the switch rendered into the payload, and the remote guard that
    # refuses before any mutation. Shared here so adding a lane means adding
    # one table entry, not copy-pasting four literal pins.
    APPLY_OPT_IN_PINS = (
        "[switch]$Apply",
        'APPLY="__APPLY__"',
        "if [ \"$APPLY\" != '1' ]; then",
        '.Replace("__APPLY__", $applyLiteral)',
    )

    # The projected shell lanes that expose -Apply. One registry instead of the
    # same name list copied into thirteen test files: adding a lane is one line
    # here, and every lane-wide contract below covers it automatically.
    LANE_SCRIPTS = (
        "cdn_fallback_lane",
        "cdn_origin_cert_sync",
        "zz_subscription_republish",
        "merged_subscription_lane",
        "reality_alt_sni_lane",
        "xhttp_nginx_front",
        "zz_xhttp_nginx_front",
        "nginx_tls_stealth",
        "nginx_camouflage_site",
        "hysteria2_masquerade_lane",
        "hysteria2_obfs_lane",
        "reality_shortid_harden",
        "stale_firewall_hygiene",
        "firewall_baseline",
    )

    # Lanes that serialize writes with their own descriptor and rollback
    # marker; their shape is pinned in the lane's own module.
    LANE_WRITE_SAFETY_EXEMPT: frozenset[str] = frozenset({"reality_alt_sni_lane"})

    # Fleet-wide write serialization and the verified rollback marker. Every
    # lane pinned these two literals in its own test file, which turned one
    # variable rename into a 12-file edit without proving new behaviour.
    LANE_WRITE_SAFETY_PINS = (
        "flock -n 9",
        "ROLLBACK_VERIFIED",
    )

    def assert_apply_is_opt_in(self: Any, text: str) -> None:
        """Every projected shell lane must refuse writes without -Apply."""
        for pin in self.APPLY_OPT_IN_PINS:
            self.assertIn(pin, text)

    def assert_lane_write_safety(self: Any, text: str) -> None:
        """Every -Apply lane must serialize writes and verify a rollback."""
        for pin in self.LANE_WRITE_SAFETY_PINS:
            self.assertIn(pin, text)

    def _valid_cpa_policy_config(self, policy: dict[str, Any]) -> dict[str, Any]:
        route_manifest = policy["ROUTE_MANIFEST"]
        codex_groups = [
            {
                "base-url": route["base-url"],
                "keys": [{"api-key": f"{route['name']}_TEST_KEY"}],
                **({"headers": dict(route["headers"])} if route.get("headers") else {}),
                "models": json.loads(json.dumps(route["models"])),
                "excluded-models": list(route_manifest["codex_api_key_exclusions"]),
            }
            for route in route_manifest.get("codex_api_key_routes", [])
        ]
        claude_groups = []
        for route in route_manifest.get("claude_api_key_routes", []):
            group_key = f"{route['name']}_TEST_KEY"
            group: dict[str, Any] = {
                "base-url": route["base-url"],
                "keys": [{"api-key": group_key}],
                "models": json.loads(json.dumps(route["models"])),
            }
            headers = claude_route_headers(route, group_key)
            if headers:
                group["headers"] = headers
            claude_groups.append(group)
        compatibility = [
            {
                "name": provider["name"],
                "base-url": (
                    f"{provider.get('scheme', 'https')}://{provider['host']}"
                    + (
                        f":{provider['port']}"
                        if provider.get("port") is not None
                        else ""
                    )
                    + provider["path"]
                ),
                "keys": [{"api-key": f"{provider['name']}_TEST_KEY"}],
                "models": json.loads(json.dumps(provider["models"])),
                "request-retry": 0,
                "disable-cooling": False,
                "support-prompt-cache-key": False,
                **(
                    {"headers": dict(provider["headers"])}
                    if provider.get("headers")
                    else {}
                ),
            }
            for provider in route_manifest["providers"]
        ]
        return cast(
            dict[str, Any],
            {
                # CPA v8 file layout (config-version: 8); canonical paths per
                # internal/config/config_v8.go in the v8.0.23 source.
                "config-version": 8,
                "server": {"host": "0.0.0.0", "port": 8317},
                "access": {"api-keys": ["FIXTURE_CLIENT_KEY"]},
                "requests": {
                    "payload": _expected_payload_tool_rules(),
                    "streaming": {
                        "keepalive-seconds": 15,
                        "send-keepalive-before-ft": True,
                    },
                },
                "routing": {
                    "strategy": "round-robin",
                    "session-affinity": True,
                    "session-affinity-ttl": "1h",
                    "session-affinity-rebind-on": "quota",
                    "session-affinity-subagents": False,
                    "force-model-prefix": True,
                    "retry": {"request-retry": 0, "max-retry-credentials": 3},
                    "cooldown": {
                        "disable-cooling": False,
                        "save-cooldown-status": False,
                        "transient-error-cooldown-seconds": 30,
                    },
                },
                "quota-exceeded": {
                    "switch-project": False,
                    "switch-preview-model": False,
                },
                "oauth": {
                    "providers": {
                        "antigravity": {
                            "antigravity-credits": False,
                            "sensitive-words": ["gpt", "openai"],
                        }
                    },
                    "settings": json.loads(
                        json.dumps(route_manifest["oauth_settings"])
                    ),
                    "model-alias": json.loads(
                        json.dumps(route_manifest["oauth_model_aliases"])
                    ),
                    "excluded-models": {
                        "antigravity": list(
                            route_manifest["antigravity_oauth_exclusions"]
                        ),
                        "codex": ["codex-*", "gpt-5.7*"]
                        + list(route_manifest["oauth_exclusions"]),
                        "claude": [
                            "claude-fable-5",
                            "claude-fable-5-1",
                            "claude-haiku-4-5-20251001",
                            "claude-opus-4-5-20251101",
                            "claude-opus-4-6",
                            "claude-opus-4-7",
                            "claude-opus-4-8",
                            "claude-opus-5",
                            "claude-sonnet-4-5-20250929",
                            "claude-sonnet-4-6",
                            "claude-sonnet-5",
                            "claude-3-5-haiku-20241022",
                            "claude-3-7-sonnet-20250219",
                            "claude-opus-4-1-20250805",
                            "claude-opus-4-20250514",
                            "claude-sonnet-4-20250514",
                        ],
                    },
                },
                "upstream": {
                    "codex": {
                        "model-level-cooling": True,
                        "stream-bootstrap-buffering": False,
                        "stream-bootstrap-timeout": "0",
                        "upstream-request-compression": True,
                        "upstream-request-compression-min-bytes": 1024,
                    },
                    "claude": {
                        "header-defaults": {"stabilize-device-profile": True},
                    },
                },
                "observability": {
                    "logs": {
                        "error-logs-max-files": 5,
                        "logs-max-total-size-mb": 32,
                    },
                },
                "api-keys": {
                    "openai-compatibility": compatibility,
                    "codex": codex_groups,
                    "claude": claude_groups,
                },
            },
        )

    @staticmethod
    def _resolve_bash() -> str | None:
        """Prefer Git Bash on Windows before retaining the PATH fallback."""
        if os.name == "nt":
            program_files_roots = dict.fromkeys(
                filter(
                    None,
                    (
                        os.environ.get("ProgramW6432"),
                        os.environ.get("ProgramFiles"),
                        os.environ.get("ProgramFiles(x86)"),
                    ),
                )
            )
            for root in program_files_roots:
                for relative_path in (
                    Path("Git") / "bin" / "bash.exe",
                    Path("Git") / "usr" / "bin" / "bash.exe",
                ):
                    candidate = Path(root) / relative_path
                    if candidate.is_file():
                        return str(candidate)

        return shutil.which("bash")

    @staticmethod
    def _bash_command(bash: str, *args: str) -> list[str]:
        """Give Git Bash the login environment its Unix utilities require."""
        if os.name == "nt" and "git" in {
            part.lower() for part in Path(bash).resolve().parts
        }:
            return [bash, "-l", *args]
        return [bash, *args]

    # Mount-style and capability probes cost two `bash -l` spawns each
    # (~0.5s per spawn on Git Bash). Their answers depend only on the selected
    # bash executable, never on the path being converted, so memoizing them at
    # module scope turns an O(call sites) cost into O(distinct executables):
    # measured 96 probes / 95.3s across three lane modules, 137.5s -> 43.4s.
    _BASH_MOUNT_STYLE: dict[str, str] = {}
    _BASH_FLOCK_AVAILABLE: dict[str, bool] = {}

    @classmethod
    def _bash_mount_style(cls, bash: str) -> str:
        """Return the drive prefix form this bash exposes: "mnt" or "root"."""
        cached = cls._BASH_MOUNT_STYLE.get(bash)
        if cached is not None:
            return cached
        # Two launchers answer to "bash" on Windows and they mount drives
        # differently: WSL exposes them at /mnt/<letter>, Git Bash (MSYS) at
        # /<letter>. Neither form is safe to assume, so probe the selected
        # executable once. Handing Git Bash the raw Win32 form happens to work
        # for most coreutils, but not for everything it can be asked to run
        # (an `rm` wrapper rejecting "embedded drive prefix" was observed
        # 2026-09-30), so the native MSYS form is preferred when available.
        if "wsl" in Path(bash).name.lower() or (
            Path(bash).name.lower() == "bash" and os.environ.get("WSL_DISTRO_NAME")
        ):
            cls._BASH_MOUNT_STYLE[bash] = "mnt"
            return "mnt"
        for probe_dir, style in (("/mnt/c", "mnt"), ("/c", "root")):
            try:
                probe = subprocess.run(
                    cls._bash_command(bash, "-c", f"test -d {probe_dir}"),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if probe.returncode == 0:
                cls._BASH_MOUNT_STYLE[bash] = style
                return style
        # No mount style answered: keep the raw Win32 form, which most
        # coreutils accept.
        cls._BASH_MOUNT_STYLE[bash] = "none"
        return "none"

    @classmethod
    def _bash_path(cls, bash: str, path: Path) -> str:
        """Use a path understood by the selected Bash implementation."""
        if os.name != "nt" or not path.drive:
            return str(path)
        style = cls._bash_mount_style(bash)
        drive = path.drive[0].lower()
        posix = path.as_posix()
        if style == "mnt":
            return f"/mnt/{drive}{posix[2:]}"
        if style == "root":
            return f"/{drive}{posix[2:]}"
        return str(path)

    @classmethod
    def _bash_has_flock(cls, bash: str) -> bool:
        """Whether this bash ships flock; probed once per executable.

        Git Bash on Windows has no flock while the deployed lanes run on
        Linux, so 17 camouflage tests skip here. Probing per test spent a
        `bash -l` spawn each time to reach the same answer.
        """
        cached = cls._BASH_FLOCK_AVAILABLE.get(bash)
        if cached is not None:
            return cached
        try:
            probe = subprocess.run(
                cls._bash_command(bash, "-c", "command -v flock"),
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            cls._BASH_FLOCK_AVAILABLE[bash] = False
            return False
        available = probe.returncode == 0
        cls._BASH_FLOCK_AVAILABLE[bash] = available
        return available

    def _require_flock(self: Any, bash: str) -> None:
        if not self._bash_has_flock(bash):
            self.skipTest("flock is not available in this Bash")

    @staticmethod
    def _render_embedded_wrapper(source: str, function_name: str) -> str:
        function_start = source.index(f"{function_name}()")
        body_start = source.index("#!/usr/bin/env bash", function_start)
        body_end = source.index("\nEOF", body_start)
        rendered = source[body_start:body_end].replace("`", "")
        return rendered.replace("\r\n", "\n").replace("\r", "\n")

    def _run_oauth_retire_catalog_contract(
        self, catalog: dict[str, Any]
    ) -> "subprocess.CompletedProcess[bytes]":
        source = read_guardrail_source()
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        block = payload.split("python3 - \"$RETIRE_CATALOG\" <<'PY'\n", 1)[1].split(
            "\nPY\n", 1
        )[0]
        manifest_text = (
            Path(__file__).parents[1] / "scripts/remote/cpa_provider_routes.json"
        ).read_text(encoding="utf-8")
        manifest_b64 = base64.b64encode(manifest_text.encode("utf-8")).decode("ascii")
        script = block.replace("__CPA_OAUTH_RETIRE_MANIFEST_B64__", manifest_b64)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(catalog, handle)
            catalog_path = handle.name
        try:
            return subprocess.run(
                [sys.executable, "-c", script, catalog_path],
                capture_output=True,
                timeout=30,
                check=False,
            )
        finally:
            os.unlink(catalog_path)
