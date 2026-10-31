#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
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

# vasma publishes the XHTTP TLS lane as a dokodemo-door wrapper in front of a
# loopback TLS inbound. That makes the public port a bare Xray listener: every
# path answers an empty `HTTP/2 404` with no `Server`, no `Content-Type` and no
# body, while the same host's nginx answers a normal 404. Xray's own XHTTP
# guidance is explicit that XHTTP should hide behind real nginx to avoid
# exactly this fingerprint.
#
# The lane keeps the subscription contract identical: nginx now terminates TLS
# on the same port with the same certificate and proxies the configured path to
# the loopback inbound, which drops to plain HTTP. Unknown paths return the
# decoy site, and unknown SNI is rejected like a strict vhost. Client configs
# (address/host/path/port/tls) do not change.
#
# vasma regenerates the wrapper on a menu reconfiguration; the drift gate
# reports `xhttp-front` and re-running this lane restores the front.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
LOCK_FILE="${VPS_XHTTP_FRONT_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
NGINX_CONF_D="${VPS_XHTTP_FRONT_CONF_DIR:-/etc/nginx/conf.d}"
TARGET_FILE="$NGINX_CONF_D/vps-launcher-xhttp-front.conf"
XRAY_CONF="${VPS_XHTTP_FRONT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
XRAY_BINARY="${VPS_XHTTP_FRONT_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
NGINX_ROOT="${VPS_XHTTP_FRONT_ROOT:-/usr/share/nginx/html}"
STATUS_DIR="${VPS_XHTTP_FRONT_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_XHTTP_FRONT_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/xhttp-front.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=xhttp-front\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

facts="$(python3 - "$XRAY_CONF" "$TARGET_FILE" <<'PY'
import glob
import json
import os
import re
import sys

conf_dir, target = sys.argv[1:3]
inbounds = []
invalid = False
for path in sorted(glob.glob(os.path.join(conf_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        inbounds.append((path, ib))


def loopback(value):
    return str(value or '') in ('127.0.0.1', '::1', 'localhost')


front_port = None
if os.path.isfile(target):
    try:
        text = open(target, encoding='utf-8').read()
    except OSError:
        text = ''
    match = re.search(r'listen\s+(\d+)\s+ssl;', text)
    if match:
        front_port = int(match.group(1))

entry = None
for path, ib in inbounds:
    stream = ib.get('streamSettings') or {}
    if stream.get('network') != 'xhttp':
        continue
    if not loopback(ib.get('listen')):
        continue
    clients = ((ib.get('settings') or {}).get('clients') or [])
    if not clients:
        continue
    tls = stream.get('tlsSettings') or {}
    xhttp = stream.get('xhttpSettings') or {}
    certs = tls.get('certificates') or [{}]
    entry = {
        'file': path,
        'inner': ib.get('port'),
        'security': stream.get('security') or 'none',
        'sni': tls.get('serverName') or xhttp.get('host') or '',
        'path': xhttp.get('path') or '/',
        'host': xhttp.get('host') or tls.get('serverName') or '',
        'cert': certs[0].get('certificateFile') or '',
        'key': certs[0].get('keyFile') or '',
        'wrapper': None,
    }
    break

if invalid:
    print(json.dumps({'state': 'invalid'}))
    raise SystemExit(0)
if not entry:
    print(json.dumps({'state': 'not_applicable'}))
    raise SystemExit(0)

for path, ib in inbounds:
    if ib.get('protocol') != 'dokodemo-door':
        continue
    settings = ib.get('settings') or {}
    if str(settings.get('address')) not in ('127.0.0.1', 'localhost'):
        continue
    if settings.get('port') != entry['inner'] or loopback(ib.get('listen')):
        continue
    entry['wrapper'] = ib.get('port')

public = entry['wrapper'] or front_port or 0
entry['public'] = public
if entry['security'] == 'tls' and entry['wrapper']:
    entry['state'] = 'absent'
elif entry['security'] == 'tls':
    entry['state'] = 'drift'
elif entry['wrapper']:
    entry['state'] = 'drift'
elif front_port and front_port == public:
    entry['state'] = 'applied'
else:
    entry['state'] = 'drift'
print(json.dumps(entry))
PY
)"

field() {
    python3 -c 'import json,sys; v=json.loads(sys.argv[1]).get(sys.argv[2]); print("" if v is None else v)' "$facts" "$1"
}

state="$(field state)"
inner="$(field inner)"
public="$(field public)"
sni="$(field sni)"
path_value="$(field path)"
host_value="$(field host)"
cert="$(field cert)"
key="$(field key)"
source_file="$(field file)"

echo "xhttp_front_state=${state}"
echo "xhttp_front_inner=${inner:-none} public=${public:-none} sni=${sni:-none} path=${path_value:-none}"

decoys_ok() {
    # $1 port, $2 sni -> 1 when an unauthenticated GET returns the decoy page.
    local code sha decoy
    decoy="$(sha256sum "$NGINX_ROOT/index.html" 2>/dev/null | awk '{print $1}')"
    [ -n "$decoy" ] || return 1
    code="$(curl -sk -m 8 -o "$TMP_BODY" -w '%{http_code}' \
        --resolve "$2:$1:127.0.0.1" "https://$2:$1/" 2>/dev/null || true)"
    sha="$(sha256sum "$TMP_BODY" 2>/dev/null | awk '{print $1}')"
    [ "$code" = '200' ] && [ "$sha" = "$decoy" ]
}

TMP_BODY="$(mktemp)"
cleanup() { rm -f -- "$TMP_BODY"; }
trap cleanup EXIT INT TERM

if [ "$state" = 'invalid' ]; then
    echo 'XHTTP_FRONT_SOURCE_INVALID' >&2
    write_status failed 4
    exit 4
fi
if [ "$state" = 'not_applicable' ]; then
    echo 'xhttp_front=not_applicable'
    write_status noop 0
    exit 0
fi

nginx_owns_public=0
if [ -n "$public" ] && ss -Hltnp 2>/dev/null | grep -q ":$public .*nginx"; then
    nginx_owns_public=1
fi
inner_listening=0
if [ -n "$inner" ] && ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$inner\$"; then
    inner_listening=1
fi
echo "nginx_owns_public=$nginx_owns_public inner_listening=$inner_listening"

if [ "$state" = 'applied' ] && [ "$nginx_owns_public" -eq 1 ] && [ "$inner_listening" -eq 1 ] &&
   decoys_ok "$public" "$sni"; then
    echo 'xhttp_front=applied'
    write_status noop 0
    exit 0
fi

if [ "$state" = 'applied' ]; then
    echo 'xhttp_front=drift'
else
    echo "xhttp_front=${state}"
fi
if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi
if [ -z "$public" ] || [ "$public" = '0' ] || [ -z "$inner" ] || [ -z "$sni" ] ||
   [ -z "$path_value" ] || [ -z "$cert" ] || [ -z "$key" ] || [ -z "$source_file" ]; then
    echo 'XHTTP_FRONT_FACTS_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi
if [ ! -f "$cert" ] || [ ! -f "$key" ]; then
    echo 'XHTTP_FRONT_TLS_MATERIAL_MISSING' >&2
    write_status failed 4
    exit 4
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/xhttp-front.XXXXXX")"
chmod 700 "$backup_dir"
mkdir -p "$backup_dir/xray"
cp -a "$source_file" "$backup_dir/xray/inbound.json"
if [ -f "$TARGET_FILE" ]; then cp -a "$TARGET_FILE" "$backup_dir/previous.conf"; else : > "$backup_dir/previously-absent"; fi

rollback() {
    trap - ERR INT TERM
    set +e
    rollback_failed=0
    cp -a "$backup_dir/xray/inbound.json" "$source_file" || rollback_failed=1
    if [ -f "$backup_dir/previous.conf" ]; then
        cp -a "$backup_dir/previous.conf" "$TARGET_FILE" || rollback_failed=1
    else
        rm -f -- "$TARGET_FILE" || rollback_failed=1
    fi
    "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1 || rollback_failed=1
    nginx -t >/dev/null 2>&1 || rollback_failed=1
    systemctl restart xray >/dev/null 2>&1 || rollback_failed=1
    systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
    if ! systemctl is-active --quiet xray || ! systemctl is-active --quiet nginx; then
        rollback_failed=1
    fi
    if [ "$rollback_failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    write_status failed 1
    exit 1
}
trap rollback ERR
trap 'exit 130' INT
trap 'exit 143' TERM

# 1. Drop the dokodemo wrapper and make the loopback inbound plain HTTP.
python3 - "$source_file" "$inner" <<'PY'
import json
import sys

path, inner = sys.argv[1], int(sys.argv[2])
data = json.load(open(path, encoding='utf-8'))
kept = []
for ib in data.get('inbounds', []) or []:
    settings = ib.get('settings') or {}
    if (
        ib.get('protocol') == 'dokodemo-door'
        and str(settings.get('address')) in ('127.0.0.1', 'localhost')
        and settings.get('port') == inner
    ):
        continue
    kept.append(ib)
for ib in kept:
    stream = ib.get('streamSettings') or {}
    if stream.get('network') == 'xhttp' and ib.get('port') == inner:
        stream['security'] = 'none'
        stream.pop('tlsSettings', None)
data['inbounds'] = kept
with open(path, 'w', encoding='utf-8') as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write('\n')
PY

# 2. Put nginx on the public port with the same certificate.
python3 - "$TARGET_FILE" "$public" "$sni" "$path_value" "$inner" "$cert" "$key" "$NGINX_ROOT" <<'PY'
import sys

out, port, sni, location, inner, cert, key, root = sys.argv[1:9]
lines = [
    '# Managed by vps-ssh-launcher scripts/xhttp_nginx_front.ps1.',
    '# Do not edit by hand; re-run the lane instead.',
    'server {',
    f'    listen {port} ssl default_server;',
    '    ssl_reject_handshake on;',
    '}',
    '',
    'server {',
    f'    listen {port} ssl;',
    '    http2 on;',
    f'    server_name {sni};',
    f'    ssl_certificate {cert};',
    f'    ssl_certificate_key {key};',
    '    ssl_protocols TLSv1.2 TLSv1.3;',
    f'    root {root};',
    '    client_max_body_size 0;',
    '    access_log off;',
    '',
    f'    location ^~ {location} {{',
    f'        proxy_pass http://127.0.0.1:{inner};',
    '        proxy_http_version 1.1;',
    '        proxy_set_header Host $host;',
    '        proxy_set_header X-Real-IP $remote_addr;',
    '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
    '        proxy_set_header Connection "";',
    '        proxy_buffering off;',
    '        proxy_request_buffering off;',
    '        proxy_cache off;',
    '        proxy_read_timeout 3600s;',
    '        proxy_send_timeout 3600s;',
    '        send_timeout 3600s;',
    '    }',
    '',
    '    location / {',
    '        try_files $uri $uri/ =404;',
    '    }',
    '}',
]
with open(out, 'w', encoding='utf-8') as handle:
    handle.write('\n'.join(lines) + '\n')
PY

if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
    echo 'XRAY_CONFIG_TEST_FAILED' >&2
    rollback
fi
if ! nginx -t; then
    echo 'NGINX_TEST_FAILED' >&2
    rollback
fi
if ! systemctl restart xray; then
    echo 'XRAY_RESTART_FAILED' >&2
    rollback
fi
if ! systemctl reload nginx; then
    echo 'NGINX_RELOAD_FAILED' >&2
    rollback
fi
sleep 1
if ! systemctl is-active --quiet xray; then
    echo 'XRAY_INACTIVE' >&2
    rollback
fi

# A restart/reload returns before the new process is answering, so retry the
# whole lane instead of failing on a single early probe.
lane_ready() {
    ss -Hltnp 2>/dev/null | grep -q ":$public .*nginx" &&
        ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$inner\$" &&
        decoys_ok "$public" "$sni"
}
lane_ok=0
for attempt in 1 2 3 4 5 6; do
    if lane_ready; then
        lane_ok=1
        break
    fi
    sleep 1
done
if [ "$lane_ok" -ne 1 ]; then
    ss -Hltnp 2>/dev/null | grep -q ":$public .*nginx" || echo 'NGINX_NOT_OWNING_PUBLIC_PORT' >&2
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$inner\$" || echo 'INNER_INBOUND_NOT_LISTENING' >&2
    decoys_ok "$public" "$sni" || echo 'DECOY_PAGE_NOT_SERVED' >&2
    rollback
fi
trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'XHTTP_FRONT_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "xhttp-front"
