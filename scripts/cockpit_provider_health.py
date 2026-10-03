#!/usr/bin/env python3
"""Check the local Cockpit provider configuration for the failure shapes that
silently stop the Provider Gateway on port 10909.

The desktop Codex client and the Cockpit model list both talk to
`http://127.0.0.1:10909/v1`, which is served by a sidecar that Cockpit starts
**only when the instance's bound account satisfies a start predicate**. When the
predicate flips, the port simply stops existing and every request renders as
`PROVIDER_MODELS_HTTP_503` or `Connection failed: error sending request` --
with no error anywhere in the app log. This module proves the configuration
shape instead of waiting for that symptom.

Three invariants are checked (see docs/runbooks/cockpit-sidecar-guardrails.md):

1. **Key/endpoint fit.** A provider entry whose key does not belong to that
   endpoint answers 401. `agt_codex_` keys are local sidecar keys; `agt_gw_`
   keys belong to the remote `fq.sciman.top` gateway. Mixing them on one entry
   is the recorded cause of `PROVIDER_MODELS_HTTP_401`.
2. **Binding/credential coherence.** The bound account id is `md5(api_key)`, so
   editing a provider key changes the id and strands the old binding. The bound
   id must reverse-map to a key that still exists in some provider entry.
3. **Sidecar/config agreement.** Each provider-gateway sidecar config must
   accept exactly the key its provider entry advertises, and must forward to the
   upstream that entry names.

Read-only: no file is opened for writing and no secret is printed in full.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

# Exit codes, chosen so a caller can branch without parsing prose.
OK = 0
FINDINGS = 1
CANNOT_CHECK = 2

PROVIDER_GATEWAY_BIND_PREFIX = "__provider_gateway__:"
API_KEY_ACCOUNT_PREFIX = "codex_apikey_"
LOCAL_SIDECAR_KEY_PREFIX = "agt_codex_"
REMOTE_GATEWAY_KEY_PREFIX = "agt_gw_"

DEFAULT_COCKPIT_DIR = pathlib.Path.home() / ".antigravity_cockpit"
DEFAULT_CODEX_CONFIG = pathlib.Path.home() / ".codex" / "config.toml"
DEFAULT_GATEWAY_PORT = 10909


@dataclass(frozen=True)
class KeyRef:
    """A provider API key, identified without exposing it."""

    provider_id: str
    provider_name: str
    base_url: str
    key_id: str
    length: int
    tail: str

    def label(self) -> str:
        return f"{self.provider_name}/{self.key_id}"


@dataclass
class Finding:
    code: str
    detail: str
    severity: str = "error"

    def render(self) -> str:
        mark = "!" if self.severity == "error" else "-"
        return f"  [{mark}] {self.code}: {self.detail}"


@dataclass
class Report:
    bind_account_id: str | None = None
    bind_needs_gateway: bool = False
    bound_key: KeyRef | None = None
    providers: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, code: str, detail: str, severity: str = "error") -> None:
        self.findings.append(Finding(code, detail, severity))


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def key_kind(api_key: str) -> str:
    """Classify a key by its issuer prefix, or `unknown`."""
    if api_key.startswith(LOCAL_SIDECAR_KEY_PREFIX):
        return "local_sidecar"
    if api_key.startswith(REMOTE_GATEWAY_KEY_PREFIX):
        return "remote_gateway"
    return "unknown"


def endpoint_expects(base_url: str) -> str:
    """The key kind an endpoint will accept, or `either` when unrecognised."""
    lowered = (base_url or "").lower()
    if "127.0.0.1" in lowered or "localhost" in lowered:
        return "local_sidecar"
    if "fq.sciman.top" in lowered:
        return "remote_gateway"
    return "either"


def check_key_endpoint_fit(
    provider_name: str, base_url: str, api_key: str
) -> Finding | None:
    """Return a finding when a key is attached to an endpoint that rejects it."""
    if not api_key:
        return Finding("empty-key", f"{provider_name}: 条目下存在空 key")
    kind = key_kind(api_key)
    expected = endpoint_expects(base_url)
    if expected == "either" or kind == "unknown":
        return None
    if kind != expected:
        return Finding(
            "key-endpoint-mismatch",
            f"{provider_name}: 挂了 {kind} 类 key（...{api_key[-4:]}），"
            f"但 {base_url} 只接受 {expected} 类 ⇒ 会得到 401",
        )
    return None


def parse_bind_account(raw: str | None) -> tuple[str | None, bool]:
    """Split a bind id into (account_id, needs_gateway)."""
    if not raw:
        return None, False
    if raw.startswith(PROVIDER_GATEWAY_BIND_PREFIX):
        return raw[len(PROVIDER_GATEWAY_BIND_PREFIX) :], True
    return raw, False


def account_md5(account_id: str | None) -> str | None:
    """The digest embedded in an API-key account id, or None for OAuth ids."""
    if not account_id or not account_id.startswith(API_KEY_ACCOUNT_PREFIX):
        return None
    digest = account_id[len(API_KEY_ACCOUNT_PREFIX) :]
    return digest or None


def load_provider_keys(
    providers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, KeyRef], dict[str, str]]:
    """Build (summary, md5->KeyRef, secret->md5) views of provider entries."""
    summary: list[dict[str, Any]] = []
    by_md5: dict[str, KeyRef] = {}
    md5_of_secret: dict[str, str] = {}
    for provider in providers:
        entry: dict[str, Any] = {
            "id": provider.get("id"),
            "name": provider.get("name"),
            "baseUrl": provider.get("baseUrl"),
            "keys": [],
        }
        for key in provider.get("apiKeys", []):
            secret = key.get("apiKey") or key.get("key") or ""
            if not secret:
                continue
            ref = KeyRef(
                provider_id=str(provider.get("id") or ""),
                provider_name=str(provider.get("name") or ""),
                base_url=str(provider.get("baseUrl") or ""),
                key_id=str(key.get("id") or ""),
                length=len(secret),
                tail=secret[-4:],
            )
            digest = _md5(secret)
            by_md5.setdefault(digest, ref)
            md5_of_secret[secret] = digest
            entry["keys"].append(
                {
                    "id": ref.key_id,
                    "length": ref.length,
                    "tail": ref.tail,
                    "kind": key_kind(secret),
                }
            )
        summary.append(entry)
    return summary, by_md5, md5_of_secret


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_config_target(path: pathlib.Path) -> str | None:
    """Return `base_url` from the codex_local_access provider block, if present."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_block = stripped == "[model_providers.codex_local_access]"
            continue
        if in_block and stripped.startswith("base_url"):
            _, _, value = stripped.partition("=")
            return value.strip().strip('"')
    return None


def build_report(cockpit_dir: pathlib.Path) -> Report:
    report = Report()
    directories = sorted(
        (cockpit_dir / "codex_provider_gateway_sidecars").glob("*/config.json")
    )

    providers_raw = read_json(cockpit_dir / "codex_model_providers.json")
    providers = (
        providers_raw
        if isinstance(providers_raw, list)
        else providers_raw.get("providers", [])
    )
    summary, by_md5, _ = load_provider_keys(providers)
    report.providers = summary

    for provider in providers:
        name = str(provider.get("name") or "?")
        base_url = str(provider.get("baseUrl") or "")
        for key in provider.get("apiKeys", []):
            secret = key.get("apiKey") or key.get("key") or ""
            finding = check_key_endpoint_fit(name, base_url, secret)
            if finding is not None:
                report.add(finding.code, finding.detail, finding.severity)

    instances = read_json(cockpit_dir / "codex_instances.json")
    bind_raw = (instances.get("defaultSettings") or {}).get("bindAccountId")
    account_id, needs_gateway = parse_bind_account(bind_raw)
    report.bind_account_id = account_id
    report.bind_needs_gateway = needs_gateway

    digest = account_md5(account_id)
    if digest and account_id:
        report.bound_key = by_md5.get(digest)
        if digest not in by_md5:
            report.add(
                "binding-strands",
                f"绑定账号 {account_id[:24]}… 的 md5={digest[:8]} 在任何 provider "
                "条目里都找不到对应 key ⇒ 条目与账号已错位（换过 key 但未重新绑定）",
            )

    if not needs_gateway:
        report.add(
            "gateway-not-required",
            f"绑定账号 {account_id or '(空)'} 不带 {PROVIDER_GATEWAY_BIND_PREFIX} 前缀 "
            "⇒ 除非该账号满足 account_requires_provider_gateway，否则 10909 不会启动",
            severity="warn",
        )

    if not directories:
        report.add(
            "no-sidecar-config",
            "未找到任何 provider gateway sidecar config ⇒ 10909 从未被生成过",
        )
    for config_path in directories:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.add("sidecar-config-unreadable", f"{config_path}: {exc}")
            continue
        port = config.get("port")
        accepted = [
            key for key in config.get("api-keys", []) if isinstance(key, str) and key
        ]
        upstreams = config.get("codex-api-key", [])
        if port == DEFAULT_GATEWAY_PORT:
            # The sidecar accepts a *local* key from its clients and forwards a
            # *remote* key to the upstream. Both directions are checked
            # separately: conflating them makes the upstream key look invalid.
            for secret in accepted:
                if key_kind(secret) != "local_sidecar":
                    report.add(
                        "sidecar-accepts-wrong-kind",
                        f"10909 sidecar 接受的 key（...{secret[-4:]}）不是 "
                        f"{LOCAL_SIDECAR_KEY_PREFIX} 前缀 ⇒ 客户端会拿到 401",
                    )
            for upstream in upstreams:
                base_url = str(upstream.get("base-url") or "")
                secret = upstream.get("api-key") or ""
                if not secret:
                    continue
                if endpoint_expects(base_url) == "remote_gateway" and (
                    key_kind(secret) != "remote_gateway"
                ):
                    report.add(
                        "sidecar-upstream-key-mismatch",
                        f"10909 转发 key（...{secret[-4:]}）不是 "
                        f"{REMOTE_GATEWAY_KEY_PREFIX} 前缀，但上游 {base_url} "
                        "只接受远程网关 key",
                    )
                if key_kind(secret) == "local_sidecar":
                    report.add(
                        "sidecar-forwards-local-key",
                        f"10909 把本地 sidecar key（...{secret[-4:]}）当上游凭据转发，"
                        "上游不会接受",
                    )
    return report


def render(report: Report, config_target: str | None) -> str:
    lines: list[str] = []
    lines.append("=== 绑定与凭据 ===")
    lines.append(f"  bindAccountId      = {report.bind_account_id}")
    lines.append(f"  需求网关前缀        = {report.bind_needs_gateway}")
    if report.bound_key is not None:
        ref = report.bound_key
        lines.append(
            f"  绑定 key 归属       = 「{ref.provider_name}」{ref.key_id} "
            f"(len {ref.length}, ...{ref.tail})"
        )
    lines.append(f"  桌面 config.toml   = {config_target or '(未找到)'}")
    if config_target and "10909" not in config_target:
        lines.append("  ⚠️ 桌面未指向 10909 —— 别名重写层被绕过")

    lines.append("")
    lines.append(f"=== provider 条目（{len(report.providers)} 条）===")
    for provider in report.providers:
        lines.append(f"  {provider['name']}  ({provider['baseUrl']})")
        for key in provider["keys"]:
            lines.append(
                f"      {key['id']}  len={key['length']}  ...{key['tail']}  [{key['kind']}]"
            )

    lines.append("")
    lines.append("=== 结论 ===")
    errors = [f for f in report.findings if f.severity == "error"]
    warns = [f for f in report.findings if f.severity != "error"]
    if not report.findings:
        lines.append("  ✓ 未发现配置层面的已知故障形态")
    for finding in errors + warns:
        lines.append(finding.render())
    if errors:
        lines.append("")
        lines.append(
            "  处置: 优先用 UI 修正（选中正确 key / 重新切号），不要外部改 JSON；"
        )
        lines.append("        改后执行 outputs/verify-sidecar-10909.sh 确认 `10909: OPEN`。")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cockpit-dir",
        type=pathlib.Path,
        default=DEFAULT_COCKPIT_DIR,
        help="Cockpit data directory (default: ~/.antigravity_cockpit)",
    )
    parser.add_argument(
        "--codex-config",
        type=pathlib.Path,
        default=DEFAULT_CODEX_CONFIG,
        help="desktop Codex config.toml (default: ~/.codex/config.toml)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if not (args.cockpit_dir / "codex_model_providers.json").exists():
        print(f"no provider registry at {args.cockpit_dir}", file=sys.stderr)
        return CANNOT_CHECK

    report = build_report(args.cockpit_dir)
    config_target = read_config_target(args.codex_config)

    if args.json:
        print(
            json.dumps(
                {
                    "bindAccountId": report.bind_account_id,
                    "bindNeedsGateway": report.bind_needs_gateway,
                    "boundKey": (
                        {
                            "provider": report.bound_key.provider_name,
                            "keyId": report.bound_key.key_id,
                            "length": report.bound_key.length,
                            "tail": report.bound_key.tail,
                        }
                        if report.bound_key
                        else None
                    ),
                    "configTarget": config_target,
                    "providers": report.providers,
                    "findings": [
                        {"code": f.code, "detail": f.detail, "severity": f.severity}
                        for f in report.findings
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render(report, config_target))

    return FINDINGS if any(f.severity == "error" for f in report.findings) else OK


if __name__ == "__main__":
    raise SystemExit(main())
