#!/usr/bin/env python3
"""只读一致性检查：fq capability path 四层对齐。

背景：`scripts/cpa_bwg_guardrails.ps1 -RotatePath` 只轮换远端 nginx 的
capability path，本地零同步。Cockpit 在 `codex_start_default_with_prepared_profile`
时会用账号文件里的 `api_base_url` 副本重写 `~/.codex/config.toml`，
所以四层必须同时对齐，否则客户端表现为 nginx 404（route=other）。

四层：
  1. nginx       远端 /etc/nginx/conf.d/cpa-gateway.conf 的 location path
  2. provider    ~/.antigravity_cockpit/codex_model_providers.json 的 base_url
  3. account     ~/.antigravity_cockpit/codex_accounts/<id>.json 的 api_base_url（AES）
  4. config      ~/.codex/config.toml 的 codex_local_access.base_url

本脚本只读：不写任何文件、不改远端。不一致时退出码 1。

用法：
  ./.venv/Scripts/python.exe outputs/_fq_path_consistency.py
  ./.venv/Scripts/python.exe outputs/_fq_path_consistency.py --json
  ./.venv/Scripts/python.exe outputs/_fq_path_consistency.py --skip-remote
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path

PATH_RE = re.compile(r"/[0-9a-f]{16}/v1")
REDACT_TAIL = "…"


def redact(path: str) -> str:
    """capability path 属敏感信息，只回显前 4 位与长度。"""
    token = path.strip("/").split("/")[0] if path else ""
    if len(token) < 6:
        return "<none>"
    return f"{token[:4]}{REDACT_TAIL}{len(token)}hex"


def extract_path(url: str) -> str:
    match = PATH_RE.search(url or "")
    return match.group(0) if match else ""


def read_nginx_path(profile: str, repo_root: Path) -> tuple[str, str]:
    """通过 ssh_tool 只读远端 nginx 配置，返回 (path, note)。"""
    py = repo_root / ".venv" / "Scripts" / "python.exe"
    cmd = [
        str(py),
        str(repo_root / "ssh_tool.py"),
        "--profile",
        profile,
        "run",
        "--command",
        "sudo -n grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -n 1",
    ]
    try:
        proc = subprocess.run(  # nosec B603
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"ssh failed: {type(exc).__name__}"
    if proc.returncode != 0:
        return "", f"ssh exit {proc.returncode}"
    path = extract_path(proc.stdout.strip())
    return path, "" if path else "no path matched"


def read_provider_path(cockpit_dir: Path) -> tuple[str, str]:
    providers_file = cockpit_dir / "codex_model_providers.json"
    if not providers_file.exists():
        return "", "codex_model_providers.json missing"
    try:
        raw = json.loads(providers_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "", f"providers unreadable: {type(exc).__name__}"
    items = raw if isinstance(raw, list) else raw.get("providers") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        base_url = str(item.get("base_url") or item.get("baseUrl") or "")
        if "fq.sciman.top" in base_url:
            path = extract_path(base_url)
            if path:
                return path, ""
    return "", "no fq provider entry"


def decrypt_account(account_file: Path, key_file: Path) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
    envelope = json.loads(account_file.read_text(encoding="utf-8"))
    plaintext = AESGCM(key).decrypt(
        base64.b64decode(envelope["nonce"]),
        base64.b64decode(envelope["ciphertext"]),
        None,
    )
    return json.loads(plaintext)


def read_account_path(cockpit_dir: Path) -> tuple[str, str, str]:
    """返回 (path, account_id, note)。account_id 供人工定位用。"""
    instances_file = cockpit_dir / "codex_instances.json"
    account_id = ""
    if instances_file.exists():
        try:
            instances = json.loads(instances_file.read_text(encoding="utf-8"))
            account_id = str(
                (instances.get("defaultSettings") or {}).get("bindAccountId") or ""
            )
        except (OSError, json.JSONDecodeError):
            pass
    if not account_id:
        runtime_file = cockpit_dir / "codex_runtime_mode.json"
        if runtime_file.exists():
            try:
                account_id = str(
                    json.loads(runtime_file.read_text(encoding="utf-8")).get(
                        "currentAccountId"
                    )
                    or ""
                )
            except (OSError, json.JSONDecodeError):
                pass
    if not account_id:
        return "", "", "no bound account id"
    account_file = cockpit_dir / "codex_accounts" / f"{account_id}.json"
    key_file = cockpit_dir / "secure-account-storage.key"
    if not account_file.exists():
        return "", account_id, "account file missing"
    if not key_file.exists():
        return "", account_id, "secure-account-storage.key missing"
    try:
        payload = decrypt_account(account_file, key_file)
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需报告任意解密失败
        return "", account_id, f"decrypt failed: {type(exc).__name__}"
    return extract_path(str(payload.get("api_base_url") or "")), account_id, ""


def read_config_path(config_file: Path) -> tuple[str, str]:
    if not config_file.exists():
        return "", "config.toml missing"
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return "", f"config unreadable: {type(exc).__name__}"
    providers = data.get("model_providers") or {}
    active = str(data.get("model_provider") or "")
    entry = providers.get(active) if active else None
    if not isinstance(entry, dict):
        entry = providers.get("codex_local_access")
    if not isinstance(entry, dict):
        return "", "no active provider entry"
    return extract_path(str(entry.get("base_url") or "")), ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="bwg", help="ssh_tool profile (default: bwg)")
    parser.add_argument("--skip-remote", action="store_true", help="只查本地三层")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    home = Path.home()
    cockpit_dir = home / ".antigravity_cockpit"
    config_file = home / ".codex" / "config.toml"

    layers: dict[str, dict[str, str]] = {}

    if args.skip_remote:
        layers["nginx"] = {"path": "", "note": "skipped"}
    else:
        path, note = read_nginx_path(args.profile, repo_root)
        layers["nginx"] = {"path": path, "note": note}

    path, note = read_provider_path(cockpit_dir)
    layers["provider"] = {"path": path, "note": note}

    path, account_id, note = read_account_path(cockpit_dir)
    layers["account"] = {"path": path, "note": note, "account_id": account_id}

    path, note = read_config_path(config_file)
    layers["config"] = {"path": path, "note": note}

    checked = {k: v for k, v in layers.items() if v.get("note") != "skipped"}
    distinct = {v["path"] for v in checked.values() if v.get("path")}
    missing = [k for k, v in checked.items() if not v.get("path")]
    consistent = len(distinct) == 1 and not missing

    if args.json:
        print(
            json.dumps(
                {
                    "consistent": consistent,
                    "layers": {
                        k: {
                            "redacted": redact(v.get("path", "")),
                            "note": v.get("note", ""),
                            **(
                                {"account_id": v["account_id"]}
                                if "account_id" in v
                                else {}
                            ),
                        }
                        for k, v in layers.items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("fq capability path consistency (read-only)")
        print("-" * 58)
        for name in ("nginx", "provider", "account", "config"):
            entry = layers[name]
            state = redact(entry.get("path", "")) if entry.get("path") else "MISSING"
            note = entry.get("note", "")
            extra = f"  [{note}]" if note and note != "skipped" else ""
            print(f"  {name:<9} {state:<14}{extra}")
        print("-" * 58)
        if consistent:
            print("CONSISTENCY=OK")
        else:
            print("CONSISTENCY=MISMATCH")
            if missing:
                print(f"  unreadable layers: {', '.join(missing)}")
            if len(distinct) > 1:
                print(f"  distinct paths: {len(distinct)}")
            print("  修复顺序：远端轮换 -> provider 表 -> 账号副本 -> 重启 Cockpit -> 启动 Codex")
            print("  注意：Cockpit 运行中不要手改 provider 表（UI 保存会整文件覆写）")

    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
