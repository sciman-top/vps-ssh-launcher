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
# keypair, so the published node has to follow. The file is rebuilt in place
# (same salt filename, so the subscription URL is unchanged) from the live
# configs; every other node is regenerated from the same sources vasma reads,
# so nothing is hand-edited that vasma would overwrite differently.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
LOCK_FILE="${VPS_ZZ_SUB_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
SUB_DIR="${VPS_ZZ_SUB_DIR:-/etc/v2ray-agent/subscribe_local/default}"
XRAY_CONF="${VPS_ZZ_SUB_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_CONF="${VPS_ZZ_SUB_SB_CONF:-/etc/v2ray-agent/sing-box/conf/config}"
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

facts="$(python3 - "$XRAY_CONF" "$SB_CONF" <<'PY'
import glob, json, os, re, subprocess, sys, urllib.parse

xray_conf, sb_conf = sys.argv[1:3]

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
echo "zz_sub_nodes=$count"
if [ "$count" -lt 3 ]; then echo 'ZZ_SUB_INCOMPLETE' >&2; write_status failed 4; exit 4; fi

current=0
if [ -f "$target" ] && [ "$(cat "$target")" = "$want" ]; then current=1; fi
echo "zz_sub_current=$current"
if [ "$current" -eq 1 ]; then echo 'zz_subscription=applied'; write_status noop 0; exit 0; fi
echo 'zz_subscription=drift'
if [ "$APPLY" != '1' ]; then write_status success 0; exit 0; fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then echo "REFUSE busy lock=$LOCK_FILE" >&2; write_status busy 75; exit 75; fi
backup_dir="$(mktemp -d "$BACKUP_PARENT/zz-sub.XXXXXX")"; chmod 700 "$backup_dir"
cp -a "$target" "$backup_dir/previous"
printf '%s' "$want" > "$target"
chmod 644 "$target"
if [ "$(cat "$target")" != "$want" ]; then
    cp -a "$backup_dir/previous" "$target"
    echo 'ZZ_SUB_WRITE_MISMATCH' >&2; write_status failed 1; exit 1
fi
echo "APPLY_BACKUP_DIR=$backup_dir"; echo 'ZZ_SUB_APPLIED'; write_status success 0
'@.Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "zz-sub"
