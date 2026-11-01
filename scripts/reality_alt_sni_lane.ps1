#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [string]$AltName,
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

# One hostname per host, kept inside the primary SNI's zone so the existing
# wildcard certificate already covers it. A different registrable domain would
# have to be added here once the user owns and points one.
$defaultAlt = @{ bwg = "static.sciman.top"; zz = "static-zz.sciman.top" }[$Profile]
if (-not $AltName) { $AltName = $defaultAlt }
if ($AltName -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$') {
  throw "-AltName must be a lowercase hostname without shell syntax."
}

# Every direct lane currently shares one SNI. An exact-hostname block of that
# name takes five of the six lanes down at once, and only the CDN lane is left.
# Adding a second name to the same REALITY inbounds keeps the existing clients
# untouched while giving every client a switchable name on a direct path.
# It does not survive an IP block or a whole-zone block; the CDN lane stays the
# answer for the first and nothing local answers the second.
$applyLiteral = if ($Apply) { "1" } else { "0" }

$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
ALT="__ALT_NAME__"
APPLY="__APPLY__"
XRAY_BIN="${VPS_ALT_SNI_XRAY_BIN:-/etc/v2ray-agent/xray/xray}"
XRAY_CONF="${VPS_ALT_SNI_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
NGINX_CONF_D="${VPS_ALT_SNI_NGINX_DIR:-/etc/nginx/conf.d}"
NGINX_ROOT="${VPS_ALT_SNI_NGINX_ROOT:-/usr/share/nginx/html}"
VHOST="$NGINX_CONF_D/vps-launcher-alt-sni.conf"
MARKER="/etc/v2ray-agent/.vps-launcher-alt-sni"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

python3 - "$XRAY_CONF" "$ALT" <<'PY' > /tmp/alt-sni-plan.json
import glob
import json
import os
import sys

conf_dir, alt = sys.argv[1:3]
front = {}
targets = []
for path in sorted(glob.glob(os.path.join(conf_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for entry in data.get('inbounds', []) or []:
        settings = entry.get('settings') or {}
        if entry.get('protocol') == 'dokodemo-door' and str(settings.get('address')) in ('127.0.0.1', 'localhost'):
            front[settings.get('port')] = entry.get('port')
    for entry in data.get('inbounds', []) or []:
        stream = entry.get('streamSettings') or {}
        if stream.get('security') != 'reality':
            continue
        rs = stream.get('realitySettings') or {}
        names = [n for n in (rs.get('serverNames') or []) if n]
        clients = (entry.get('settings') or {}).get('clients') or []
        if not names or not clients:
            continue
        target = str(rs.get('target') or '')
        targets.append({
            'file': path,
            'serverNames': names,
            'target': target,
            'targetPort': int(target.rsplit(':', 1)[1]) if target.count(':') == 1 else 443,
            'port': entry.get('port'),
            'front': front.get(entry.get('port')),
            'network': stream.get('network') or 'tcp',
            'uuid': clients[0].get('id') or '',
            'pbk': rs.get('publicKey') or '',
            'sid': next((s for s in (rs.get('shortIds') or []) if s), ''),
            'hasAlt': alt in names,
        })
print(json.dumps({'alt': alt, 'targets': targets}, indent=2))
PY

echo "==reality-alt-sni-discovery=="
python3 - /tmp/alt-sni-plan.json <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1]))
targets = plan['targets']
if not targets:
    print('reality_inbounds=none')
    raise SystemExit(2)
print('reality_inbounds=%d' % len(targets))
for t in targets:
    print('sni_list=%s target=%s port=%s front=%s alt_present=%s' % (
        ','.join(t['serverNames']), t['target'] or 'none', t['port'], t['front'], t['hasAlt']))
PY

ALT_PORT="$(python3 -c 'import json,sys; t=json.load(open("/tmp/alt-sni-plan.json"))["targets"]; print(t[0]["targetPort"])')"
echo "alt_serving_port=$ALT_PORT"

PUBLIC_IP="$(curl -s -m 8 https://api.ipify.org || true)"
ALT_DNS="$(getent ahostsv4 "$ALT" 2>/dev/null | awk '{print $1}' | sort -u | head -1 || true)"
echo "public_ip=${PUBLIC_IP:-unknown} alt_dns=${ALT_DNS:-unresolved}"
if [ -z "$ALT_DNS" ]; then
    echo "ALT_SNI_DNS_MISSING name=$ALT" >&2
    exit 2
fi
if [ -n "$PUBLIC_IP" ] && [ "$ALT_DNS" != "$PUBLIC_IP" ]; then
    echo "ALT_SNI_DNS_MISMATCH name=$ALT resolves=$ALT_DNS expected=$PUBLIC_IP" >&2
    exit 2
fi

if [ "$APPLY" != '1' ]; then
    echo 'ALT_SNI_DRY_RUN=1'
    echo 'ALT_SNI_PLAN=add-server-name,write-vhost,restart-xray,reload-nginx,verify-lane'
    exit 0
fi

BACKUP="$XRAY_CONF/backup-alt-sni-$STAMP"
mkdir -p "$BACKUP"
TOUCHED=()
restore() {
    local rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ]; then
        echo "ALT_SNI_ROLLBACK reason=exit_$rc" >&2
        for f in "${TOUCHED[@]:-}"; do
            [ -n "$f" ] || continue
            if [ -f "$BACKUP/$(basename "$f")" ]; then cp -a "$BACKUP/$(basename "$f")" "$f"; fi
        done
        if [ -f "$BACKUP/$(basename "$VHOST")" ]; then cp -a "$BACKUP/$(basename "$VHOST")" "$VHOST"; else rm -f -- "$VHOST"; fi
        rm -f -- "$MARKER"
        rm -f -- /tmp/alt-sni-plan.json
        if [ -n "${WORK:-}" ]; then rm -rf -- "$WORK"; fi
        nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
        systemctl restart xray >/dev/null 2>&1 || true
        echo 'ALT_SNI_APPLY_FAILED'
    fi
    exit "$rc"
}
trap restore EXIT

# 1. Serve the alt name from the same strict vhost the REALITY fallback uses.
if [ -f "$VHOST" ]; then cp -a "$VHOST" "$BACKUP/$(basename "$VHOST")"; fi
# Pick a certificate/key pair that actually covers ALT. Taking the first pair
# from conf.d can select an unrelated vhost and leave the new SNI serving the
# wrong certificate while nginx -t still passes.
tls_material="$(python3 - "$NGINX_CONF_D" "$ALT" <<'PY'
import glob
import os
import re
import subprocess
import sys

conf_dir, host = sys.argv[1:3]
cert_re = re.compile(r'^\s*ssl_certificate\s+([^;]+);')
key_re = re.compile(r'^\s*ssl_certificate_key\s+([^;]+);')
for path in sorted(glob.glob(os.path.join(conf_dir, '*.conf'))):
    certs = []
    keys = []
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except OSError:
        continue
    for line in lines:
        match = cert_re.match(line)
        if match:
            certs.append(match.group(1).strip())
        match = key_re.match(line)
        if match:
            keys.append(match.group(1).strip())
    for cert, key in zip(certs, keys):
        if not os.path.isfile(cert) or not os.path.isfile(key):
            continue
        try:
            result = subprocess.run(
                ['openssl', 'x509', '-in', cert, '-noout', '-checkhost', host],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            continue
        if result.returncode == 0 and 'does match' in result.stdout:
            print(cert)
            print(key)
            raise SystemExit(0)
PY
)"
CERT="$(printf '%s\n' "$tls_material" | sed -n '1p')"
KEY="$(printf '%s\n' "$tls_material" | sed -n '2p')"
if [ -z "$CERT" ] || [ -z "$KEY" ]; then
    echo 'ALT_SNI_CERT_NOT_FOUND' >&2
    exit 3
fi
# The fallback vhost binds the same port family as nginx itself. When that
# port is dual-stack, an IPv4-only alt vhost would send alt-name clients to
# the [::] default_server and get ssl_reject_handshake.
ALT_V6_LISTEN=""
if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "\[::\]:$ALT_PORT"; then
    ALT_V6_LISTEN="    listen [::]:$ALT_PORT ssl;"
fi
TMPV="$(mktemp "$NGINX_CONF_D/.alt-sni.XXXXXX")"
cat > "$TMPV" <<NGINX
# Second SNI for the direct REALITY lanes. Kept beside the vasma-generated
# files so a vasma reinstall leaves it intact; proxy_hardening_drift.ps1
# reports reality-alt-sni when it disappears or the xray side loses the name.
server {
    listen $ALT_PORT ssl;
$ALT_V6_LISTEN
    http2 on;
    server_name $ALT;
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    root $NGINX_ROOT;
    access_log off;
    location / {
        try_files \$uri \$uri/ =404;
    }
}
NGINX
chmod 644 "$TMPV"
mv -f -- "$TMPV" "$VHOST"

# 2. Add the alt name to every REALITY inbound, config by config.
while IFS= read -r f; do
    [ -n "$f" ] || continue
    cp -a "$f" "$BACKUP/$(basename "$f")"
    TOUCHED+=("$f")
    python3 - "$f" "$ALT" <<'PY'
import json
import sys

path, alt = sys.argv[1:3]
data = json.load(open(path, encoding='utf-8'))
changed = False
for entry in data.get('inbounds', []) or []:
    rs = (entry.get('streamSettings') or {}).get('realitySettings')
    if not rs:
        continue
    names = [n for n in (rs.get('serverNames') or []) if n]
    if alt not in names:
        names.append(alt)
        rs['serverNames'] = names
        changed = True
if changed:
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
        fh.write('\n')
PY
done < <(python3 -c 'import json; print("\n".join(sorted({t["file"] for t in json.load(open("/tmp/alt-sni-plan.json"))["targets"]})))')

nginx -t
"$XRAY_BIN" run -test -confdir "$XRAY_CONF" > /dev/null
systemctl reload nginx
systemctl restart xray

for _ in $(seq 1 20); do
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qE ":$ALT_PORT\$" && break
    sleep 0.5
done
ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qE ":$ALT_PORT\$" || {
    echo "alt_port_not_listening port=$ALT_PORT" >&2
    exit 4
}

# 3. Prove the fallback actually serves a certificate that covers the alt name.
# `openssl s_client` still prints "Verify return code: 0" when nginx rejects the
# name and sends no certificate, so the served leaf itself is the evidence.
ZONE="${ALT#*.}"
HS_OK=0
for _ in $(seq 1 10); do
    SERVED="$(timeout 8 openssl s_client -connect "127.0.0.1:$ALT_PORT" -servername "$ALT" </dev/null 2>/dev/null \
        | openssl x509 -noout -ext subjectAltName 2>/dev/null || true)"
    if printf '%s' "$SERVED" | grep -q "DNS:\*\.$ZONE"; then
        HS_OK=1
        break
    fi
    sleep 1
done
if [ "$HS_OK" -ne 1 ]; then
    echo "alt_sni_handshake_failed name=$ALT port=$ALT_PORT" >&2
    exit 5
fi
echo "alt_sni_handshake=ok name=$ALT port=$ALT_PORT"

# 4. Prove a real REALITY client works through the alt name end to end.
WORK="$(mktemp -d)"
cleanup() { rm -rf -- "$WORK" /tmp/alt-sni-plan.json; }
# The restore EXIT trap stays armed through the proof and the marker write:
# a failure here must roll the lane back, otherwise the apply is left
# half-done and invisible to the drift gate (configs changed, no marker).
python3 - /tmp/alt-sni-plan.json "$WORK/client.json" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1]))
out = sys.argv[2]
# The proof client speaks raw-tcp Vision only; never point it at an
# XHTTP REALITY inbound even when that one carries the fallback front,
# or a healthy apply would be rolled back on a false negative.
tcp_targets = [t for t in plan['targets'] if t.get('network', 'tcp') == 'tcp']
chosen = next((t for t in tcp_targets if t['front']), tcp_targets[0] if tcp_targets else None)
if chosen is None:
    print('ALT_SNI_NO_VISION_TARGET', file=sys.stderr)
    raise SystemExit(7)
cfg = {
    "log": {"loglevel": "warning"},
    "inbounds": [{"tag": "socks", "listen": "127.0.0.1", "port": 28191, "protocol": "socks",
                  "settings": {"udp": False}}],
    "outbounds": [{
        "tag": "proxy", "protocol": "vless",
        "settings": {"vnext": [{"address": "127.0.0.1", "port": int(chosen['front'] or chosen['port']),
                                "users": [{"id": chosen['uuid'], "flow": "xtls-rprx-vision",
                                           "encryption": "none"}]}]},
        "streamSettings": {"network": "tcp", "security": "reality",
                           "realitySettings": {"serverName": plan['alt'], "fingerprint": "chrome",
                                               "publicKey": chosen['pbk'], "shortId": chosen['sid'],
                                               "spiderX": "/"}}}],
    "routing": {"rules": [{"type": "field", "inboundTag": ["socks"], "outboundTag": "proxy"}]},
}
json.dump(cfg, open(out, 'w', encoding='utf-8'))
PY
"$XRAY_BIN" run -config "$WORK/client.json" > "$WORK/client.log" 2>&1 &
CLIENT_PID=$!
for _ in $(seq 1 20); do
    ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx '127.0.0.1:28191' && break
    sleep 0.5
done
EGRESS="$(curl -s -m 20 -x socks5h://127.0.0.1:28191 https://api.ipify.org || true)"
kill "$CLIENT_PID" 2>/dev/null || true
wait "$CLIENT_PID" 2>/dev/null || true
if [ -z "$EGRESS" ]; then
    echo 'alt_sni_lane=dead' >&2
    exit 6
fi
echo "alt_sni_lane=ok egress=$EGRESS"

# The drift gate keys off this marker: it is what turns "the lane was never
# applied here" into "the lane regressed here".
printf '%s\n' "$ALT" > "$MARKER"
chmod 644 "$MARKER"
# Marker written: the apply is committed. Best-effort teardown of the proof
# client's scratch dir; a teardown failure must not flip the outcome, and the
# restore trap stays armed with rc=0 so it never acts on success.
cleanup || true
echo "APPLY_BACKUP_DIR=$BACKUP"
echo 'ALT_SNI_APPLIED'
'@.Replace("__ALT_NAME__", $AltName).Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "alt-sni"
