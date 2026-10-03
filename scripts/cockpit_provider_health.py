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

Four invariants are checked (see docs/runbooks/cockpit-sidecar-guardrails.md):

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
4. **Desktop catalog routability.** Whichever gateway the desktop points at can
   only serve the model names that gateway actually routes. The desktop model
   picker is a *projection* built from the provider catalogs, so it keeps
   offering names (for example a provider-only alias) that the selected gateway
   answers 404 for. Every selectable slug must therefore resolve either at the
   public gateway's route manifest or at the local sidecar's upstream list.

Both desktop targets are accepted, and the choice is recorded rather than
policed: `local_gateway` keeps the model-alias rewrite layer and the local
concurrency gate in the path, while `public_gateway` removes the sidecar's
silent-stop failure mode at the cost of the local gate. What is *not* accepted
is pointing at a gateway that cannot serve the names the picker offers.

Read-only: no file is opened for writing and no secret is printed in full.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

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
DEFAULT_DESKTOP_CATALOG = pathlib.Path.home() / ".codex" / "cockpit-model-catalog.json"
DEFAULT_ROUTES = pathlib.Path(__file__).parent / "remote" / "cpa_provider_routes.json"
PUBLIC_GATEWAY_HOST = "fq.sciman.top"

# Slugs the desktop catalog carries that the *gateway* is not expected to route,
# because Cockpit owns them locally (auto-review, and the helper models it uses
# for titles and summaries). Listing them explicitly keeps the routability check
# from turning into a permanent, ignorable error.
DESKTOP_LOCAL_ONLY_MODELS = frozenset({"codex-auto-review"})

TARGET_LOCAL = "local_gateway"
TARGET_PUBLIC = "public_gateway"
TARGET_OTHER = "other"
TARGET_UNKNOWN = "unknown"


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
    # The registry entries as read, so a later check can still see each entry's
    # own `modelCatalog` (the `providers` summary above deliberately drops it).
    providers_raw: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, code: str, detail: str, severity: str = "error") -> None:
        self.findings.append(Finding(code, detail, severity))


def _md5(text: str) -> str:
    # Not a security primitive: this reproduces the digest the app embeds in an
    # account id (`codex_apikey_<md5(api_key)>`), so the check can reverse-map a
    # binding back to the provider entry that owns the key.
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


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


def classify_desktop_target(base_url: str | None) -> str:
    """Which gateway the desktop config points at.

    Both real targets are accepted. The classification exists so the report can
    say *which* mode is active, and so the routability check knows which
    catalogue to compare against.
    """
    if not base_url:
        return TARGET_UNKNOWN
    lowered = base_url.lower()
    if "10909" in lowered or "127.0.0.1" in lowered or "localhost" in lowered:
        return TARGET_LOCAL
    if PUBLIC_GATEWAY_HOST in lowered:
        return TARGET_PUBLIC
    return TARGET_OTHER


def load_route_aliases(routes_path: pathlib.Path) -> set[str]:
    """Every model name the public gateway advertises, from the route manifest."""
    try:
        routes = read_json(routes_path)
    except (OSError, ValueError):
        return set()
    if not isinstance(routes, dict):
        return set()
    aliases: set[str] = set()
    for group in ("providers", "oauth_routes"):
        for route in routes.get(group) or []:
            for model in route.get("models") or []:
                alias = model.get("alias")
                if isinstance(alias, str) and alias:
                    aliases.add(alias)
    return aliases


def load_desktop_catalog_slugs(catalog_path: pathlib.Path) -> list[str]:
    """Model slugs the desktop picker can offer, in catalog order."""
    try:
        catalog = read_json(catalog_path)
    except (OSError, ValueError):
        return []
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        return []
    slugs: list[str] = []
    for model in models:
        slug = model.get("slug") if isinstance(model, dict) else None
        if isinstance(slug, str) and slug:
            slugs.append(slug)
    return slugs


def load_sidecar_upstream_models(cockpit_dir: pathlib.Path) -> set[str]:
    """Every model name the local 10909 sidecar will forward or rewrite.

    The sidecar serves whatever its manifest lists under
    `providerGateway.upstreamModels`, plus every alias in `modelAliases` and the
    local-only ids in `modelIds`. Reading the manifest (not the provider entry)
    matters: the desktop talks to the sidecar, so the sidecar's list is the
    authority for what the local gateway can answer.
    """
    names: set[str] = set()
    for config_path in sorted(
        (cockpit_dir / "codex_provider_gateway_sidecars").glob("*/config.json")
    ):
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if config.get("port") != DEFAULT_GATEWAY_PORT:
            continue
        manifest_path = config_path.with_name("manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in manifest.get("apiKeys") or []:
            gateway = key.get("providerGateway") or {}
            for model in gateway.get("upstreamModels") or []:
                if isinstance(model, str) and model:
                    names.add(model)
        for alias in manifest.get("modelAliases") or []:
            name = alias.get("alias") if isinstance(alias, dict) else None
            if isinstance(name, str) and name:
                names.add(name)
        for model in manifest.get("modelIds") or []:
            if isinstance(model, str) and model:
                names.add(model)
    return names


def check_desktop_catalog_routable(
    target: str,
    slugs: Iterable[str],
    routable: set[str],
    local_only: frozenset[str] = DESKTOP_LOCAL_ONLY_MODELS,
) -> list[Finding]:
    """Names the picker offers that the selected gateway cannot serve.

    A name that is offered but unroutable fails deterministically (400
    `model_not_found`), which the user reads as a broken gateway rather than a
    stale catalogue entry.
    """
    if target not in (TARGET_LOCAL, TARGET_PUBLIC) or not routable:
        return []
    unroutable = sorted(
        {slug for slug in slugs if slug not in routable and slug not in local_only}
    )
    if not unroutable:
        return []
    return [
        Finding(
            "desktop-model-unroutable",
            f"桌面模型目录里有 {len(unroutable)} 个名字在 {target} 上不可路由："
            f"{', '.join(unroutable)} ⇒ 选中它们会稳定得到 400 model_not_found",
        )
    ]


def check_provider_catalogs(
    providers: list[dict[str, Any]],
    local_routable: set[str],
    public_routable: set[str],
    local_only: frozenset[str] = DESKTOP_LOCAL_ONLY_MODELS,
) -> list[Finding]:
    """Stale names inside *each* provider entry's own model catalog.

    The desktop catalog is the union Cockpit projects from these, so a stale name
    that only one entry carries never reaches the picker -- but it is still a
    callable name: whatever selects that provider can send it, and the gateway
    answers a deterministic 400. Measured case: a retired slot-2 alias kept
    living in one provider's catalog and produced 13 `400`s over 72h in the
    gateway journal, while the repo no longer referenced it anywhere.

    Reported as a warning, not an error: it only bites while that provider is the
    selected one, so it must not fail the check on its own.
    """

    findings: list[Finding] = []
    for provider in providers:
        name = str(provider.get("name") or "?")
        base_url = str(provider.get("baseUrl") or "")
        catalog = provider.get("modelCatalog")
        if not isinstance(catalog, list):
            continue
        target = classify_desktop_target(base_url)
        if target == TARGET_LOCAL:
            routable = local_routable
        elif target == TARGET_PUBLIC:
            routable = public_routable
        else:
            continue
        if not routable:
            continue
        stale = sorted(
            {
                slug
                for slug in catalog
                if isinstance(slug, str)
                and slug
                and slug not in routable
                and slug not in local_only
            }
        )
        if stale:
            findings.append(
                Finding(
                    "provider-catalog-stale",
                    f"provider「{name}」({base_url}) 的目录里有 {len(stale)} 个名字在 "
                    f"{target} 上不可路由：{', '.join(stale)} ⇒ 选中该 provider 后"
                    "点这些名字会稳定得到 400",
                    severity="warn",
                )
            )
    return findings


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
    report.providers_raw = providers

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
    target = classify_desktop_target(config_target)
    lines.append(f"  桌面目标模式        = {target}")
    if target == TARGET_LOCAL:
        lines.append(
            "      本地闸门 + 别名重写层在路径上；侧车静默停机是这一类的主要风险"
        )
    elif target == TARGET_PUBLIC:
        lines.append(
            "      直连公网：无本地闸门与别名层，但没有侧车静默停机；"
            "目录必须与网关可路由集合一致"
        )

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
            "  处置: 优先用 UI 修正，不要外部改 JSON（app 是运行中真源，外部改会打拉锯战）。"
        )
        if any(f.code == "desktop-model-unroutable" for f in errors):
            lines.append(
                "        目录类发现: 在「模型供应商」里重建所选网关的模型目录，"
                "让它成为该网关的投影；然后复跑本脚本确认已消失。"
            )
        if target == TARGET_LOCAL:
            lines.append(
                "        凭据类发现: 在 UI 里选中正确 key / 重新切号，"
                "然后执行 outputs/verify-sidecar-10909.sh 确认 `10909: OPEN`。"
            )
        else:
            lines.append(
                "        凭据类发现: 在 UI 里选中正确 key / 重新切号。"
                f"当前是 {target}，10909 的运行态与桌面可用性无关，不必用它验收。"
            )
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
    parser.add_argument(
        "--desktop-catalog",
        type=pathlib.Path,
        default=DEFAULT_DESKTOP_CATALOG,
        help="desktop model catalog (default: ~/.codex/cockpit-model-catalog.json)",
    )
    parser.add_argument(
        "--routes",
        type=pathlib.Path,
        default=DEFAULT_ROUTES,
        help="public gateway route manifest (default: scripts/remote/cpa_provider_routes.json)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--check-all-providers",
        action="store_true",
        help=(
            "also check every provider entry's own modelCatalog against the gateway "
            "it names (opt-in: an unused provider's stale names only bite when it is "
            "selected, so they are reported as warnings)"
        ),
    )
    args = parser.parse_args(argv)

    if not (args.cockpit_dir / "codex_model_providers.json").exists():
        print(f"no provider registry at {args.cockpit_dir}", file=sys.stderr)
        return CANNOT_CHECK

    report = build_report(args.cockpit_dir)
    config_target = read_config_target(args.codex_config)
    target = classify_desktop_target(config_target)

    if target == TARGET_OTHER:
        report.add(
            "desktop-target-unrecognised",
            f"桌面 config.toml 指向 {config_target}，既不是 10909 本地网关，"
            f"也不是 {PUBLIC_GATEWAY_HOST} 公网入口 ⇒ 无法判定它服务的模型集合",
            severity="warn",
        )
    elif target == TARGET_UNKNOWN:
        report.add(
            "desktop-target-missing",
            "未能在 config.toml 里找到 [model_providers.codex_local_access] 的 "
            "base_url ⇒ 桌面目标未知",
            severity="warn",
        )
    else:
        routable = (
            load_route_aliases(args.routes)
            if target == TARGET_PUBLIC
            else load_sidecar_upstream_models(args.cockpit_dir)
        )
        report.findings.extend(
            check_desktop_catalog_routable(
                target, load_desktop_catalog_slugs(args.desktop_catalog), routable
            )
        )

    if args.check_all_providers:
        report.findings.extend(
            check_provider_catalogs(
                report.providers_raw,
                load_sidecar_upstream_models(args.cockpit_dir),
                load_route_aliases(args.routes),
            )
        )

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
