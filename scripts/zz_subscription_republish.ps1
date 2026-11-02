#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("zz")]
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

# vasma's subscription file is generated from its own inbounds. Repointing the
# REALITY lane from sing-box 22752 to Xray 443 keeps the same UUID, shortId and
# keypair, so the published node has to follow. The local list is rebuilt in
# place from the live configs, and the client-visible base64 copy under
# /etc/v2ray-agent/subscribe/default is regenerated with vasma's own
# md5(email+salt) naming. Writing only subscribe_local left the served URL on
# the pre-migration node list (REALITY on the retired 22752) for a month while
# the lane reported "applied".
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
LOCK_FILE="${VPS_ZZ_SUB_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
SUB_DIR="${VPS_ZZ_SUB_DIR:-/etc/v2ray-agent/subscribe_local/default}"
SERVED_DIR="${VPS_ZZ_SUB_SERVED_DIR:-/etc/v2ray-agent/subscribe/default}"
SALT_FILE="${VPS_ZZ_SUB_SALT_FILE:-/etc/v2ray-agent/subscribe_local/subscribeSalt}"
XRAY_CONF="${VPS_ZZ_SUB_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_CONF="${VPS_ZZ_SUB_SB_CONF:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_CONF_D="${VPS_ZZ_SUB_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
SUBSCRIBE_CONF="$NGINX_CONF_D/subscribe.conf"
BACKUP_PARENT="${VPS_ZZ_SUB_BACKUP_DIR:-/var/backups}"
STATUS_DIR="${VPS_ZZ_SUB_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
STATUS_FILE="$STATUS_DIR/zz-subscription.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    { printf 'name=zz-subscription\n'; printf 'finished_at=%s\n' "$finished"
      printf 'result=%s\n' "$result"; printf 'exit_code=%s\n' "$code"; } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

facts="$(python3 - "$XRAY_CONF" "$SB_CONF" "$NGINX_CONF_D" <<'PY'
import glob, json, os, re, subprocess, sys, urllib.parse

xray_conf, sb_conf, nginx_conf_d = sys.argv[1:4]

def q(v):
    return urllib.parse.quote(str(v), safe='')

# REALITY from Xray (the published public port is the dokodemo wrapper).
f = {}
for path in sorted(glob.glob(os.path.join(xray_conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        s = ib.get('settings') or {}
        if ib.get('protocol') == 'dokodemo-door' and str(s.get('address')) in ('127.0.0.1', 'localhost'):
            f.setdefault('front', {})[s.get('port')] = ib.get('port')
for path in sorted(glob.glob(os.path.join(xray_conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        st = ib.get('streamSettings') or {}
        rs = st.get('realitySettings') or {}
        clients = (ib.get('settings') or {}).get('clients') or []
        if ib.get('protocol') != 'vless' or st.get('security') != 'reality' or not clients:
            continue
        sids = [x for x in (rs.get('shortIds') or []) if x]
        if not sids:
            continue
        net = st.get('network') or 'tcp'
        inner = ib.get('port')
        public = f.get('front', {}).get(inner, inner)
        sni = (rs.get('serverNames') or [''])[0]
        item = {'net': net, 'port': public, 'uuid': clients[0].get('id'), 'pbk': rs.get('publicKey') or '',
                'sid': sids[0], 'sni': sni, 'flow': clients[0].get('flow') or ''}
        if net == 'xhttp':
            xs = st.get('xhttpSettings') or {}
            item['path'] = xs.get('path') or '/'
            item['host'] = xs.get('host') or sni
            f.setdefault('xhttp', []).append(item)
        elif net == 'tcp':
            f['reality'] = item

# XHTTP TLS lane. After xhttp_nginx_front.ps1 the loopback inbound carries no
# TLS settings, so the published node has to come from the nginx front (SNI and
# path) plus the dokodemo that forwards the public port to nginx.
front_conf = None
# Maintained-first, matching merged_subscription_lane.ps1 and the drift
# gate: the legacy zz renderer only wins if the unified front is absent.
for name in ('vps-launcher-xhttp-front.conf', 'vps-launcher-zz-xhttp-front.conf'):
    candidate = os.path.join(nginx_conf_d, name)
    if os.path.isfile(candidate):
        front_conf = candidate
        break
if front_conf:
    front_listen = None
    front_sni = None
    front_path = None
    front_inner = None
    for line in open(front_conf, encoding='utf-8'):
        line = line.strip()
        match = re.search(r'listen\s+(?:[\d.]+:)?(\d+)\s+ssl;', line)
        if match:
            front_listen = int(match.group(1))
        if line.startswith('server_name '):
            front_sni = line.split(None, 1)[1].rstrip(';')
        if line.startswith('location ^~ '):
            front_path = line.split(None, 2)[2].rstrip(' {')
        match = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+);', line)
        if match:
            front_inner = int(match.group(1))
    if front_sni and front_inner:
        for path in sorted(glob.glob(os.path.join(xray_conf, '*.json'))):
            try:
                data = json.load(open(path, encoding='utf-8'))
            except Exception:
                continue
            for ib in data.get('inbounds', []) or []:
                st = ib.get('streamSettings') or {}
                if st.get('network') != 'xhttp' or st.get('security') == 'reality':
                    continue
                if str(ib.get('listen') or '') not in ('127.0.0.1', '::1', 'localhost'):
                    continue
                if int(ib.get('port') or 0) != front_inner:
                    continue
                clients = (ib.get('settings') or {}).get('clients') or []
                if not clients:
                    continue
                xs = st.get('xhttpSettings') or {}
                f['xhttp_tls'] = {
                    'port': f.get('front', {}).get(front_listen, front_listen),
                    'uuid': clients[0].get('id'),
                    'sni': front_sni,
                    'host': xs.get('host') or front_sni,
                    'path': xs.get('path') or front_path or '/',
                    'mode': xs.get('mode') or 'auto',
                }
                break

for path in sorted(glob.glob(os.path.join(sb_conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        if ib.get('type') != 'hysteria2':
            continue
        users = ib.get('users') or []
        if not users or not users[0].get('password'):
            continue
        obfs = (ib.get('obfs') or {})
        item = {'port': ib.get('listen_port'), 'password': users[0]['password'],
                'sni': (ib.get('tls') or {}).get('server_name') or '',
                'obfs': obfs.get('password') if obfs.get('type') == 'salamander' else None}
        f['hy2_obfs' if obfs.get('type') == 'salamander' else 'hy2_base'] = item

print(json.dumps(f))
PY
)"

echo "zz_sub_state=$(printf '%s' "$facts" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("reality" if d.get("reality") else "missing")')"

paths="$(ls -1 "$SUB_DIR" 2>/dev/null || true)"
if [ -z "$paths" ]; then echo 'ZZ_SUB_NO_SALT_FILE' >&2; write_status failed 4; exit 4; fi
if [ "$(printf '%s\n' "$paths" | wc -l)" -ne 1 ]; then echo 'ZZ_SUB_AMBIGUOUS_SALT' >&2; write_status failed 4; exit 4; fi
target="$SUB_DIR/$paths"
email="$(basename "$target")"
salt="$(cat "$SALT_FILE" 2>/dev/null || true)"
if [ -z "$salt" ]; then echo 'ZZ_SUB_SALT_MISSING' >&2; write_status failed 4; exit 4; fi
# vasma names the served file md5(email + salt + newline); reproduce it
# byte-for-byte so the already-published URL keeps working.
email_md5="$(printf '%s%s\n' "$email" "$salt" | md5sum | awk '{print $1}')"
if [ -z "$email_md5" ]; then echo 'ZZ_SUB_SALT_INDEX_FAILED' >&2; write_status failed 4; exit 4; fi
served="$SERVED_DIR/$email_md5"

render() {
    printf '%s' "$facts" | python3 -c '
import json, sys, urllib.parse
f = json.load(sys.stdin)
links = []
r = f.get("reality")
if r:
    extra = "&flow=%s" % r["flow"] if r.get("flow") else ""
    links.append("vless://%s@%s:%s?encryption=none&security=reality&sni=%s&fp=chrome&pbk=%s&sid=%s&type=tcp%s#zz-vless_reality_vision"
                 % (r["uuid"], r["sni"], r["port"], r["sni"], r["pbk"], r["sid"], extra))
for x in f.get("xhttp", []):
    links.append("vless://%s@%s:%s?encryption=none&security=reality&sni=%s&fp=chrome&pbk=%s&sid=%s&type=xhttp&host=%s&path=%s&mode=auto#zz-VLESS_Reality_XHTTP"
                 % (x["uuid"], x["sni"], x["port"], x["sni"], x["pbk"], x["sid"], x["host"], urllib.parse.quote(x["path"], safe="")))
t = f.get("xhttp_tls")
if t:
    links.append("vless://%s@%s:%s?encryption=none&security=tls&type=xhttp&sni=%s&host=%s&fp=chrome&alpn=h2&path=%s&mode=%s#zz-VLESS_XHTTP_TLS"
                 % (t["uuid"], t["sni"], t["port"], t["sni"], t["host"],
                    urllib.parse.quote(t["path"], safe=""), urllib.parse.quote(t["mode"], safe="")))
b = f.get("hy2_base")
if b:
    links.append("hysteria2://%s@%s:%s?peer=%s&insecure=0&sni=%s&alpn=h3#zz-singbox_hysteria2"
                 % (urllib.parse.quote(b["password"], safe=""), b["sni"], b["port"], b["sni"], b["sni"]))
o = f.get("hy2_obfs")
if o:
    links.append("hysteria2://%s@%s:%s?sni=%s&alpn=h3&obfs=salamander&obfs-password=%s#zz-hy2-obfs"
                 % (urllib.parse.quote(o["password"], safe=""), o["sni"], o["port"], o["sni"], urllib.parse.quote(o["obfs"] or "", safe="")))
print("\n".join(links))
'
}

want="$(render)"
count="$(printf '%s\n' "$want" | grep -cE '^(vless|hysteria2)://' || true)"
has_tls="$(printf '%s\n' "$want" | grep -cE '^vless://.*security=tls[^#]*type=xhttp' || true)"
echo "zz_sub_nodes=$count tls_node=$has_tls"
# The XHTTP TLS lane is the one this republish exists to add back; a list
# without it still "works" for Vision users and hides the missing lane.
if [ "$count" -lt 4 ] || [ "$has_tls" -ne 1 ]; then
    echo 'ZZ_SUB_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi

local_current=0
served_current=0
if [ -f "$target" ] && [ "$(cat "$target")" = "$want" ]; then local_current=1; fi
if [ -f "$served" ] && [ "$(base64 -d < "$served" 2>/dev/null || true)" = "$want" ]; then
    served_current=1
fi
echo "zz_sub_local=$local_current served=$served_current served_file_len=${#email_md5}"
if [ "$local_current" -eq 1 ] && [ "$served_current" -eq 1 ]; then
    echo 'zz_subscription=applied'
    write_status noop 0
    exit 0
fi
echo 'zz_subscription=drift'
if [ "$APPLY" != '1' ]; then write_status success 0; exit 0; fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then echo "REFUSE busy lock=$LOCK_FILE" >&2; write_status busy 75; exit 75; fi
backup_dir="$(mktemp -d "$BACKUP_PARENT/zz-sub.XXXXXX")"; chmod 700 "$backup_dir"
cp -a "$target" "$backup_dir/previous"
if [ -f "$served" ]; then cp -a "$served" "$backup_dir/served"; else : > "$backup_dir/served-absent"; fi

rollback() {
    # INT/TERM pass the signal exit code explicitly: the interrupted command's
    # rc can be 0, which would report a rolled-back apply as success.
    local rc="${1:-$?}"
    trap - ERR INT TERM
    set +e
    local failed=0
    cp -a "$backup_dir/previous" "$target" || failed=1
    if [ -f "$backup_dir/served" ]; then
        cp -a "$backup_dir/served" "$served" || failed=1
    else
        rm -f -- "$served" || failed=1
    fi
    if [ "$failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    write_status failed "$rc"
    exit "$rc"
}
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

printf '%s' "$want" > "$target"
chmod 644 "$target"
mkdir -p "$SERVED_DIR"
printf '%s\n' "$(base64 -w0 "$target")" > "$served"
chmod 644 "$served"
if [ "$(cat "$target")" != "$want" ] ||
   [ "$(base64 -d < "$served" 2>/dev/null || true)" != "$want" ]; then
    echo 'ZZ_SUB_WRITE_MISMATCH' >&2
    false
fi

# Prove the client-visible URL, not just the file: nginx aliases
# /s/default/<md5> to the served copy, so a 200 with the same node count is
# the lowest layer that matches what a subscription client downloads.
sub_host="$(sed -n 's/^[[:space:]]*server_name[[:space:]]\+\([^;]*\);.*/\1/p' "$SUBSCRIBE_CONF" 2>/dev/null | head -1)"
sub_port="$(sed -n 's/^[[:space:]]*listen[[:space:]]\+\([0-9]\+\).*/\1/p' "$SUBSCRIBE_CONF" 2>/dev/null | head -1)"
if [ -z "$sub_host" ] || [ -z "$sub_port" ]; then
    echo 'ZZ_SUB_HTTP_TARGET_MISSING' >&2
    false
fi
http="$(curl -sk -m 10 --resolve "$sub_host:$sub_port:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "https://$sub_host:$sub_port/s/default/$email_md5" 2>/dev/null || true)"
served_nodes="$(curl -sk -m 10 --resolve "$sub_host:$sub_port:127.0.0.1" \
    "https://$sub_host:$sub_port/s/default/$email_md5" 2>/dev/null |
    base64 -d 2>/dev/null | grep -cE '^(vless|hysteria2)://' || true)"
echo "zz_sub_http=${http:-000} served_nodes=${served_nodes:-0}"
if [ "$http" != '200' ] || [ "$served_nodes" != "$count" ]; then
    echo 'ZZ_SUB_VERIFY_FAILED' >&2
    false
fi
trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"; echo 'ZZ_SUB_APPLIED'; write_status success 0
'@.Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "zz-sub"
