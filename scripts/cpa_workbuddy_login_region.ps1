#requires -Version 7
[CmdletBinding()]
param(
  [ValidateSet("bwg")]
  [string]$Profile = "bwg",
  [string]$Config = "",
  [ValidateSet("intl", "cn")]
  [string]$Region = "intl",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$applyLiteral = if ($Apply) { "1" } else { "0" }
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
REGION="__REGION__"
CONFIG="${WORKBUDDY_LOGIN_REGION_CONFIG:-/opt/cliproxyapi/config.yaml}"
LOCK_FILE="${WORKBUDDY_LOGIN_REGION_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
TRAFFIC_LOCK="${WORKBUDDY_LOGIN_REGION_TRAFFIC_LOCK:-/run/cpa-admission/requests.lock}"
BACKUP_ROOT="${WORKBUDDY_LOGIN_REGION_BACKUP_ROOT:-/root}"

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "REFUSE cpa_busy maintenance lock held"; exit 1; }

if [ "$APPLY" = "1" ]; then
  exec 8>"$TRAFFIC_LOCK"
  deadline=$((SECONDS + 45))
  until flock -n 8; do
    [ "$SECONDS" -lt "$deadline" ] || {
      echo "REFUSE cpa_traffic_not_idle requests.lock held"
      exit 75
    }
    sleep 1
  done
  echo "TRAFFIC_DRAIN=ACQUIRED"
fi

python3 - "$CONFIG" "$REGION" "$APPLY" "$BACKUP_ROOT" <<'PY'
import hashlib
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
desired = sys.argv[2].strip().lower()
apply = sys.argv[3] == "1"
backup_root = Path(sys.argv[4])

if desired not in {"cn", "intl"}:
    raise SystemExit("REFUSE login_region target must be cn or intl")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse(text):
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise SystemExit("REFUSE config.yaml root must be a mapping")
    return value


def section(config):
    plugins = config.get("plugins")
    configs = plugins.get("configs") if isinstance(plugins, dict) else None
    value = configs.get("workbuddy") if isinstance(configs, dict) else None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        raise SystemExit("REFUSE WorkBuddy plugin is not enabled")
    return value


def diff_paths(before, after, prefix=()):
    if type(before) is not type(after):
        return [prefix]
    if isinstance(before, dict):
        paths = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                paths.append(prefix + (key,))
            else:
                paths.extend(diff_paths(before[key], after[key], prefix + (key,)))
        return paths
    if isinstance(before, list):
        if len(before) != len(after):
            return [prefix]
        paths = []
        for index, (left, right) in enumerate(zip(before, after)):
            paths.extend(diff_paths(left, right, prefix + (index,)))
        return paths
    return [] if before == after else [prefix]


with config_path.open("r", encoding="utf-8", newline="") as handle:
    before_text = handle.read()
before_data = parse(before_text)
before_section = section(before_data)
current = before_section.get("login_region")
if current not in {"cn", "intl"}:
    raise SystemExit("REFUSE current WorkBuddy login_region must be cn or intl")

if current == desired:
    print(f"WORKBUDDY_LOGIN_REGION=ALREADY_APPLIED value={desired}")
    raise SystemExit(0)

pattern = re.compile(
    r"(?m)^(?P<prefix>[ \t]+login_region:[ \t]*)"
    r"(?P<value>cn|intl)"
    r"(?P<suffix>[ \t]*(?:#.*)?)\r?$"
)
matches = list(pattern.finditer(before_text))
if len(matches) != 1:
    raise SystemExit(
        f"REFUSE expected exactly one login_region line, found {len(matches)}"
    )
after_text = (
    before_text[: matches[0].start("value")]
    + desired
    + before_text[matches[0].end("value") :]
)
after_data = parse(after_text)
after_section = section(after_data)
expected_path = ("plugins", "configs", "workbuddy", "login_region")
actual_diff = diff_paths(before_data, after_data)
if actual_diff != [expected_path] or after_section.get("login_region") != desired:
    raise SystemExit(f"REFUSE unexpected YAML mutation paths={actual_diff!r}")

if not apply:
    print(
        "WORKBUDDY_LOGIN_REGION_DRY_RUN "
        f"current={current} target={desired}"
    )
    raise SystemExit(0)

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
backup_dir = backup_root / f"cpa-workbuddy-login-region-backup-{stamp}"
backup_dir.mkdir(parents=True, mode=0o700)
os.chmod(backup_dir, 0o700)
backup_path = backup_dir / "config.yaml"
shutil.copy2(config_path, backup_path)
os.chmod(backup_path, 0o600)
backup_bytes = config_path.read_bytes()

try:
    with config_path.open("r+", encoding="utf-8", newline="") as handle:
        handle.seek(0)
        handle.write(after_text)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    after_bytes = config_path.read_bytes()
    verify_data = parse(after_bytes.decode("utf-8"))
    if section(verify_data).get("login_region") != desired:
        raise RuntimeError("post-write WorkBuddy login_region mismatch")
except Exception:
    with config_path.open("r+", encoding="utf-8", newline="") as handle:
        handle.seek(0)
        handle.truncate()
        handle.write(backup_bytes.decode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    raise

print(
    "WORKBUDDY_LOGIN_REGION=APPLIED "
    f"previous={current} current={desired}"
)
print(f"SHA256_BEFORE={digest(before_text.encode('utf-8'))}")
print(f"SHA256_AFTER={digest(after_bytes)}")
print(f"BACKUP={backup_path}")
PY

if [ "$APPLY" = "1" ]; then
  # Keep the admission gate held briefly while fsnotify reloads the plugin
  # configuration, then release it before this process exits.
  sleep 3
  echo "CONFIG_RELOAD_SETTLE=3s"
fi
'@.Replace("__APPLY__", $applyLiteral).Replace("__REGION__", $Region)

Invoke-LauncherRemoteCommand `
  -Python $script:Python `
  -ProjectRoot $repoRoot `
  -Config $Config `
  -Profile $Profile `
  -Command $remoteCommand `
  -RemoteTempPrefix "workbuddy-login-region"
