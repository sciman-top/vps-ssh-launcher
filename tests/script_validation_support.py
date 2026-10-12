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


CPA_TEST_PROVIDER_ALIASES = {
    "gpt-6.1-sol-input": "gpt-6.1-sol",
}

# Codex-family routes answer over the Responses API, so health fixtures must
# shape their replies by protocol, not uniformly as chat completions.
CODEX_TEST_ROUTE_ALIASES = {"muse-spark-1.3-contributor"}

_EXPECTED_PAYLOAD_TOOL_RULES_CACHE: dict[str, Any] | None = None


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
                    "strategy": "fill-first",
                    "session-affinity": True,
                    "session-affinity-ttl": "1h",
                    "session-affinity-subagents": False,
                    "force-model-prefix": True,
                    "retry": {"request-retry": 0, "max-retry-credentials": 1},
                    "cooldown": {
                        "disable-cooling": False,
                        "save-cooldown-status": False,
                        "transient-error-cooldown-seconds": 60,
                    },
                },
                "quota-exceeded": {
                    "switch-project": False,
                    "switch-preview-model": False,
                },
                "oauth": {
                    "providers": {"antigravity": {"antigravity-credits": False}},
                    "excluded-models": {
                        "codex": ["codex-*", "gpt-5.7*"]
                        + list(route_manifest["oauth_exclusions"])
                    },
                },
                "upstream": {
                    "codex": {
                        "stream-bootstrap-buffering": False,
                        "stream-bootstrap-timeout": "0",
                    }
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

    @staticmethod
    def _bash_path(bash: str, path: Path) -> str:
        """Use a path understood by the selected Bash implementation."""
        if os.name != "nt" or not path.drive:
            return str(path)

        # Two launchers answer to "bash" on Windows and they mount drives
        # differently: WSL exposes them at /mnt/<letter>, Git Bash (MSYS) at
        # /<letter>. Neither form is safe to assume, so probe the selected
        # executable. Handing Git Bash the raw Win32 form happens to work for
        # most coreutils, but not for everything it can be asked to run
        # (an `rm` wrapper rejecting "embedded drive prefix" was observed
        # 2026-09-30), so the native MSYS form is preferred when available.
        posix = path.as_posix()
        drive = path.drive[0].lower()
        for probe_dir, prefix in (("/mnt/c", f"/mnt/{drive}"), ("/c", f"/{drive}")):
            try:
                probe = subprocess.run(
                    ScriptValidationMixin._bash_command(
                        bash, "-c", f"test -d {probe_dir}"
                    ),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if probe.returncode == 0:
                return f"{prefix}{posix[2:]}"
        return str(path)

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
        block = payload.split(
            "python3 - /tmp/cpa-oauth-retire-catalog.json <<'PY'\n", 1
        )[1].split("\nPY\n", 1)[0]
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
