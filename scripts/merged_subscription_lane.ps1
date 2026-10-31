#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg")]
  [string]$Profile,
  [ValidatePattern('^$|^[0-9a-f]{16,64}$')]
  [string]$Token,
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

# The token names a file that carries every credential, so it is generated
# once on this workstation and reused instead of being echoed by the lane.
$configDir = Split-Path -Parent $Config
$tokenStore = Join-Path $configDir "merged-sub-token.txt"
if (-not $Token) {
  if (Test-Path -LiteralPath $tokenStore -PathType Leaf) {
    $Token = (Get-Content -LiteralPath $tokenStore -Raw).Trim()
  } else {
    $Token = -join (1..32 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    Set-Content -LiteralPath $tokenStore -Value $Token -Encoding utf8NoBOM
  }
}
if ($Token -notmatch '^[0-9a-f]{16,64}$') {
  throw "Subscription token must be 16..64 hex characters."
}

$applyLiteral = if ($Apply) { "1" } else { "0" }

# One subscription for every lane. vasma only publishes the inbounds it owns,
# so the Hysteria2 obfs lane and the Cloudflare lane never appear there and
# every device needs two manual imports. This lane derives both of them from
# the live configs, merges all six nodes into one base64 list and drops it
# under the existing static root, where the 33387 vhost and the Cloudflare
# vhost both serve it without touching vasma's nginx files.
#
# The file name is an operator-held token supplied by the caller, so the
# credential-bearing URL is never printed by this lane. A vasma reinstall can
# rotate the UUID or the Hysteria2 password, which would leave a stale merged
# list; the drift gate compares the published UUID with the live inbound.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
TOKEN="__TOKEN__"
LOCK_FILE="${VPS_MERGED_SUB_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
ROOT="${VPS_MERGED_SUB_ROOT:-/usr/share/nginx/html}"
MERGED_DIR="$ROOT/s/merged"
TARGET="$MERGED_DIR/$TOKEN"
XRAY_CONF="${VPS_MERGED_SUB_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_CONF="${VPS_MERGED_SUB_SB_CONF:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_CONF_D="${VPS_MERGED_SUB_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
STATUS_DIR="${VPS_MERGED_SUB_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
TOKEN_FILE="${VPS_MERGED_SUB_TOKEN_FILE:-/var/lib/vps-ssh-launcher/merged-sub.token}"
BACKUP_PARENT="${VPS_MERGED_SUB_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/merged-sub.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=merged-sub\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

build() {
    python3 - "$XRAY_CONF" "$SB_CONF" "$NGINX_CONF_D" <<'PY'
import glob
import json
import os
import sys
import urllib.parse

xray_conf, sb_conf, nginx_conf_d = sys.argv[1:4]


def quote(value):
    return urllib.parse.quote(str(value), safe='')


reality = None
reality_xhttp = None
front_public = None
front_sni = None
loopback_fronts = {}
for path in sorted(glob.glob(os.path.join(xray_conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for entry in data.get('inbounds', []) or []:
        dokodemo = entry.get('settings') or {}
        if entry.get('protocol') == 'dokodemo-door' and str(dokodemo.get('address')) in ('127.0.0.1', 'localhost'):
            # vasma fronts the REALITY TCP lane with a dokodemo on 443 that
            # forwards to the loopback VLESS inbound. The client must dial the
            # wrapper's port, never the loopback one.
            loopback_fronts[dokodemo.get('port')] = entry.get('port')
        stream = entry.get('streamSettings') or {}
        security = stream.get('security') or 'none'
        network = stream.get('network') or 'tcp'
        clients = (entry.get('settings') or {}).get('clients') or []
        if entry.get('protocol') == 'vless' and security == 'reality' and network == 'tcp' and clients:
            rs = stream.get('realitySettings') or {}
            sids = [sid for sid in (rs.get('shortIds') or []) if sid]
            if sids:
                inner_port = entry.get('port')
                reality = {
                    'port': loopback_fronts.get(inner_port, inner_port),
                    'uuid': clients[0].get('id'),
                    'pbk': rs.get('publicKey') or '',
                    'sid': sids[0],
                    'sni': (rs.get('serverNames') or [''])[0],
                }
        if entry.get('protocol') == 'vless' and security == 'reality' and network == 'xhttp' and clients:
            rs = stream.get('realitySettings') or {}
            xs = stream.get('xhttpSettings') or {}
            reality_xhttp = {
                'port': entry.get('port'),
                'uuid': clients[0].get('id'),
                'pbk': rs.get('publicKey') or '',
                'sid': [sid for sid in (rs.get('shortIds') or []) if sid][0:1],
                'sni': (rs.get('serverNames') or [''])[0],
                'path': xs.get('path') or '/',
                'host': xs.get('host') or (rs.get('serverNames') or [''])[0],
            }
        if entry.get('protocol') == 'dokodemo-door' and entry.get('tag') == 'vps-launcher-xhttp-front':
            front_public = entry.get('port')

front_conf = os.path.join(nginx_conf_d, 'vps-launcher-xhttp-front.conf')
front_path = None
front_inner = None
if os.path.isfile(front_conf):
    text = open(front_conf, encoding='utf-8').read()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('server_name '):
            front_sni = line.split(None, 1)[1].rstrip(';')
        if line.startswith('location ^~ '):
            front_path = line.split(None, 2)[2].rstrip(' {')
        if line.startswith('proxy_pass http://127.0.0.1:'):
            front_inner = line.rsplit(':', 1)[1].rstrip(';')

hy2_base = None
hy2_obfs = None
for path in sorted(glob.glob(os.path.join(sb_conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for entry in data.get('inbounds', []) or []:
        if entry.get('type') != 'hysteria2':
            continue
        users = entry.get('users') or []
        if not users or not users[0].get('password'):
            continue
        obfs = entry.get('obfs') or {}
        item = {
            'port': entry.get('listen_port'),
            'password': users[0]['password'],
            'sni': (entry.get('tls') or {}).get('server_name') or '',
            'obfs': obfs.get('password') if obfs.get('type') == 'salamander' else None,
        }
        if obfs.get('type') == 'salamander':
            hy2_obfs = item
        else:
            hy2_base = item

cdn_conf = os.path.join(nginx_conf_d, 'vps-launcher-cdn.conf')
cdn_port = None
cdn_host = None
cdn_path = None
if os.path.isfile(cdn_conf):
    text = open(cdn_conf, encoding='utf-8').read()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('listen ') and line.endswith('ssl;'):
            cdn_port = int(line.split()[1])
        if line.startswith('server_name '):
            cdn_host = line.split(None, 1)[1].rstrip(';')
        if line.startswith('location ^~ '):
            cdn_path = line.split(None, 2)[2].rstrip(' {')

links = []
ports = []
if reality:
    sn = reality['sni']
    ports.append(reality['port'])
    links.append(
        'vless://%s@%s:%s?encryption=none&security=reality&sni=%s&fp=chrome&pbk=%s&sid=%s&type=tcp&flow=xtls-rprx-vision#bwg-reality-vision'
        % (reality['uuid'], sn, reality['port'], sn, reality['pbk'], reality['sid'])
    )
if reality_xhttp and reality_xhttp['sid']:
    ports.append(reality_xhttp['port'])
    sn = reality_xhttp['sni']
    links.append(
        'vless://%s@%s:%s?encryption=none&security=reality&sni=%s&fp=chrome&pbk=%s&sid=%s&type=xhttp&host=%s&path=%s&mode=auto#bwg-xhttp-reality'
        % (
            reality_xhttp['uuid'], sn, reality_xhttp['port'], sn, reality_xhttp['pbk'],
            reality_xhttp['sid'][0], reality_xhttp['host'], quote(reality_xhttp['path']),
        )
    )
if reality and front_public and front_path and front_sni:
    ports.append(front_public)
    links.append(
        'vless://%s@%s:%s?encryption=none&security=tls&sni=%s&fp=chrome&alpn=h2&type=xhttp&host=%s&path=%s&mode=auto#bwg-xhttp-tls'
        % (reality['uuid'], front_sni, front_public, front_sni, front_sni, quote(front_path))
    )
if hy2_base:
    ports.append(hy2_base['port'])
    links.append(
        'hysteria2://%s@%s:%s?sni=%s&alpn=h3#bwg-hy2-base'
        % (quote(hy2_base['password']), hy2_base['sni'], hy2_base['port'], hy2_base['sni'])
    )
if hy2_obfs:
    ports.append(hy2_obfs['port'])
    links.append(
        'hysteria2://%s@%s:%s?sni=%s&alpn=h3&obfs=salamander&obfs-password=%s#bwg-hy2-obfs'
        % (
            quote(hy2_obfs['password']), hy2_obfs['sni'], hy2_obfs['port'],
            hy2_obfs['sni'], quote(hy2_obfs['obfs'] or ''),
        )
    )
if reality and cdn_host and cdn_port and cdn_path:
    ports.append(cdn_port)
    links.append(
        'vless://%s@%s:%s?encryption=none&security=tls&sni=%s&fp=chrome&alpn=http%%2F1.1&type=ws&host=%s&path=%s#bwg-cdn-ws'
        % (reality['uuid'], cdn_host, cdn_port, cdn_host, cdn_host, quote(cdn_path))
    )

print(json.dumps({'links': links, 'ports': sorted(set(ports)), 'uuid': (reality or {}).get('uuid') or ''}))
PY
}

state="preview"
facts="$(build)" || { echo 'MERGED_SUB_BUILD_FAILED' >&2; write_status failed 4; exit 4; }
count="$(printf '%s' "$facts" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["links"]))')"
echo "merged_sub_nodes=$count"
dead_ports="$(printf '%s' "$facts" | python3 -c 'import json,sys; print(" ".join(str(x) for x in json.load(sys.stdin)["ports"]))')"
dead=0
for port in $dead_ports; do
    if ! ss -Hltn 2>/dev/null | awk '$4 !~ /^(127\.0\.0\.1|\[::1\]|::1)/ {print $4}' | grep -qE ":$port\$" &&
       ! ss -Hlun 2>/dev/null | awk '$4 !~ /^(127\.0\.0\.1|\[::1\]|::1)/ {print $4}' | grep -qE ":$port\$"; then
        echo "dead_port=$port" >&2
        dead=1
    fi
done
if [ "$dead" -eq 1 ]; then
    echo 'MERGED_SUB_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi
if [ "$count" -lt 6 ]; then
    echo 'MERGED_SUB_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi

published=0
if [ -f "$TARGET" ]; then
    published="$(base64 -d < "$TARGET" 2>/dev/null | grep -cE '^(vless|hysteria2)://' || true)"
fi
echo "merged_sub_published=${published:-0} token_len=${#TOKEN}"

match=0
if [ "$published" = "$count" ]; then
    want="$(printf '%s' "$facts" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["links"]))' | base64 -w0)"
    # $TARGET already holds the base64 text; comparing it re-encoded compared
    # base64(base64(list)) with base64(list) and never matched, so every pass
    # rewrote the file and reported drift.
    have="$(cat "$TARGET" 2>/dev/null || true)"
    [ "$want" = "$have" ] && match=1
fi
echo "merged_sub_current=$match"

if [ "$match" -eq 1 ]; then
    echo 'merged_sub=applied'
    write_status noop 0
    exit 0
fi
echo 'merged_sub=drift'
if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/merged-sub.XXXXXX")"
chmod 700 "$backup_dir"
if [ -f "$TARGET" ]; then cp -a "$TARGET" "$backup_dir/previous"; else : > "$backup_dir/absent"; fi

mkdir -p "$MERGED_DIR"
chmod 755 "$MERGED_DIR"
printf '%s' "$facts" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["links"]))' | base64 -w0 > "$TARGET"
chmod 644 "$TARGET"
mkdir -p "$STATUS_DIR" "$(dirname "$TOKEN_FILE")"
printf '%s' "$TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

http="$(curl -sk -m 8 --resolve "fq.sciman.top:33387:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "https://fq.sciman.top:33387/s/merged/$TOKEN" 2>/dev/null || true)"
served="$(base64 -d < "$TARGET" 2>/dev/null | grep -cE '^(vless|hysteria2)://' || true)"
echo "merged_sub_http=${http:-000} served=$served"
if [ "$http" != '200' ] || [ "$served" != "$count" ]; then
    echo 'MERGED_SUB_VERIFY_FAILED' >&2
    rm -f -- "$TARGET"
    [ -f "$backup_dir/previous" ] && cp -a "$backup_dir/previous" "$TARGET"
    write_status failed 1
    exit 1
fi
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'MERGED_SUB_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__TOKEN__", $Token)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "merged-sub"

$urlFile = Join-Path $configDir "merged-subscription-url.txt"
$urls = @(
  "https://fq.sciman.top:33387/s/merged/$Token"
  "https://cdn.sciman.top:2087/s/merged/$Token"
) -join "`n"
Set-Content -LiteralPath $urlFile -Value $urls -Encoding utf8NoBOM
Write-Output "merged_sub_url_file=$urlFile"
