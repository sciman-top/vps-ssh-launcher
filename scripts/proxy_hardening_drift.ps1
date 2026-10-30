#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$ObfsPort = 8444
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

# Read-only drift gate for the three anti-blocking lanes. A vasma reinstall or
# a menu re-configuration regenerates the REALITY source configs (restoring the
# empty shortId) and can re-fetch the decoy site, silently undoing the hooks
# without touching any service: everything keeps "working" while the endpoint
# gets noisier to probe. Nothing else in the fleet notices that, so this lane
# reports a single greppable verdict the operator can wire into a patrol.
#
# It never writes. `HARDENING_DRIFT=NONE` is the clean verdict;
# `HARDENING_DRIFT=<lane,...>` names what regressed and which script re-applies
# it, so the fix is a copy of the command rather than a memory exercise.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
OBFS_PORT="__OBFS_PORT__"
XRAY_CONF="${VPS_DRIFT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_SOURCE_DIR="${VPS_DRIFT_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_ROOT="${VPS_DRIFT_NGINX_ROOT:-/usr/share/nginx/html}"
drift=()

echo '==camouflage-page=='
root_index="$NGINX_ROOT/index.html"
decoy_index="$NGINX_ROOT/en/index.html"
if [ ! -f "$root_index" ]; then
    echo 'camouflage=missing-root'
    drift+=('camouflage')
elif grep -qE 'Welcome to nginx!|If you see this page' "$root_index" 2>/dev/null; then
    echo 'camouflage=stock-default'
    drift+=('camouflage')
elif [ ! -f "$decoy_index" ]; then
    # A vasma reinstall that re-fetched the decoy can drop en/ while the root
    # page survives; that breaks the next promotion, so surface it as drift.
    echo 'camouflage=decoy-source-missing'
    drift+=('camouflage')
elif ! cmp -s "$root_index" "$decoy_index"; then
    # A root page that is neither stock nor the current decoy is still a
    # plausible site; report it but keep the verdict distinct so an operator
    # can tell "someone customised it" from "the lane was reverted".
    echo 'camouflage=root-differs-from-decoy'
else
    echo 'camouflage=ok'
fi

echo '==reality-shortid=='
empty_xray="$(python3 - "$XRAY_CONF" <<'PY'
import glob, json, os, sys
conf = sys.argv[1]
found = 0
invalid = False
for path in glob.glob(os.path.join(conf, '*.json')):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        if '' in (rs.get('shortIds') or []):
            found += 1
print(found)
raise SystemExit(2 if invalid else 0)
PY
)" || empty_xray=error
empty_sb="$(python3 - "$SB_SOURCE_DIR" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
found = 0
invalid = False
for path in glob.glob(os.path.join(root, '*.json')):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        reality = ((ib.get('tls') or {}).get('reality')) or {}
        if '' in (reality.get('short_id') or []):
            found += 1
print(found)
raise SystemExit(2 if invalid else 0)
PY
)" || empty_sb=error
echo "xray_empty_short_id=${empty_xray}"
echo "singbox_empty_short_id=${empty_sb}"
if [ "$empty_xray" != '0' ] || [ "$empty_sb" != '0' ]; then
    drift+=('reality-shortid')
fi

echo '==xray-port-collision=='
collision_state="$(python3 - "$XRAY_CONF" <<'PY'
import glob
import json
import os
import sys

conf = sys.argv[1]
seen = {}
invalid = False
for path in sorted(glob.glob(os.path.join(conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        port = ib.get('port')
        if not isinstance(port, int) or port <= 0:
            continue
        seen.setdefault(port, []).append(
            f"{os.path.basename(path)}:{ib.get('tag') or ib.get('protocol') or 'untagged'}"
        )
duplicates = [
    f"{port}=" + ",".join(labels)
    for port, labels in sorted(seen.items())
    if len(labels) > 1
]
print(f"xray_duplicate_ports={','.join(duplicates) if duplicates else 'none'}")
raise SystemExit(2 if invalid else 0)
PY
)" || collision_state='xray_duplicate_ports=invalid'
printf '%s\n' "$collision_state"
if ! printf '%s\n' "$collision_state" | grep -q '^xray_duplicate_ports=none$'; then
    drift+=('xray-port-collision')
fi

echo '==hysteria2-obfs=='
fragment="$SB_SOURCE_DIR/98_vps_ssh_launcher_hysteria2_obfs.json"
base_present=0
if [ -f "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" ]; then base_present=1; fi
if [ "$base_present" -eq 0 ]; then
    echo 'obfs=not-applicable'
elif [ ! -f "$fragment" ]; then
    echo 'obfs=fragment-missing'
    drift+=('hysteria2-obfs')
elif ! ss -lun 2>/dev/null | grep -q ":$OBFS_PORT "; then
    echo 'obfs=fragment-present-not-listening'
    drift+=('hysteria2-obfs')
else
    echo 'obfs=ok'
fi

echo '==hysteria2-masquerade=='
if [ "$base_present" -eq 0 ]; then
    echo 'masquerade=not-applicable'
else
    masquerade_state="$(python3 - "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" \
        "$SB_SOURCE_DIR/97_vps_ssh_launcher_singbox_log.json" <<'PY'
import json
import os
import sys

base_path, log_path = sys.argv[1:3]
masquerade_ok = False
log_ok = False
url = ''
try:
    data = json.load(open(base_path, encoding='utf-8'))
    for ib in data.get('inbounds', []) or []:
        if ib.get('type') != 'hysteria2' or ib.get('listen_port') != 443:
            continue
        masq = ib.get('masquerade') or {}
        if (
            masq.get('type') == 'proxy'
            and masq.get('rewrite_host') is True
            and str(masq.get('url') or '').startswith('https://')
        ):
            masquerade_ok = True
            url = str(masq.get('url'))
    if os.path.isfile(log_path):
        log_ok = json.load(open(log_path, encoding='utf-8')).get('log', {}).get('level') == 'info'
except Exception:
    masquerade_ok = False
    log_ok = False
print(f"masquerade_set={'yes' if masquerade_ok else 'no'}")
print(f"log_level_pinned={'yes' if log_ok else 'no'}")
print(f"masquerade_url={url or 'none'}")
PY
    )" || masquerade_state='masquerade=invalid'
    printf '%s\n' "$masquerade_state"
    if ! printf '%s\n' "$masquerade_state" | grep -q '^masquerade_set=yes$' ||
       ! printf '%s\n' "$masquerade_state" | grep -q '^log_level_pinned=yes$'; then
        drift+=('hysteria2-masquerade')
    fi
fi

echo '==listeners=='
probe_listening() {
    # $1 = tcp|udp, $2 = port. `ss` prints the local address in column 4; require
    # the port to be a trailing ":<port>" so ":443" cannot also match ":4433".
    # Explicit branches instead of a computed -Hl"$1"n: under an if-then this
    # form is the one already proven in this fleet's wrappers.
    if [ "$1" = 'udp' ]; then
        ss -Hlun 2>/dev/null | awk -v p=":$2" 'index($4, p) == length($4) - length(p) + 1 { f = 1 } END { exit(f ? 0 : 1) }'
    else
        ss -Hltn 2>/dev/null | awk -v p=":$2" 'index($4, p) == length($4) - length(p) + 1 { f = 1 } END { exit(f ? 0 : 1) }'
    fi
}
for probe in tcp:443 tcp:21470 tcp:22752 tcp:33387 tcp:12378 udp:443 "udp:$OBFS_PORT"; do
    proto="${probe%%:*}"
    port="${probe##*:}"
    if probe_listening "$proto" "$port"; then
        echo "listener_${proto}_${port}=up"
    else
        echo "listener_${proto}_${port}=down"
    fi
done

if [ "${#drift[@]}" -eq 0 ]; then
    echo 'HARDENING_DRIFT=NONE'
    exit 0
fi
echo "HARDENING_DRIFT=$(IFS=,; echo "${drift[*]}")"
exit 3
'@.Replace("__OBFS_PORT__", "$ObfsPort")

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "hardening-drift"
