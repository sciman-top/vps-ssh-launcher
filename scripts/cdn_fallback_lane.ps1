#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg")]
  [string]$Profile,
  [string]$PublicHost = "cdn.sciman.top",
  [int]$OriginPort = 2087,
  [int]$LocalPort = 45989,
  [string]$WsPath = "/l7kcDNws",
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
foreach ($port in @($OriginPort, $LocalPort)) {
  if ($port -lt 1024 -or $port -gt 65535) {
    throw "-OriginPort/-LocalPort must be unprivileged ports (1024..65535)."
  }
}
if ($OriginPort -eq $LocalPort) {
  throw "-OriginPort and -LocalPort must differ."
}
if ($PublicHost -notmatch '^[A-Za-z0-9.-]+$') {
  throw "-PublicHost must be a DNS name."
}
if ($WsPath -notmatch '^/[A-Za-z0-9._~/-]+$') {
  throw "-WsPath must be an absolute path without shell syntax."
}

$applyLiteral = if ($Apply) { "1" } else { "0" }

# Cloudflare-fronted fallback lane. Every other lane on this host is reached by
# its own address, so a GFW block of the bwg IP or of its four ports leaves
# nothing to switch to once the second VPS retires. This is the only lane whose
# client-visible address is an anycast edge: the client talks to Cloudflare on
# 2087, Cloudflare talks to the origin on 2087 over TLS, and nginx on the
# origin fronts a loopback VLESS+WS inbound. The censor never sees the origin.
#
# WebSocket is deliberate: it is the transport Cloudflare proxies without
# buffering surprises, and this lane is a last resort rather than a throughput
# lane. The client port stays 2087 because Cloudflare connects to the origin on
# the port the client used; rewriting it would need Origin Rules and a
# zone-wide API token the on-host acme credentials do not carry.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
PUBLIC_HOST="__PUBLIC_HOST__"
ORIGIN_PORT="__ORIGIN_PORT__"
LOCAL_PORT="__LOCAL_PORT__"
WS_PATH="__WS_PATH__"
LOCK_FILE="${VPS_CDN_LANE_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
NGINX_CONF_D="${VPS_CDN_LANE_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
TARGET_FILE="$NGINX_CONF_D/vps-launcher-cdn.conf"
XRAY_CONF="${VPS_CDN_LANE_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
FRAGMENT="$XRAY_CONF/15_vps_ssh_launcher_cdn_ws.json"
XRAY_BINARY="${VPS_CDN_LANE_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
NGINX_ROOT="${VPS_CDN_LANE_ROOT:-/usr/share/nginx/html}"
TLS_DIR="${VPS_CDN_LANE_TLS_DIR:-/etc/v2ray-agent/tls}"
ACME_HOME="${VPS_CDN_LANE_ACME_HOME:-/root/.acme.sh}"
ZONE_NAME="${VPS_CDN_LANE_ZONE:-sciman.top}"
STATUS_DIR="${VPS_CDN_LANE_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_CDN_LANE_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/cdn-lane.status"
TAG="vps-launcher-cdn-ws"

# vasma stores the wildcard certificate under the first hostname it was issued
# for (fq.sciman.top), so an exact-name lookup would miss it. Prefer the exact
# file when present and otherwise pick the certificate that actually covers the
# public host.
resolve_tls() {
    if [ -f "$TLS_DIR/$PUBLIC_HOST.crt" ] && [ -f "$TLS_DIR/$PUBLIC_HOST.key" ]; then
        printf '%s\n%s\n' "$TLS_DIR/$PUBLIC_HOST.crt" "$TLS_DIR/$PUBLIC_HOST.key"
        return 0
    fi
    local crt key
    for crt in "$TLS_DIR"/*.crt; do
        [ -f "$crt" ] || continue
        if openssl x509 -in "$crt" -noout -checkhost "$PUBLIC_HOST" 2>/dev/null | grep -q 'does match'; then
            key="${crt%.crt}.key"
            if [ -f "$key" ]; then
                printf '%s\n%s\n' "$crt" "$key"
                return 0
            fi
        fi
    done
    return 1
}

CERT_FILE=""
KEY_FILE=""
if tls_material="$(resolve_tls)"; then
    CERT_FILE="$(printf '%s\n' "$tls_material" | sed -n 1p)"
    KEY_FILE="$(printf '%s\n' "$tls_material" | sed -n 2p)"
fi
echo "cdn_tls_cert=${CERT_FILE:-none}"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=cdn-lane\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

facts="$(python3 - "$XRAY_CONF" "$TARGET_FILE" "$TAG" "$LOCAL_PORT" "$WS_PATH" "$ORIGIN_PORT" <<'PY'
import glob
import json
import os
import re
import sys

conf_dir, target, tag = sys.argv[1:4]
local_port, ws_path, origin_port = (int(sys.argv[4]), sys.argv[5], int(sys.argv[6]))

inbound = None
invalid = False
for path in sorted(glob.glob(os.path.join(conf_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for entry in data.get('inbounds', []) or []:
        if entry.get('tag') == tag:
            stream = entry.get('streamSettings') or {}
            ws = stream.get('wsSettings') or {}
            inbound = {
                'file': path,
                'port': entry.get('port'),
                'listen': entry.get('listen'),
                'security': stream.get('security') or 'none',
                'path': ws.get('path') or '',
                'clients': len((entry.get('settings') or {}).get('clients') or []),
            }

conf_text = ''
if os.path.isfile(target):
    try:
        conf_text = open(target, encoding='utf-8').read()
    except OSError:
        conf_text = ''
conf_listen = None
match = re.search(r'listen\s+(\d+)\s+ssl;', conf_text)
if match:
    conf_listen = int(match.group(1))
conf_upstream = None
match = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+);', conf_text)
if match:
    conf_upstream = int(match.group(1))
conf_upgrade = 'proxy_set_header Upgrade $http_upgrade;' in conf_text

if invalid:
    state = 'invalid'
elif inbound is None and conf_listen is None:
    state = 'absent'
elif (
    inbound is not None
    and inbound['port'] == local_port
    and inbound['security'] == 'none'
    and inbound['path'] == ws_path
    and inbound['clients'] >= 1
    and conf_listen == origin_port
    and conf_upstream == local_port
    and conf_upgrade
):
    state = 'applied'
else:
    state = 'drift'

print(json.dumps({
    'state': state,
    'inbound_port': (inbound or {}).get('port'),
    'inbound_path': (inbound or {}).get('path'),
    'conf_listen': conf_listen,
    'conf_upstream': conf_upstream,
}))
PY
)"

field() {
    python3 -c 'import json,sys
v=json.loads(sys.argv[1])
for key in sys.argv[2].split("."):
    v=(v or {}).get(key) if isinstance(v,dict) else None
print("" if v is None else v)' "$facts" "$1"
}

state="$(field state)"
echo "cdn_lane_state=${state}"
echo "cdn_inbound=$(field inbound_port) path=$(field inbound_path) conf_listen=$(field conf_listen) conf_upstream=$(field conf_upstream)"
echo "cdn_public_host=$PUBLIC_HOST origin_port=$ORIGIN_PORT ws_path=$WS_PATH"

listen_up=0
if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$LOCAL_PORT"; then
    listen_up=1
fi
origin_up=0
if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$ORIGIN_PORT\$"; then
    origin_up=1
fi
echo "cdn_local_listening=$listen_up origin_listening=$origin_up"

TMP_BODY="$(mktemp)"
cleanup() { rm -f -- "$TMP_BODY"; }
trap cleanup EXIT INT TERM

decoys_ok() {
    local code sha decoy
    decoy="$(sha256sum "$NGINX_ROOT/index.html" 2>/dev/null | awk '{print $1}')"
    [ -n "$decoy" ] || return 1
    code="$(curl -sk -m 8 -o "$TMP_BODY" -w '%{http_code}' \
        --resolve "$PUBLIC_HOST:$ORIGIN_PORT:127.0.0.1" "https://$PUBLIC_HOST:$ORIGIN_PORT/" 2>/dev/null || true)"
    sha="$(sha256sum "$TMP_BODY" 2>/dev/null | awk '{print $1}')"
    [ "$code" = '200' ] && [ "$sha" = "$decoy" ]
}

cf_token() {
    local conf
    conf="$(ls "$ACME_HOME"/*."$ZONE_NAME"_ecc/*.conf 2>/dev/null | head -1)"
    grep -h -m1 -E '^SAVED_CF_Token=' "$ACME_HOME/account.conf" "$conf" 2>/dev/null |
        sed -E "s/^[^=]+='?([^']*)'?$/\1/"
}

origin_ip() {
    local ip
    ip="$(curl -4 -s --max-time 6 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null |
        sed -n 's/^ip=//p' | head -1)"
    [ -n "$ip" ] || ip="$(curl -4 -s --max-time 6 https://1.1.1.1/cdn-cgi/trace 2>/dev/null |
        sed -n 's/^ip=//p' | head -1)"
    printf '%s' "$ip"
}

TOKEN="$(cf_token)"
if [ -z "$TOKEN" ]; then
    echo 'CDN_LANE_CF_TOKEN_MISSING' >&2
    write_status failed 4
    exit 4
fi
export CF_TOKEN="$TOKEN"
export CF_ZONE="$ZONE_NAME"
export CF_HOST="$PUBLIC_HOST"

dns_state() {
    python3 <<'PY'
import json
import os
import urllib.error
import urllib.parse
import urllib.request

token = os.environ['CF_TOKEN']
zone = os.environ['CF_ZONE']
host = os.environ['CF_HOST']


def call(path):
    request = urllib.request.Request(
        'https://api.cloudflare.com/client/v4' + path,
        headers={'Authorization': 'Bearer ' + token},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return json.load(error)
        except Exception:
            return {'success': False}
    except Exception:
        return {'success': False}


zones = call('/zones?name=' + urllib.parse.quote(zone))
if not zones.get('success') or not zones.get('result'):
    print('dns_state=api_error')
    raise SystemExit(0)
zone_id = zones['result'][0]['id']
records = call('/zones/%s/dns_records?type=A&name=%s' % (zone_id, urllib.parse.quote(host)))
if not records.get('success'):
    print('dns_state=api_error')
    raise SystemExit(0)
items = records.get('result') or []
if not items:
    print('dns_state=missing')
    raise SystemExit(0)
record = items[0]
print('dns_state=%s' % ('proxied' if record.get('proxied') else 'dns_only'))
print('dns_record_id=%s' % record['id'])
PY
}

dns_upsert() {
    ORIGIN_IP="$1" DNS_RECORD_ID="$2" python3 <<'PY'
import json
import os
import urllib.parse
import urllib.request

token = os.environ['CF_TOKEN']
zone = os.environ['CF_ZONE']
host = os.environ['CF_HOST']
origin = os.environ['ORIGIN_IP']
record_id = os.environ.get('DNS_RECORD_ID', '')


def call(path, method='GET', payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        'https://api.cloudflare.com/client/v4' + path,
        data=data,
        method=method,
        headers={
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


zones = call('/zones?name=' + urllib.parse.quote(zone))
zone_id = zones['result'][0]['id']
body = {'type': 'A', 'name': host, 'content': origin, 'proxied': True, 'ttl': 1}
if record_id:
    result = call('/zones/%s/dns_records/%s' % (zone_id, record_id), 'PUT', body)
    action = 'updated'
else:
    result = call('/zones/%s/dns_records' % zone_id, 'POST', body)
    action = 'created'
if not result.get('success'):
    print('DNS_UPSERT_FAILED action=%s errors=%s' % (action, result.get('errors')))
    raise SystemExit(1)
print('DNS_UPSERT_OK action=%s proxied=%s' % (action, result['result'].get('proxied')))
PY
}

dns_out="$(dns_state)"
printf '%s\n' "$dns_out"
dns_status="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_state=//p')"
dns_record_id="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_record_id=//p')"
if [ "$dns_status" = 'api_error' ]; then
    echo 'CDN_LANE_CF_API_ERROR' >&2
    write_status failed 4
    exit 4
fi

edge_code="$(curl -s -m 15 -o /dev/null -w '%{http_code}' "https://$PUBLIC_HOST:$ORIGIN_PORT/" 2>/dev/null || true)"
echo "cdn_edge_http=$edge_code"

if [ "$state" = 'invalid' ]; then
    echo 'CDN_LANE_SOURCE_INVALID' >&2
    write_status failed 4
    exit 4
fi
if [ "$state" = 'applied' ] && [ "$listen_up" -eq 1 ] && [ "$origin_up" -eq 1 ] &&
   [ "$dns_status" = 'proxied' ] && [ "$edge_code" = '200' ] && decoys_ok; then
    echo 'cdn_lane=applied'
    write_status noop 0
    exit 0
fi
if [ "$state" = 'applied' ]; then
    echo 'cdn_lane=drift'
else
    echo "cdn_lane=$state"
fi
if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo 'CDN_LANE_TLS_MATERIAL_MISSING' >&2
    write_status failed 4
    exit 4
fi

OIP="$(origin_ip)"
if [ -z "$OIP" ]; then
    echo 'CDN_LANE_ORIGIN_IP_UNKNOWN' >&2
    write_status failed 4
    exit 4
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/cdn-lane.XXXXXX")"
chmod 700 "$backup_dir"
mkdir -p "$backup_dir/xray"
if [ -f "$FRAGMENT" ]; then cp -a "$FRAGMENT" "$backup_dir/xray/fragment.json"; else : > "$backup_dir/fragment-absent"; fi
if [ -f "$TARGET_FILE" ]; then cp -a "$TARGET_FILE" "$backup_dir/previous.conf"; else : > "$backup_dir/conf-absent"; fi

rollback() {
    trap - ERR INT TERM
    set +e
    local rollback_failed=0
    if [ -f "$backup_dir/fragment-absent" ]; then
        rm -f -- "$FRAGMENT" || rollback_failed=1
    else
        cp -a "$backup_dir/xray/fragment.json" "$FRAGMENT" || rollback_failed=1
    fi
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

dns_upsert "$OIP" "$dns_record_id" || { echo 'CDN_LANE_DNS_FAILED' >&2; rollback; }

# 1. Loopback VLESS+WS inbound. It reuses the existing VLESS credential so the
#    operator keeps one UUID across lanes; no new secret is invented here.
VPS_CDN_UUID="$(python3 - "$XRAY_CONF" <<'PY'
import glob
import json
import os
import sys

for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for entry in data.get('inbounds', []) or []:
        if entry.get('protocol') != 'vless':
            continue
        for client in (entry.get('settings') or {}).get('clients') or []:
            if client.get('id'):
                print(client['id'])
                raise SystemExit(0)
raise SystemExit(1)
PY
)" || { echo 'CDN_LANE_UUID_MISSING' >&2; rollback; }

VPS_CDN_UUID="$VPS_CDN_UUID" VPS_CDN_PATH="$WS_PATH" VPS_CDN_PORT="$LOCAL_PORT" \
    VPS_CDN_TAG="$TAG" python3 - "$FRAGMENT" <<'PY'
import json
import os
import sys

document = {
    'inbounds': [
        {
            'tag': os.environ['VPS_CDN_TAG'],
            'listen': '127.0.0.1',
            'port': int(os.environ['VPS_CDN_PORT']),
            'protocol': 'vless',
            'settings': {
                'clients': [
                    {'id': os.environ['VPS_CDN_UUID'], 'email': 'cdn-ws'}
                ],
                'decryption': 'none',
            },
            'streamSettings': {
                'network': 'ws',
                'security': 'none',
                'wsSettings': {'path': os.environ['VPS_CDN_PATH']},
            },
        }
    ]
}
with open(sys.argv[1], 'w', encoding='utf-8') as handle:
    json.dump(document, handle, ensure_ascii=False, indent=2)
    handle.write('\n')
PY

# 2. nginx terminates TLS on the Cloudflare origin port and fronts the decoy
#    site, mirroring the XHTTP lane's shape. No client-IP headers: the inner
#    inbound has no trustedXForwardedFor and would only log warnings.
cat > "$TARGET_FILE" <<EOF
# Managed by vps-ssh-launcher scripts/cdn_fallback_lane.ps1.
# Do not edit by hand; re-run the lane instead.
server {
    listen $ORIGIN_PORT ssl default_server;
    ssl_reject_handshake on;
}

server {
    listen $ORIGIN_PORT ssl;
    http2 on;
    server_name $PUBLIC_HOST;
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    ssl_protocols TLSv1.2 TLSv1.3;
    root $NGINX_ROOT;
    client_max_body_size 0;
    access_log off;

    location ^~ $WS_PATH {
        proxy_pass http://127.0.0.1:$LOCAL_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        send_timeout 3600s;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF

if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
    echo 'XRAY_CONFIG_TEST_FAILED' >&2
    rollback
fi
if ! nginx -t >/dev/null 2>&1; then
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

lane_ok=0
for attempt in 1 2 3 4 5 6; do
    if systemctl is-active --quiet xray &&
       ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$LOCAL_PORT" &&
       ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$ORIGIN_PORT\$" &&
       decoys_ok; then
        lane_ok=1
        break
    fi
    sleep 1
done
if [ "$lane_ok" -ne 1 ]; then
    echo 'CDN_LANE_NOT_READY' >&2
    rollback
fi
trap - ERR INT TERM

# Cloudflare needs a moment to publish the proxied record; a miss here is
# reported rather than treated as fatal because DNS propagation is external.
edge_after="$(curl -s -m 20 -o /dev/null -w '%{http_code}' "https://$PUBLIC_HOST:$ORIGIN_PORT/" 2>/dev/null || true)"
echo "cdn_edge_http=$edge_after"
if [ "$edge_after" != '200' ]; then
    echo 'CDN_EDGE_NOT_SERVING_YET retry_in_60s ds=cloudflare_ssl_mode_should_be_full_or_strict'
fi
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'CDN_LANE_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__PUBLIC_HOST__", $PublicHost).Replace("__ORIGIN_PORT__", "$OriginPort").Replace("__LOCAL_PORT__", "$LocalPort").Replace("__WS_PATH__", $WsPath)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "cdn-lane"
