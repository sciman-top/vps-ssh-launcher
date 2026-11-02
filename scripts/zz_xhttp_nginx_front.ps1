#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("zz")]
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

# XHTTP TLS lane behind nginx on zz. The public port stays on the Xray
# dokodemo wrapper because vasma republishes the subscription from
# inbound[1].port; the wrapper forwards to loopback nginx, which terminates
# TLS and proxies the XHTTP path to the plain loopback inbound. Client
# address/port/path/tls are unchanged, unknown paths get the decoy, unknown
# SNI is rejected.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
FRONT_PORT="__FRONT_PORT__"
LOCK_FILE="${VPS_ZZ_XHTTP_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
NGINX_CONF_D="${VPS_ZZ_XHTTP_CONF_DIR:-/etc/nginx/conf.d}"
TARGET_FILE="$NGINX_CONF_D/vps-launcher-zz-xhttp-front.conf"
XRAY_CONF="${VPS_ZZ_XHTTP_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
XRAY_BINARY="${VPS_ZZ_XHTTP_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
NGINX_ROOT="${VPS_ZZ_XHTTP_ROOT:-/usr/share/nginx/html}"
TLS_DIR="${VPS_ZZ_XHTTP_TLS_DIR:-/etc/v2ray-agent/tls}"
STATUS_DIR="${VPS_ZZ_XHTTP_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_ZZ_XHTTP_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/zz-xhttp-front.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    { printf 'name=zz-xhttp-front\n'; printf 'finished_at=%s\n' "$finished"
      printf 'result=%s\n' "$result"; printf 'exit_code=%s\n' "$code"; } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

# The maintained bwg/native front owns this profile's shape now. Keeping the
# old zz renderer alive would create one vhost with a missing HSTS header and
# immediately collide with the unified front's default_server on 15994.
MAINTAINED="$NGINX_CONF_D/vps-launcher-xhttp-front.conf"
if [ -f "$MAINTAINED" ] &&
   grep -qE '^[[:space:]]*server_name[[:space:]]+zz\.sciman\.top;' "$MAINTAINED" &&
   grep -qF -- 'proxy_pass http://127.0.0.1:45988;' "$MAINTAINED"; then
    if [ -f "$TARGET_FILE" ]; then
        # The retirement below deletes a remote file and reloads nginx; like
        # every other write in this lane it must wait for -Apply. Dry-run
        # only reports the pending retirement so the operator can schedule it.
        if [ "$APPLY" != '1' ]; then
            echo 'zz_xhttp_superseded_pending=maintained_front'
            write_status success 0
            exit 0
        fi
        exec 9>"$LOCK_FILE"
        if ! flock -n 9; then echo "REFUSE busy lock=$LOCK_FILE" >&2; write_status busy 75; exit 75; fi
        backup_dir="$(mktemp -d "$BACKUP_PARENT/zz-xhttp-front.XXXXXX")"
        chmod 700 "$backup_dir"
        cp -a "$TARGET_FILE" "$backup_dir/old-legacy-front.conf"
        rm -f -- "$TARGET_FILE"
        if ! nginx -t >/dev/null 2>&1 || ! systemctl reload nginx; then
            cp -a "$backup_dir/old-legacy-front.conf" "$TARGET_FILE"
            nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
            echo 'ZZ_XHTTP_LEGACY_RETIRE_FAILED' >&2
            write_status failed 1
            exit 1
        fi
        echo "APPLY_BACKUP_DIR=$backup_dir"
    fi
    echo 'zz_xhttp_superseded=maintained_front'
    write_status noop 0
    exit 0
fi

facts="$(python3 - "$XRAY_CONF" "$TARGET_FILE" "$FRONT_PORT" "$TLS_DIR" <<'PY'
import glob, json, os, re, subprocess, sys

conf_dir, target, front_port, tls_dir = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
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

conf_text = open(target, encoding='utf-8').read() if os.path.isfile(target) else ''
m = re.search(r'listen\s+127\.0\.0\.1:(\d+)\s+ssl;', conf_text); conf_listen = int(m.group(1)) if m else None
m = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+);', conf_text); conf_inner = int(m.group(1)) if m else None
m = re.search(r'ssl_certificate\s+(\S+);', conf_text); conf_cert = m.group(1) if m else ''
m = re.search(r'server_name\s+([^;]+);', conf_text); conf_sni = m.group(1).strip() if m else ''

entry = None
for path, ib in inbounds:
    stream = ib.get('streamSettings') or {}
    if stream.get('network') != 'xhttp' or not loopback(ib.get('listen')):
        continue
    if not ((ib.get('settings') or {}).get('clients') or []):
        continue
    xhttp = stream.get('xhttpSettings') or {}
    entry = {'file': path, 'inner': ib.get('port'), 'security': stream.get('security') or 'none',
             'sni': xhttp.get('host') or '', 'path': xhttp.get('path') or '/', 'host': xhttp.get('host') or ''}
    break
if invalid:
    print(json.dumps({'state': 'invalid'})); raise SystemExit(0)
if not entry:
    print(json.dumps({'state': 'not_applicable'})); raise SystemExit(0)

cert = os.path.join(tls_dir, entry['sni'] + '.crt') if entry['sni'] else ''
key = os.path.join(tls_dir, entry['sni'] + '.key') if entry['sni'] else ''
if not os.path.isfile(cert) or not os.path.isfile(key):
    cert = key = ''
    for cand in sorted(glob.glob(os.path.join(tls_dir, '*.crt'))):
        candidate_key = cand[:-4] + '.key'
        if not os.path.isfile(candidate_key):
            continue
        try:
            result = subprocess.run(
                ['openssl', 'x509', '-in', cand, '-noout', '-checkhost', entry['sni']],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            continue
        if result.returncode == 0 and 'does match' in result.stdout:
            cert, key = cand, candidate_key
            break

wrapper = None
wrapper_target = None
for path, ib in inbounds:
    if ib.get('protocol') != 'dokodemo-door' or loopback(ib.get('listen')):
        continue
    settings = ib.get('settings') or {}
    if str(settings.get('address')) not in ('127.0.0.1', 'localhost'):
        continue
    if settings.get('port') in (entry['inner'], front_port):
        wrapper = ib.get('port'); wrapper_target = settings.get('port'); break

entry['published'] = wrapper or 0
entry['front'] = front_port
entry['wrapper_target'] = wrapper_target
entry['cert'] = cert
entry['key'] = key
if entry['security'] == 'tls' and wrapper_target == entry['inner']:
    entry['state'] = 'bare'
elif (entry['security'] == 'none' and wrapper_target == front_port and conf_listen == front_port
      and conf_inner == entry['inner'] and conf_cert == cert and conf_sni == entry['sni']):
    entry['state'] = 'applied'
else:
    entry['state'] = 'drift'
print(json.dumps(entry))
PY
)"

field() {
    python3 -c 'import json,sys
v=json.loads(sys.argv[1])
for k in sys.argv[2].split("."):
    v=(v or {}).get(k) if isinstance(v,dict) else None
print("" if v is None else v)' "$facts" "$1"
}

state="$(field state)"
inner="$(field inner)"; published="$(field published)"; sni="$(field sni)"
path_value="$(field path)"; cert="$(field cert)"; key="$(field key)"
source_file="$(field file)"
echo "zz_xhttp_state=${state}"
echo "zz_xhttp_inner=${inner:-none} published=${published:-none} front=$FRONT_PORT sni=${sni:-none} path=${path_value:-none}"

TMP_BODY="$(mktemp)"; cleanup() { rm -f -- "$TMP_BODY"; }; trap cleanup EXIT INT TERM

decoys_ok() {
    local code sha decoy
    decoy="$(sha256sum "$NGINX_ROOT/index.html" 2>/dev/null | awk '{print $1}')"
    [ -n "$decoy" ] || return 1
    code="$(curl -sk -m 8 -o "$TMP_BODY" -w '%{http_code}' --resolve "$2:$1:127.0.0.1" "https://$2:$1/" 2>/dev/null || true)"
    sha="$(sha256sum "$TMP_BODY" 2>/dev/null | awk '{print $1}')"
    [ "$code" = '200' ] && [ "$sha" = "$decoy" ]
}

validate_front_facts() {
    # Ported from xhttp_nginx_front.ps1: the facts expand into an unquoted
    # heredoc, so a tampered xray JSON could inject nginx syntax that -t
    # cannot distinguish from the managed template.
    python3 - "$FRONT_PORT" "$sni" "$cert" "$key" "$NGINX_ROOT" \
        "$path_value" "$inner" <<'PY'
import re
import sys

port, sni, cert, key, root, path, inner = sys.argv[1:8]
if not re.fullmatch(r'[0-9]+', port) or not re.fullmatch(r'[0-9]+', inner):
    raise SystemExit('invalid XHTTP front port')
if any(not value or '\n' in value or '\r' in value for value in (cert, key, root, path)):
    raise SystemExit('invalid XHTTP front fact')
if not re.fullmatch(r'[A-Za-z0-9.-]+', sni):
    raise SystemExit('invalid XHTTP front hostname')
if not path.startswith('/') or ' ' in path or '\t' in path:
    raise SystemExit('invalid XHTTP front path')
PY
}

render_conf() {
    cat <<EOF
# Managed by vps-ssh-launcher scripts/zz_xhttp_nginx_front.ps1.
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
    add_header Strict-Transport-Security "max-age=31536000" always;
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
        # The bare Xray front answered every unknown path with an empty HTTP/2
        # 404 and no ordinary web headers. Keep that from becoming a stock
        # nginx 404 instead: serve the branded page with its real 404 status.
        error_page 404 =404 /404.html;
        try_files \$uri \$uri/ =404;
    }
}
EOF
}

conf_current() {
    local expected
    expected="$(mktemp)"
    # The header is allowed only when the generated file is a complete nginx
    # document. Command substitution around render_conf would otherwise
    # capture a diagnostic and write it to disk as a config line.
    if validate_front_facts >/dev/null 2>&1 &&
       render_conf > "$expected" &&
       grep -q '^# Managed by vps-ssh-launcher scripts/zz_xhttp_nginx_front.ps1.$' "$expected"; then
        cmp -s "$expected" "$TARGET_FILE"
        local result=$?
        rm -f -- "$expected"
        return "$result"
    fi
    rm -f -- "$expected"
    return 1
}

if [ "$state" = 'invalid' ]; then echo 'ZZ_XHTTP_SOURCE_INVALID' >&2; write_status failed 4; exit 4; fi
if [ "$state" = 'not_applicable' ]; then echo 'zz_xhttp=not_applicable'; write_status noop 0; exit 0; fi

front_owned=0
if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$FRONT_PORT"; then front_owned=1; fi
echo "zz_xhttp_front_listening=$front_owned"

if [ "$state" = 'applied' ] && [ "$front_owned" -eq 1 ] && conf_current && decoys_ok "$published" "$sni"; then
    echo 'zz_xhttp=applied'; write_status noop 0; exit 0
fi
if [ "$state" = 'applied' ]; then echo 'zz_xhttp=drift'; else echo "zz_xhttp=$state"; fi
if [ "$APPLY" != '1' ]; then exit 0; fi  # dry run leaves the last real status untouched

for v in "$published" "$inner" "$sni" "$path_value" "$cert" "$key" "$source_file"; do
    [ -n "$v" ] || { echo 'ZZ_XHTTP_FACTS_INCOMPLETE' >&2; write_status failed 4; exit 4; }
done
if ! validate_front_facts; then echo 'ZZ_XHTTP_FACTS_INVALID' >&2; write_status failed 4; exit 4; fi
if [ ! -f "$cert" ] || [ ! -f "$key" ]; then echo 'ZZ_XHTTP_TLS_MATERIAL_MISSING' >&2; write_status failed 4; exit 4; fi
if ! openssl x509 -in "$cert" -noout -checkhost "$sni" 2>/dev/null | grep -q 'does match'; then
    echo "ZZ_XHTTP_TLS_HOST_MISMATCH sni=$sni" >&2
    write_status failed 4
    exit 4
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then echo "REFUSE busy lock=$LOCK_FILE" >&2; write_status busy 75; exit 75; fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/zz-xhttp-front.XXXXXX")"; chmod 700 "$backup_dir"
mkdir -p "$backup_dir/xray"; cp -a "$source_file" "$backup_dir/xray/inbound.json"
if [ -f "$TARGET_FILE" ]; then cp -a "$TARGET_FILE" "$backup_dir/previous.conf"; fi

rollback() {
    local rc="${1:-1}"
    trap - ERR HUP INT TERM; set +e; local rf=0
    rm -f -- "${tmp_conf:-}" 2>/dev/null || true
    cp -a "$backup_dir/xray/inbound.json" "$source_file" || rf=1
    if [ -f "$backup_dir/previous.conf" ]; then cp -a "$backup_dir/previous.conf" "$TARGET_FILE" || rf=1; else rm -f -- "$TARGET_FILE" || rf=1; fi
    "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1 || rf=1
    nginx -t >/dev/null 2>&1 || rf=1
    systemctl restart xray >/dev/null 2>&1 || rf=1
    systemctl reload nginx >/dev/null 2>&1 || rf=1
    systemctl is-active --quiet xray && systemctl is-active --quiet nginx || rf=1
    if [ "$rf" -eq 0 ]; then echo "ROLLBACK_VERIFIED backup=$backup_dir"; else echo "ROLLBACK_FAILED backup=$backup_dir" >&2; fi
    write_status failed "$rc"; exit "$rc"
}
trap rollback ERR; trap 'rollback 129' HUP; trap 'rollback 130' INT; trap 'rollback 143' TERM

python3 - "$source_file" "$inner" "$published" "$FRONT_PORT" <<'PY'
import json, sys
path=sys.argv[1]; inner, published, front = (int(v) for v in sys.argv[2:5])
data=json.load(open(path, encoding='utf-8')); kept=[]; placed=False
for ib in data.get('inbounds', []) or []:
    s=ib.get('settings') or {}
    if ib.get('protocol')=='dokodemo-door' and str(s.get('address')) in ('127.0.0.1','localhost') and s.get('port') in (inner, front):
        if placed: continue
        ib.setdefault('tag','zz-xhttp-front'); ib['port']=published; ib['listen']='0.0.0.0'
        ib['settings']={'address':'127.0.0.1','port':front,'network':'tcp'}; placed=True
    kept.append(ib)
for ib in kept:
    st=ib.get('streamSettings') or {}
    if st.get('network')=='xhttp' and ib.get('port')==inner:
        st['security']='none'; st.pop('tlsSettings',None)
data['inbounds']=kept
json.dump(data, open(path,'w',encoding='utf-8'), ensure_ascii=False, indent=2); open(path,'a').write('\n')
PY

# Render into a sibling temp file, then move: a mid-pipeline failure under
# set -e must never leave a truncated live vhost behind.
tmp_conf="$(mktemp "$NGINX_CONF_D/.zz-xhttp-front.XXXXXX")"
if render_conf > "$tmp_conf"; then
    mv -f -- "$tmp_conf" "$TARGET_FILE"
else
    rm -f -- "$tmp_conf"
    echo 'ZZ_XHTTP_RENDER_FAILED' >&2
    rollback
fi

if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then echo 'XRAY_CONFIG_TEST_FAILED' >&2; rollback; fi
if ! nginx -t >/dev/null 2>&1; then echo 'NGINX_TEST_FAILED' >&2; rollback; fi
if ! systemctl restart xray; then echo 'XRAY_RESTART_FAILED' >&2; rollback; fi
if ! systemctl reload nginx; then echo 'NGINX_RELOAD_FAILED' >&2; rollback; fi

lane_ok=0
for attempt in 1 2 3 4 5 6; do
    if systemctl is-active --quiet xray &&
       ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$FRONT_PORT" &&
       ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$published\$" &&
       ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$inner\$" &&
       decoys_ok "$published" "$sni"; then lane_ok=1; break; fi
    sleep 1
done
if [ "$lane_ok" -ne 1 ]; then echo 'ZZ_XHTTP_NOT_READY' >&2; rollback; fi
trap - ERR HUP INT TERM
# Apply-time hygiene: keep the newest 8 lane backups so repeated applies
# cannot grow /var/backups without bound (mirrors the updater keep-8 prune).
find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d -name 'zz-xhttp-front.*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +9 | cut -d' ' -f2- \
    | while IFS= read -r old_backup_dir; do
        rm -rf -- "$old_backup_dir" || echo 'BACKUP_PRUNE_FAILED' >&2
    done
echo 'BACKUP_PRUNE scope=zz-xhttp-front policy=keep_8'
echo "APPLY_BACKUP_DIR=$backup_dir"; echo 'ZZ_XHTTP_APPLIED'; write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__FRONT_PORT__", "$FrontPort")

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "zz-xhttp-front"
