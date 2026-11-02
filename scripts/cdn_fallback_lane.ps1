#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [string]$PublicHost = $null,
  [int]$OriginPort = 0,
  [int]$LocalPort = 0,
  [string]$WsPath = "",
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

# Per-host defaults; the two lanes must not share a hostname or a WS path.
$defaults = @{
  bwg = @{ Host = "cdn.sciman.top"; OriginPort = 2087; LocalPort = 45989; WsPath = "/l7kcDNws" }
  zz  = @{ Host = "zzcdn.sciman.top"; OriginPort = 2087; LocalPort = 45989; WsPath = "/zzl7kcDNws" }
}
$d = $defaults[$Profile]
if (-not $PublicHost) { $PublicHost = $d.Host }
if (-not $OriginPort) { $OriginPort = $d.OriginPort }
if (-not $LocalPort) { $LocalPort = $d.LocalPort }
if (-not $WsPath) { $WsPath = $d.WsPath }

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
# for (fq.sciman.top), so an exact-name lookup would miss it. Every candidate is
# still checked against the public hostname before it is accepted. A certificate
# that merely exists is not enough: Cloudflare Full (strict) and direct probes
# both need the origin leaf to cover the name this lane publishes.
resolve_tls() {
    local crt key base
    cert_covers_host() {
        local candidate="$1"
        [ -f "$candidate" ] || return 1
        openssl x509 -in "$candidate" -noout -checkhost "$PUBLIC_HOST" 2>/dev/null |
            grep -q 'does match'
    }
    crt="$TLS_DIR/$PUBLIC_HOST.crt"
    key="$TLS_DIR/$PUBLIC_HOST.key"
    if cert_covers_host "$crt" && [ -f "$key" ]; then
        printf '%s\n%s\n' "$crt" "$key"
        return 0
    fi
    for crt in "$TLS_DIR"/*.crt; do
        [ -f "$crt" ] || continue
        key="${crt%.crt}.key"
        if cert_covers_host "$crt" && [ -f "$key" ]; then
            printf '%s\n%s\n' "$crt" "$key"
            return 0
        fi
    done
    # A vasma base-name pair may cover the lane through a wildcard. Keep the
    # compatibility lookup, but retain the same hostname check.
    base="${PUBLIC_HOST#cdn.}"
    crt="$TLS_DIR/$base.crt"
    key="$TLS_DIR/$base.key"
    if cert_covers_host "$crt" && [ -f "$key" ]; then
        printf '%s\n%s\n' "$TLS_DIR/$base.crt" "$TLS_DIR/$base.key"
        return 0
    fi
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

cdn_conf_current() {
    # Searching a missing file is not a healthy state: the previous command
    # chain could treat the second and third greps as the verdict after the
    # first test short-circuited.
    [ -f "$TARGET_FILE" ] &&
        grep -Fq -- '# Managed by vps-ssh-launcher scripts/cdn_fallback_lane.ps1.' "$TARGET_FILE" &&
        grep -Fq -- 'add_header Strict-Transport-Security "max-age=31536000" always;' "$TARGET_FILE" &&
        grep -Fq -- 'error_page 404 =404 /404.html;' "$TARGET_FILE"
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
    # pipefail turns a non-matching glob into a failing pipeline, and the
    # caller runs under `set -e`: an absent acme directory must read as "no
    # token", not abort the lane.
    conf="$( (ls "$ACME_HOME"/*."$ZONE_NAME"_ecc/*.conf 2>/dev/null || true) | head -1)"
    # No match is the normal "this host has no Cloudflare token" case, so the
    # pipeline must not fail under pipefail + set -e.
    { grep -h -m1 -E '^SAVED_CF_Token=' "$ACME_HOME/account.conf" "$conf" 2>/dev/null || true; } |
        sed -E "s/^[^=]+='?([^']*)'?$/\1/"
}

origin_ip() {
    local ip
    # `|| true` on each attempt: under set -e a failing endpoint must fall
    # through to the next one instead of killing the caller mid-apply.
    ip="$(curl -4 -s --max-time 6 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null |
        sed -n 's/^ip=//p' | head -1 || true)"
    [ -n "$ip" ] || ip="$(curl -4 -s --max-time 6 https://1.1.1.1/cdn-cgi/trace 2>/dev/null |
        sed -n 's/^ip=//p' | head -1 || true)"
    printf '%s' "$ip"
}

TOKEN="$(cf_token)"
if [ -z "$TOKEN" ]; then
    # The DNS record may already exist (created out of band, or by a host
    # without the acme Cloudflare token). Absence of the token only blocks the
    # upsert; the lane still verifies and serves with the existing record.
    echo 'cdn_dns_token=absent'
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
    if not token:
        return {'success': False, 'skipped': True}
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
if zones.get('skipped'):
    # No token on this host: the record must already exist, so read it over
    # plain DNS instead of the API. sys.exit(0) would make the $(...) capture
    # look like a non-zero command to `set -e`, so fall through instead.
    import socket
    try:
        answer = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        print('dns_state=proxied' if answer else 'dns_state=missing')
    except socket.gaierror:
        print('dns_state=missing')
elif not zones.get('success') or not zones.get('result'):
    print('dns_state=api_error')
else:
    zone_id = zones['result'][0]['id']
    records = call('/zones/%s/dns_records?type=A&name=%s' % (zone_id, urllib.parse.quote(host)))
    if not records.get('success'):
        print('dns_state=api_error')
    else:
        items = records.get('result') or []
        if not items:
            print('dns_state=missing')
        else:
            record = items[0]
            print('dns_state=%s' % ('proxied' if record.get('proxied') else 'dns_only'))
            print('dns_record_id=%s' % record['id'])
            # Captured so a failed apply can restore the pre-lane record
            # (content/proxied/ttl) instead of leaving the lane's upsert in
            # place while the local config rolled back.
            print('dns_record_content=%s' % record.get('content', ''))
            print('dns_record_proxied=%s' % ('true' if record.get('proxied') else 'false'))
            print('dns_record_ttl=%s' % record.get('ttl', 1))
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
if action == 'created':
    print('dns_new_record_id=%s' % result['result']['id'])
PY
}

dns_out="$(dns_state)"
printf '%s\n' "$dns_out"
dns_status="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_state=//p')"
dns_record_id="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_record_id=//p')"
dns_record_content="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_record_content=//p')"
dns_record_proxied="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_record_proxied=//p')"
dns_record_ttl="$(printf '%s\n' "$dns_out" | sed -n 's/^dns_record_ttl=//p')"
token_present='no'
[ -n "$TOKEN" ] && token_present='yes'
echo "cdn_dns_status=${dns_status:-none} token_present=$token_present"
echo "cdn_trace_target=$TARGET_FILE"
if [ "$dns_status" = 'api_error' ] && [ -n "$TOKEN" ]; then
    echo 'CDN_LANE_CF_API_ERROR' >&2
    write_status failed 4
    exit 4
fi
# Without the token the record must already be proxied; anything else cannot
# be repaired from here and must not be reported as a healthy lane.
if [ -z "$TOKEN" ]; then
    if [ "$dns_status" != 'proxied' ]; then
        echo 'CDN_LANE_DNS_NOT_PROXIED' >&2
        write_status failed 4
        exit 4
    fi
    # Best-effort proxy proof: the CDN record must not resolve straight to
    # this host's own public address; when it does, the record is no longer
    # proxied and only the token path could repair it. The origin identity is
    # the host's own public IP (the payload runs on the origin), which avoids
    # guessing an apex hostname — several zones carry no apex A record, and a
    # failed getent would abort under pipefail + set -e.
    origin_hint="$(curl -s -m 8 https://api.ipify.org 2>/dev/null || true)"
    cdn_hint="$(getent ahostsv4 "$PUBLIC_HOST" 2>/dev/null | awk 'NR==1{print $1}' || true)"
    if [ -n "$origin_hint" ] && [ -n "$cdn_hint" ] && [ "$cdn_hint" = "$origin_hint" ]; then
        echo 'CDN_LANE_DNS_NOT_PROXIED' >&2
        write_status failed 4
        exit 4
    fi
fi

edge_code="$(curl -s -m 15 -o /dev/null -w '%{http_code}' "https://$PUBLIC_HOST:$ORIGIN_PORT/" 2>/dev/null || true)"
echo "cdn_edge_http=$edge_code"

if [ "$state" = 'invalid' ]; then
    echo 'CDN_LANE_SOURCE_INVALID' >&2
    write_status failed 4
    exit 4
fi
if [ "$state" = 'applied' ] && [ "$listen_up" -eq 1 ] && [ "$origin_up" -eq 1 ] &&
   [ "$dns_status" = 'proxied' ] && [ "$edge_code" = '200' ] && decoys_ok &&
   cdn_conf_current; then
    echo 'cdn_lane=applied'
    write_status noop 0
    exit 0
fi
if [ "$state" = 'applied' ]; then
    if ! cdn_conf_current; then
        echo 'cdn_lane_template=stale'
    fi
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
# DNS restore ledger: with the token present the rollback can put the
# pre-lane record back (or delete one this lane creates); without it the
# upsert never runs and no ledger is written.
if [ -n "$TOKEN" ]; then
    if [ -n "$dns_record_id" ]; then
        printf 'action=restore\nrecord_id=%s\ncontent=%s\nproxied=%s\nttl=%s\n' \
            "$dns_record_id" "$dns_record_content" "$dns_record_proxied" "$dns_record_ttl" \
            > "$backup_dir/dns-restore"
    else
        printf 'action=delete\n' > "$backup_dir/dns-restore"
    fi
fi

rollback() {
    trap - ERR INT TERM
    set +e
    local rollback_failed=0
    # A failure between mktemp and mv leaves the rendered sibling
    # (.cdn-lane.XXXXXX) behind in conf.d; clean it up with the rollback.
    if [ -n "${temporary_conf:-}" ] && [ -e "$temporary_conf" ]; then
        rm -f -- "$temporary_conf" || rollback_failed=1
    fi
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
    # A DNS/API failure lands here before any local write; restarting xray and
    # reloading nginx would bounce every lane on the host for nothing. The
    # restore branches above are idempotent no-ops in that state.
    if [ "$local_wrote" -eq 1 ]; then
        systemctl restart xray >/dev/null 2>&1 || rollback_failed=1
        systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
    fi
    if ! systemctl is-active --quiet xray || ! systemctl is-active --quiet nginx; then
        rollback_failed=1
    fi
    # Best-effort external-state restore: the DNS upsert runs before the
    # local writes, so a later failure must not leave a proxied record
    # pointing at this origin while the lane itself rolled back.
    if [ -f "$backup_dir/dns-restore" ]; then
        if [ -z "$TOKEN" ]; then
            echo "CDN_DNS_NOT_ROLLED_BACK reason=no-token" >&2
            rollback_failed=1
        else
            DNS_RESTORE_FILE="$backup_dir/dns-restore" DNS_HOST="$PUBLIC_HOST" \
                python3 <<'PY' || { echo "CDN_DNS_NOT_ROLLED_BACK reason=api" >&2; rollback_failed=1; }
import json
import os
import urllib.parse
import urllib.request

token = os.environ['CF_TOKEN']
zone = os.environ['CF_ZONE']
host = os.environ['DNS_HOST']
state = {}
for line in open(os.environ['DNS_RESTORE_FILE'], encoding='utf-8'):
    key, _, value = line.strip().partition('=')
    state[key] = value


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
if not zones.get('success') or not zones.get('result'):
    print('CDN_DNS_ROLLBACK_FAILED reason=zone-lookup')
    raise SystemExit(1)
zone_id = zones['result'][0]['id']
if state.get('action') == 'delete':
    record_id = state.get('record_id', '')
    if not record_id:
        # The upsert never created a record; nothing to undo.
        print('CDN_DNS_ROLLED_BACK action=none')
        raise SystemExit(0)
    result = call('/zones/%s/dns_records/%s' % (zone_id, record_id), 'DELETE')
    if not result.get('success'):
        print('CDN_DNS_ROLLBACK_FAILED reason=delete')
        raise SystemExit(1)
    print('CDN_DNS_ROLLED_BACK action=deleted')
else:
    body = {
        'type': 'A',
        'name': host,
        'content': state.get('content', ''),
        'proxied': state.get('proxied') == 'true',
        'ttl': int(state.get('ttl') or 1),
    }
    result = call(
        '/zones/%s/dns_records/%s' % (zone_id, state.get('record_id', '')),
        'PUT',
        body,
    )
    if not result.get('success'):
        print('CDN_DNS_ROLLBACK_FAILED reason=restore')
        raise SystemExit(1)
    print('CDN_DNS_ROLLED_BACK action=restored')
PY
        fi
    fi
    if [ "$rollback_failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    write_status failed 1
    exit 1
}
local_wrote=0
trap rollback ERR
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -n "$TOKEN" ]; then
    upsert_out="$(dns_upsert "$OIP" "$dns_record_id")" || {
        printf '%s\n' "${upsert_out:-}" || true
        echo 'CDN_LANE_DNS_FAILED' >&2
        rollback
    }
    printf '%s\n' "$upsert_out"
    dns_new_record_id="$(printf '%s\n' "$upsert_out" | sed -n 's/^dns_new_record_id=//p')"
    if [ -z "$dns_record_id" ] && [ -n "$dns_new_record_id" ]; then
        # The lane created this record; the rollback ledger deletes it.
        printf 'record_id=%s\n' "$dns_new_record_id" >> "$backup_dir/dns-restore"
    fi
else
    echo 'cdn_dns=kept_existing_proxied_record'
fi

# 1. Loopback VLESS+WS inbound. It reuses the existing VLESS credential so the
#    operator keeps one UUID across lanes; no new secret is invented here.
VPS_CDN_UUID="$(python3 - "$XRAY_CONF" <<'PY'
import glob
import json
import os
import sys

found = ''
for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    if found:
        break
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for entry in data.get('inbounds', []) or []:
        if found:
            break
        if entry.get('protocol') != 'vless':
            continue
        for client in (entry.get('settings') or {}).get('clients') or []:
            if client.get('id'):
                found = client['id']
                break
if found:
    sys.stdout.write(found)
else:
    sys.exit(1)
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
local_wrote=1

temporary_conf="$(mktemp "$NGINX_CONF_D/.cdn-lane.XXXXXX")"
# 2. nginx terminates TLS on the Cloudflare origin port and fronts the decoy
#    site, mirroring the XHTTP lane's shape. No client-IP headers: the inner
#    inbound has no trustedXForwardedFor and would only log warnings. Render
#    to a sibling before replacement so a shell error cannot truncate the
#    currently served config.
cat > "$temporary_conf" <<EOF
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
    add_header Strict-Transport-Security "max-age=31536000" always;
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
        # Cloudflare passes the branded page through; a stock nginx 404 would
        # distinguish this fallback origin from the rest of the decoy site.
        error_page 404 =404 /404.html;
        try_files \$uri \$uri/ =404;
    }
}
EOF
chmod 644 "$temporary_conf"
mv -f -- "$temporary_conf" "$TARGET_FILE"

if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
    echo 'XRAY_CONFIG_TEST_FAILED' >&2
    rollback
fi
if ! nginx -t >/dev/null 2>&1; then
    echo 'NGINX_TEST_FAILED' >&2
    rollback
fi
# A template-only change must not bounce every lane on the host: xray is
# restarted only when the inbound fragment actually changed (or it is down).
xray_changed=0
if [ -f "$backup_dir/fragment-absent" ] || ! cmp -s "$FRAGMENT" "$backup_dir/xray/fragment.json"; then
    xray_changed=1
fi
if [ "$xray_changed" -eq 1 ] || ! systemctl is-active --quiet xray; then
    if ! systemctl restart xray; then
        echo 'XRAY_RESTART_FAILED' >&2
        rollback
    fi
else
    echo 'xray_restart=skipped(unchanged inbound)'
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

# Tracing is on inside the payload; capture stderr so a failing apply shows the
# exact command that tripped `set -e` instead of only the exit code.
& (Get-Command Invoke-LauncherRemoteCommand) -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "cdn-lane" 2>&1 |
  ForEach-Object { [Console]::WriteLine([string]$_) }











