#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$FrontPort = 15994,
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
if ($FrontPort -lt 1024 -or $FrontPort -gt 65535) {
  throw "-FrontPort must be an unprivileged port (1024..65535)."
}

$applyLiteral = if ($Apply) { "1" } else { "0" }

# vasma publishes the XHTTP TLS lane as a dokodemo-door wrapper in front of a
# loopback TLS inbound. That makes the public port a bare Xray listener: every
# path answers an empty `HTTP/2 404` with no `Server`, no `Content-Type` and no
# body, while the same host's nginx answers a normal 404. Xray's own XHTTP
# guidance is explicit that XHTTP should hide behind real nginx.
#
# The public port must stay bound by Xray, not by nginx: vasma derives the
# published port from `inbounds[1].port` when it refreshes the subscription,
# and a menu reconfiguration rewrites that wrapper. If nginx owned the public
# port instead, the regenerated wrapper would collide with it and xray would
# fail to start - an outage, not a silent revert.
#
# So the wrapper survives and simply forwards to nginx on loopback:
#   client -> dokodemo 0.0.0.0:<published> -> 127.0.0.1:<front> (nginx, TLS)
#          -> 127.0.0.1:<inner> (plain XHTTP)
# Client configs (address/host/path/port/tls) do not change, unknown paths get
# the decoy site, unknown SNI is rejected, and a vasma reinstall degrades to the
# bare listener (drift gate reports `xhttp-front`) instead of breaking the core.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
FRONT_PORT="__FRONT_PORT__"
LOCK_FILE="${VPS_XHTTP_FRONT_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
NGINX_CONF_D="${VPS_XHTTP_FRONT_CONF_DIR:-/etc/nginx/conf.d}"
TARGET_FILE="$NGINX_CONF_D/vps-launcher-xhttp-front.conf"
XRAY_CONF="${VPS_XHTTP_FRONT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
XRAY_BINARY="${VPS_XHTTP_FRONT_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
NGINX_ROOT="${VPS_XHTTP_FRONT_ROOT:-/usr/share/nginx/html}"
TLS_DIR="${VPS_XHTTP_FRONT_TLS_DIR:-/etc/v2ray-agent/tls}"
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

facts="$(python3 - "$XRAY_CONF" "$TARGET_FILE" "$FRONT_PORT" "$TLS_DIR" <<'PY'
import glob
import json
import os
import re
import sys

conf_dir, target, front_port, tls_dir = (
    sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
)
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


conf_text = ''
if os.path.isfile(target):
    try:
        conf_text = open(target, encoding='utf-8').read()
    except OSError:
        conf_text = ''
conf_listen = None
match = re.search(r'listen\s+(?:[\d.]+:)?(\d+)\s+ssl;', conf_text)
if match:
    conf_listen = int(match.group(1))
conf_inner = None
match = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+);', conf_text)
if match:
    conf_inner = int(match.group(1))

entry = None
for path, ib in inbounds:
    stream = ib.get('streamSettings') or {}
    if stream.get('network') != 'xhttp' or not loopback(ib.get('listen')):
        continue
    if not ((ib.get('settings') or {}).get('clients') or []):
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
    }
    if not entry['cert'] and entry['sni']:
        # A previously fronted tree no longer carries tlsSettings, so fall back
        # to vasma's on-disk naming for this host's own certificate.
        entry['cert'] = os.path.join(tls_dir, entry['sni'] + '.crt')
        entry['key'] = os.path.join(tls_dir, entry['sni'] + '.key')
    break

if invalid:
    print(json.dumps({'state': 'invalid'}))
    raise SystemExit(0)
if not entry:
    print(json.dumps({'state': 'not_applicable'}))
    raise SystemExit(0)

# The wrapper is either still vasma-shaped (-> inner, TLS) or already ours
# (-> front_port). Either way its own port is the published port.
wrapper = None
wrapper_target = None
for path, ib in inbounds:
    if ib.get('protocol') != 'dokodemo-door' or loopback(ib.get('listen')):
        continue
    settings = ib.get('settings') or {}
    if str(settings.get('address')) not in ('127.0.0.1', 'localhost'):
        continue
    if settings.get('port') in (entry['inner'], front_port):
        wrapper = ib.get('port')
        wrapper_target = settings.get('port')
        break

published = wrapper or conf_listen or 0
entry['published'] = published
entry['front'] = front_port
entry['wrapper_target'] = wrapper_target
if entry['security'] == 'tls' and wrapper_target == entry['inner']:
    entry['state'] = 'bare'
elif (
    entry['security'] == 'none'
    and wrapper_target == front_port
    and conf_listen == front_port
    and conf_inner == entry['inner']
):
    entry['state'] = 'applied'
elif entry['security'] == 'none' and not wrapper and conf_listen == published:
    # Deployed shape from the first revision: nginx owns the public port and
    # there is no wrapper. Migrate instead of reporting a false failure.
    entry['state'] = 'legacy'
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
published="$(field published)"
sni="$(field sni)"
path_value="$(field path)"
cert="$(field cert)"
key="$(field key)"
source_file="$(field file)"

echo "xhttp_front_state=${state}"
echo "xhttp_front_inner=${inner:-none} published=${published:-none} front=$FRONT_PORT sni=${sni:-none} path=${path_value:-none}"

TMP_BODY="$(mktemp)"
cleanup() { rm -f -- "$TMP_BODY"; }
trap cleanup EXIT INT TERM

decoys_ok() {
    # $1 published port, $2 sni -> 1 when an unauthenticated GET returns the
    # decoy page through the whole dokodemo -> nginx chain.
    local code sha decoy
    decoy="$(sha256sum "$NGINX_ROOT/index.html" 2>/dev/null | awk '{print $1}')"
    [ -n "$decoy" ] || return 1
    code="$(curl -sk -m 8 -o "$TMP_BODY" -w '%{http_code}' \
        --resolve "$2:$1:127.0.0.1" "https://$2:$1/" 2>/dev/null || true)"
    sha="$(sha256sum "$TMP_BODY" 2>/dev/null | awk '{print $1}')"
    [ "$code" = '200' ] && [ "$sha" = "$decoy" ]
}

# Single source of truth for the managed front. Both the staleness check and
# the apply path render through here: the previous "already applied" shortcut
# only looked at the inbound shape and the decoy response, so a template fix
# (headers, timeouts, TLS directives) was silently skipped on every host that
# was already fronted.
#
# The inner inbound is plain XHTTP on loopback, so it never sets
# sockopt.trustedXForwardedFor: forwarding X-Real-IP/X-Forwarded-For would be
# ignored by Xray and would write one warning line per request into
# error.log. Only the upstream headers XHTTP actually needs are set.
render_conf() {
    cat <<EOF
# Managed by vps-ssh-launcher scripts/xhttp_nginx_front.ps1.
# Do not edit by hand; re-run the lane instead.
server {
    listen 127.0.0.1:$FRONT_PORT ssl default_server;
    ssl_reject_handshake on;
}

server {
    listen 127.0.0.1:$FRONT_PORT ssl;
    http2 on;
    server_name $sni;
    ssl_certificate $cert;
    ssl_certificate_key $key;
    ssl_protocols TLSv1.2 TLSv1.3;
    root $NGINX_ROOT;
    client_max_body_size 0;
    access_log off;

    location ^~ $path_value {
        proxy_pass http://127.0.0.1:$inner;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        send_timeout 3600s;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF
}

conf_current() {
    [ -f "$TARGET_FILE" ] && render_conf | cmp -s - "$TARGET_FILE"
}

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

front_owned=0
if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$FRONT_PORT"; then
    front_owned=1
fi
echo "front_loopback_listening=$front_owned"

if [ "$state" = 'applied' ] && [ "$front_owned" -eq 1 ] && conf_current &&
   decoys_ok "$published" "$sni"; then
    echo 'xhttp_front=applied'
    write_status noop 0
    exit 0
fi
if [ "$state" = 'applied' ]; then
    if ! conf_current; then
        echo 'xhttp_front_template=stale'
    fi
    echo 'xhttp_front=drift'
else
    echo "xhttp_front=$state"
fi
if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi
if [ -z "$published" ] || [ "$published" = '0' ] || [ -z "$inner" ] || [ -z "$sni" ] ||
   [ -z "$path_value" ] || [ -z "$cert" ] || [ -z "$key" ] || [ -z "$source_file" ]; then
    echo 'XHTTP_FRONT_FACTS_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi
if [ "$published" = "$FRONT_PORT" ]; then
    echo 'XHTTP_FRONT_PORT_COLLISION' >&2
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
    # xray first: restoring a wrapper on the published port needs it free, and
    # the current nginx would otherwise still hold the loopback front.
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

# 1. Keep the published port on Xray (vasma reads it) and point the wrapper at
#    nginx; the loopback inbound becomes plain HTTP because nginx terminates TLS.
python3 - "$source_file" "$inner" "$published" "$FRONT_PORT" <<'PY'
import json
import sys

path = sys.argv[1]
inner, published, front = (int(value) for value in sys.argv[2:5])
data = json.load(open(path, encoding='utf-8'))
kept = []
placed = False
for ib in data.get('inbounds', []) or []:
    settings = ib.get('settings') or {}
    is_wrapper = (
        ib.get('protocol') == 'dokodemo-door'
        and str(settings.get('address')) in ('127.0.0.1', 'localhost')
        and settings.get('port') in (inner, front)
    )
    if is_wrapper:
        if placed:
            continue
        ib.setdefault('tag', 'vps-launcher-xhttp-front')
        ib['port'] = published
        ib['listen'] = '0.0.0.0'
        ib.setdefault('settings', {})
        ib['settings']['address'] = '127.0.0.1'
        ib['settings']['port'] = front
        ib['settings']['network'] = 'tcp'
        placed = True
    kept.append(ib)
if not placed:
    kept.append({
        'tag': 'vps-launcher-xhttp-front',
        'port': published,
        'listen': '0.0.0.0',
        'protocol': 'dokodemo-door',
        'settings': {'address': '127.0.0.1', 'port': front, 'network': 'tcp'},
    })
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

# 2. nginx terminates TLS on loopback and fronts the decoy site.
render_conf > "$TARGET_FILE"

xray_changed=0
if ! cmp -s "$source_file" "$backup_dir/xray/inbound.json"; then
    xray_changed=1
fi

if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
    echo 'XRAY_CONFIG_TEST_FAILED' >&2
    rollback
fi
if ! nginx -t; then
    echo 'NGINX_TEST_FAILED' >&2
    rollback
fi
# nginx first: in the legacy shape it still owns the published port, and xray
# cannot bind it until nginx releases it. A graceful reload keeps the old
# workers (and their listening socket) alive, so the migration needs a
# restart; the steady-state shape only touches the loopback front and can
# reload.
if [ "$state" = 'legacy' ]; then
    if ! systemctl restart nginx; then
        echo 'NGINX_RESTART_FAILED' >&2
        rollback
    fi
elif ! systemctl reload nginx; then
    echo 'NGINX_RELOAD_FAILED' >&2
    rollback
fi
# A template-only change must not bounce every lane on the host: xray is
# restarted only when the inbound file actually changed (or it is down).
if [ "$xray_changed" -eq 1 ] || ! systemctl is-active --quiet xray; then
    if ! systemctl restart xray; then
        echo 'XRAY_RESTART_FAILED' >&2
        rollback
    fi
else
    echo 'xray_restart=skipped(unchanged inbound)'
fi

lane_ready() {
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$FRONT_PORT" &&
        ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$published\$" &&
        ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$inner\$" &&
        conf_current &&
        decoys_ok "$published" "$sni"
}
lane_ok=0
for attempt in 1 2 3 4 5 6; do
    if systemctl is-active --quiet xray && lane_ready; then
        lane_ok=1
        break
    fi
    sleep 1
done
if [ "$lane_ok" -ne 1 ]; then
    systemctl is-active --quiet xray || echo 'XRAY_INACTIVE' >&2
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$FRONT_PORT" || echo 'FRONT_LOOPBACK_NOT_LISTENING' >&2
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$published\$" || echo 'PUBLISHED_PORT_NOT_LISTENING' >&2
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$inner\$" || echo 'INNER_INBOUND_NOT_LISTENING' >&2
    decoys_ok "$published" "$sni" || echo 'DECOY_PAGE_NOT_SERVED' >&2
    rollback
fi
trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'XHTTP_FRONT_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__FRONT_PORT__", "$FrontPort")

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "xhttp-front"
