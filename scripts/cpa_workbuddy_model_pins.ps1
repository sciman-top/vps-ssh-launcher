#requires -Version 7
[CmdletBinding()]
param(
  [ValidateSet("bwg")]
  [string]$Profile = "bwg",
  [string]$Config = "",
  [string]$ModelsCn = "deepseek-v4.1-flash,hy4free",
  [string]$ModelsIntl = "deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free",
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
# Pin shape is validated whenever a value is provided, not only under -Apply:
# these strings are interpolated into the remote bash payload in read mode
# too, so an unvalidated value must never reach the projection. The payload's
# python token check runs only after bash has already parsed the assignment,
# so a value carrying quotes or substitutions would break out of
# MODELS_CN="..." before any REFUSE could fire (vasma_kernel_update_cron.ps1
# carries the same host-side rule).
if ($ModelsCn -notmatch '^[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*$' -or
    $ModelsIntl -notmatch '^[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*$') {
  throw "-ModelsCn/-ModelsIntl must be comma-separated model tokens matching ^[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*$; got cn='$ModelsCn' intl='$ModelsIntl'."
}
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
MODELS_CN="__MODELS_CN__"
MODELS_INTL="__MODELS_INTL__"
CONFIG="${WORKBUDDY_MODEL_PINS_CONFIG:-/opt/cliproxyapi/config.yaml}"
LOCK_FILE="${WORKBUDDY_MODEL_PINS_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
TRAFFIC_LOCK="${WORKBUDDY_MODEL_PINS_TRAFFIC_LOCK:-/run/cpa-admission/requests.lock}"
BACKUP_ROOT="${WORKBUDDY_MODEL_PINS_BACKUP_ROOT:-/root}"

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

python3 - "$CONFIG" "$MODELS_CN" "$MODELS_INTL" "$APPLY" "$BACKUP_ROOT" <<'PY'
import hashlib
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
desired_cn = sys.argv[2].strip()
desired_intl = sys.argv[3].strip()
apply = sys.argv[4] == "1"
backup_root = Path(sys.argv[5])

if not desired_cn or not desired_intl:
    raise SystemExit("REFUSE models_cn and models_intl must be non-empty strings")

for label, val in [("models_cn", desired_cn), ("models_intl", desired_intl)]:
    parts = [p.strip() for p in val.split(",") if p.strip()]
    if not parts:
        raise SystemExit(f"REFUSE {label} has no valid model tokens")
    for part in parts:
        if not re.match(r"^[A-Za-z0-9._-]+$", part):
            raise SystemExit(f"REFUSE {label} contains invalid model token: {part!r}")


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
current_cn = str(before_section.get("models_cn", "")).strip()
current_intl = str(before_section.get("models_intl", "")).strip()

if current_cn == desired_cn and current_intl == desired_intl:
    print(f"WORKBUDDY_MODEL_PINS=ALREADY_APPLIED cn={desired_cn} intl={desired_intl}")
    raise SystemExit(0)

updated_text = before_text

if current_cn != desired_cn:
    pattern_cn = re.compile(
        r"(?m)^(?P<prefix>[ \t]+models_cn:[ \t]*)"
        r"(?P<value>[^\r\n#]+?)"
        r"(?P<suffix>[ \t]*(?:#.*)?)\r?$"
    )
    matches_cn = list(pattern_cn.finditer(updated_text))
    if len(matches_cn) != 1:
        raise SystemExit(
            f"REFUSE expected exactly one models_cn line, found {len(matches_cn)}"
        )
    updated_text = (
        updated_text[: matches_cn[0].start("value")]
        + desired_cn
        + updated_text[matches_cn[0].end("value") :]
    )

if current_intl != desired_intl:
    pattern_intl = re.compile(
        r"(?m)^(?P<prefix>[ \t]+models_intl:[ \t]*)"
        r"(?P<value>[^\r\n#]+?)"
        r"(?P<suffix>[ \t]*(?:#.*)?)\r?$"
    )
    matches_intl = list(pattern_intl.finditer(updated_text))
    if len(matches_intl) != 1:
        raise SystemExit(
            f"REFUSE expected exactly one models_intl line, found {len(matches_intl)}"
        )
    updated_text = (
        updated_text[: matches_intl[0].start("value")]
        + desired_intl
        + updated_text[matches_intl[0].end("value") :]
    )

after_data = parse(updated_text)
after_section = section(after_data)
expected_paths = set()
if current_cn != desired_cn:
    expected_paths.add(("plugins", "configs", "workbuddy", "models_cn"))
if current_intl != desired_intl:
    expected_paths.add(("plugins", "configs", "workbuddy", "models_intl"))

actual_diff = set(diff_paths(before_data, after_data))
if actual_diff != expected_paths:
    raise SystemExit(f"REFUSE unexpected YAML mutation paths={actual_diff!r}")
if after_section.get("models_cn") != desired_cn or after_section.get("models_intl") != desired_intl:
    raise SystemExit("REFUSE post-parse model pin values mismatch desired")

if not apply:
    print(
        "WORKBUDDY_MODEL_PINS_DRY_RUN "
        f"current_cn={current_cn} target_cn={desired_cn} "
        f"current_intl={current_intl} target_intl={desired_intl}"
    )
    raise SystemExit(0)

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
backup_dir = backup_root / f"cpa-workbuddy-model-pins-backup-{stamp}"
backup_dir.mkdir(parents=True, mode=0o700)
os.chmod(backup_dir, 0o700)
backup_path = backup_dir / "config.yaml"
shutil.copy2(config_path, backup_path)
os.chmod(backup_path, 0o600)
backup_bytes = config_path.read_bytes()

try:
    with config_path.open("r+", encoding="utf-8", newline="") as handle:
        handle.seek(0)
        handle.write(updated_text)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    after_bytes = config_path.read_bytes()
    verify_data = parse(after_bytes.decode("utf-8"))
    verify_section = section(verify_data)
    if verify_section.get("models_cn") != desired_cn or verify_section.get("models_intl") != desired_intl:
        raise RuntimeError("post-write WorkBuddy models pin mismatch")
except Exception:
    with config_path.open("r+", encoding="utf-8", newline="") as handle:
        handle.seek(0)
        handle.truncate()
        handle.write(backup_bytes.decode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    raise

print(
    "WORKBUDDY_MODEL_PINS=APPLIED "
    f"cn={desired_cn} intl={desired_intl}"
)
print(f"SHA256_BEFORE={digest(before_text.encode('utf-8'))}")
print(f"SHA256_AFTER={digest(after_bytes)}")
print(f"BACKUP={backup_path}")
PY

if [ "$APPLY" = "1" ]; then
  sleep 3
  echo "CONFIG_RELOAD_SETTLE=3s"
fi
'@.Replace("__APPLY__", $applyLiteral).Replace("__MODELS_CN__", $ModelsCn).Replace("__MODELS_INTL__", $ModelsIntl)

Invoke-LauncherRemoteCommand `
  -Python $script:Python `
  -ProjectRoot $repoRoot `
  -Config $Config `
  -Profile $Profile `
  -Command $remoteCommand `
  -RemoteTempPrefix "workbuddy-model-pins"
