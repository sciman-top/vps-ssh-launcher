#requires -Version 7
# WorkBuddy client-surface bridge tool (2026-11-17 luode0320 cutover).
# The legacy realm-pin plugin pins died with the mmqz single
# plugin: the luode pair exposes dynamic catalogs narrowed by
# oauth-excluded-models, and the legacy client names ride on the host-level
# oauth-model-alias fork bridge. This tool keeps that bridge honest: it
# reads the checked-in route manifest (scripts/remote/cpa_provider_routes.json)
# as the single source of truth, audits the deployed config against it, and
# under -Apply projects the manifest's workbuddy-ai-provider alias entries
# into config.yaml (removing a leftover legacy "workbuddy" channel key).
# The excluded-models surface itself is computed from the live dynamic
# catalog at cutover time and only audited here — the
# ==client-model-catalog== doctor gate stays the fail-closed enforcer.
[CmdletBinding()]
param(
  [ValidateSet("bwg")]
  [string]$Profile = "bwg",
  [string]$Config = "",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-LaneScript -ScriptRoot $PSScriptRoot -Config $Config -AllowPyLauncher

$manifestPath = Join-Path $PSScriptRoot "remote\cpa_provider_routes.json"
$manifestJson = Get-Content -Raw -Encoding utf8 $manifestPath
$manifestB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($manifestJson))

# Validate the manifest shape before anything is projected: since the
# 2026-11-17 user-directed rename+retirement the bridge is exactly the one
# intl fork alias (deepseek-v4.1-flash-intl riding on the declared-but-closed
# deepseek-v4.1-flash-sg; hy4flash-free is retired outright), and the
# legacy workbuddy channel must stay gone. Note the deployed bridge lives
# inside config.yaml's oauth: block (oauth.model-alias); this tool's surgery
# still targets the legacy top-level spelling and therefore refuses against
# the cutover config — the transaction scripts own that surface now.
$manifest = $manifestJson | ConvertFrom-Json
$aliasEntries = $manifest."oauth_model_aliases"."workbuddy-ai-provider"
if (-not $aliasEntries -or $aliasEntries.Count -ne 1) {
  throw "Manifest must carry exactly one workbuddy-ai-provider alias entry; got $($aliasEntries.Count)."
}
$aliases = @($aliasEntries | ForEach-Object { $_.alias })
$expected = @("deepseek-v4.1-flash-intl")
if (($aliases | Sort-Object) -join "," -ne ($expected | Sort-Object) -join ",") {
  throw "Unexpected alias set: $($aliases -join ',')."
}
if ($manifest."oauth_model_aliases".PSObject.Properties.Name -contains "workbuddy") {
  throw "Legacy workbuddy alias channel is still declared in the manifest."
}

$applyLiteral = if ($Apply) { "1" } else { "0" }
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
MANIFEST_B64="__MANIFEST_B64__"
CONFIG="${WORKBUDDY_ALIAS_BRIDGE_CONFIG:-/opt/cliproxyapi/config.yaml}"
LOCK_FILE="${WORKBUDDY_ALIAS_BRIDGE_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
TRAFFIC_LOCK="${WORKBUDDY_ALIAS_BRIDGE_TRAFFIC_LOCK:-/run/cpa-admission/requests.lock}"
BACKUP_ROOT="${WORKBUDDY_ALIAS_BRIDGE_BACKUP_ROOT:-/root}"

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

python3 - "$CONFIG" "$APPLY" "$BACKUP_ROOT" "$MANIFEST_B64" <<'PY'
import base64
import hashlib
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
apply = sys.argv[2] == "1"
backup_root = Path(sys.argv[3])
manifest = yaml.safe_load(base64.b64decode(sys.argv[4]).decode("utf-8"))

entries = manifest["oauth_model_aliases"]["workbuddy-ai-provider"]
if len(entries) != 1:
    raise SystemExit("REFUSE manifest bridge must carry exactly one entry")

with config_path.open("r", encoding="utf-8", newline="") as handle:
    before_text = handle.read()
before_data = yaml.safe_load(before_text)
if not isinstance(before_data, dict):
    raise SystemExit("REFUSE config.yaml root must be a mapping")

alias_section = before_data.get("oauth-model-alias")
if not isinstance(alias_section, dict) or not alias_section:
    raise SystemExit(
        "REFUSE oauth-model-alias section missing; the cutover transaction owns its creation"
    )

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

current = alias_section.get("workbuddy-ai-provider")
legacy_present = "workbuddy" in alias_section
already = (
    isinstance(current, list)
    and len(current) == len(entries)
    and sorted(map(lambda e: (e.get("name"), e.get("alias"), e.get("fork", False)), current))
    == sorted(map(lambda e: (e.get("name"), e.get("alias"), bool(e.get("fork"))), entries))
    and not legacy_present
)
if already:
    print("WORKBUDDY_ALIAS_BRIDGE=ALREADY_APPLIED aliases=deepseek-v4.1-flash-intl-only")
    raise SystemExit(0)

# Section-level text surgery: locate the oauth-model-alias top-level key and
# the workbuddy-ai-provider channel inside it, replace that channel's item
# block with the manifest shape, and drop a legacy workbuddy channel if one
# lingers. Everything else must stay byte-identical.
header = re.search(r"(?m)^oauth-model-alias:[ \t]*(?:#.*)?\r?$", before_text)
if not header:
    raise SystemExit("REFUSE oauth-model-alias header line not found")
lines = before_text.splitlines(keepends=True)
section_start = None
for index, line in enumerate(lines):
    if re.match(r"^oauth-model-alias:", line):
        section_start = index
        break
section_end = len(lines)
for index in range(section_start + 1, len(lines)):
    if re.match(r"^[A-Za-z0-9_-]+:", lines[index]):
        section_end = index
        break
section_lines = lines[section_start:section_end]

def channel_span(name):
    begin = None
    for offset, line in enumerate(section_lines):
        if re.match(r"^  " + re.escape(name) + r":", line):
            begin = offset
            break
    if begin is None:
        return None
    finish = len(section_lines)
    for offset in range(begin + 1, len(section_lines)):
        if re.match(r"^  [A-Za-z0-9_-]+:", section_lines[offset]):
            finish = offset
            break
    return begin, finish

rendered = (
    yaml.safe_dump(entries, default_flow_style=False, sort_keys=True, allow_unicode=True)
    .strip("\n")
    .splitlines()
)
if section_lines and not section_lines[-1].endswith("\n"):
    section_lines[-1] = section_lines[-1] + "\n"
new_channel = ["  workbuddy-ai-provider:\n"] + [
    "    " + line + "\n" for line in rendered
]
spans = []
ai_span = channel_span("workbuddy-ai-provider")
if ai_span:
    spans.append((ai_span[0], ai_span[1], new_channel))
else:
    # Channel missing entirely: append it at the end of the section.
    spans.append((len(section_lines), len(section_lines), new_channel))
legacy_span = channel_span("workbuddy")
if legacy_span:
    spans.append((legacy_span[0], legacy_span[1], []))
spans.sort()
rebuilt: list[str] = []
cursor = 0
for begin, finish, replacement in spans:
    if begin < cursor:
        raise SystemExit("REFUSE overlapping channel spans during surgery")
    rebuilt.extend(section_lines[cursor:begin])
    rebuilt.extend(replacement)
    cursor = finish
rebuilt.extend(section_lines[cursor:])
updated_lines = lines[:section_start] + rebuilt + lines[section_end:]
updated_text = "".join(updated_lines)

after_data = yaml.safe_load(updated_text)
expected_paths = {("oauth-model-alias", "workbuddy-ai-provider")}
if legacy_present:
    expected_paths.add(("oauth-model-alias", "workbuddy"))
actual_diff = set(diff_paths(before_data, after_data))
if actual_diff != expected_paths:
    raise SystemExit(f"REFUSE unexpected YAML mutation paths={sorted(actual_diff)!r}")
bridged = after_data["oauth-model-alias"]["workbuddy-ai-provider"]
if sorted(e["alias"] for e in bridged) != sorted(e["alias"] for e in entries):
    raise SystemExit("REFUSE post-parse alias values mismatch the manifest")
if "workbuddy" in after_data["oauth-model-alias"]:
    raise SystemExit("REFUSE legacy workbuddy channel survived the surgery")

if not apply:
    print(
        "WORKBUDDY_ALIAS_BRIDGE_DRY_RUN "
        f"current_aliases={sorted(e['alias'] for e in (current or []))} "
        f"target_aliases={sorted(e['alias'] for e in entries)} "
        f"legacy_channel_present={str(legacy_present).lower()}"
    )
    raise SystemExit(0)

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
backup_dir = backup_root / f"cpa-workbuddy-alias-bridge-backup-{stamp}"
backup_dir.mkdir(parents=True, mode=0o700)
os.chmod(backup_dir, 0o700)
backup_path = backup_dir / "config.yaml"
shutil.copy2(config_path, backup_path)

temp_path = config_path.with_name(".workbuddy-alias-bridge.tmp")
with temp_path.open("w", encoding="utf-8", newline="") as handle:
    handle.write(updated_text)
os.chmod(temp_path, 0o600)
os.replace(temp_path, config_path)

with config_path.open("rb") as handle:
    written = handle.read()
if hashlib.sha256(written).hexdigest() != hashlib.sha256(updated_text.encode("utf-8")).hexdigest():
    raise SystemExit("REFUSE post-write digest mismatch")
print(f"WORKBUDDY_ALIAS_BRIDGE=APPLIED backup={backup_path} aliases=deepseek-v4.1-flash-intl-only")
PY
'@
$remoteCommand = $remoteCommand.Replace("__APPLY__", $applyLiteral).Replace("__MANIFEST_B64__", $manifestB64)
if ($remoteCommand -match "__APPLY__|__MANIFEST_B64__") {
  throw "Unfilled WorkBuddy alias-bridge placeholders."
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile $Profile -Command $remoteCommand `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
