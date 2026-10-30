#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$ObfsPort = 8444,
  [string]$ObfsPassword,
  [int]$TimeoutSeconds = 12
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
if ($TimeoutSeconds -lt 3 -or $TimeoutSeconds -gt 60) {
  throw "-TimeoutSeconds must be between 3 and 60."
}

# Read-only controlled acceptance for the proxy lanes. Runs a real client
# handshake from the host itself against its active listeners, instead of
# trusting "service active", a config hash, or a stale subscription. Every
# probed lane fetches a 204 through the tunnel. The verdict is PASS, PARTIAL
# when a lane applicable to this host was omitted or unreadable, or FAIL; it is
# greppable in a receipt. A missing optional lane is N/A and does not set
# PARTIAL. No configuration is written.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
OBFS_PORT="__OBFS_PORT__"
TIMEOUT="__TIMEOUT__"
OBFS_PASSWORD_FILE="__OBFS_PASSWORD_FILE__"
XRAY_BINARY="${VPS_ACCEPT_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
XRAY_CONF="${VPS_ACCEPT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_BINARY="${VPS_ACCEPT_SB_BINARY:-/etc/v2ray-agent/sing-box/sing-box}"
SB_SOURCE_DIR="${VPS_ACCEPT_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
WORK="$(mktemp -d)"
chmod 700 "$WORK"
PIDS=()

cleanup() {
    trap - EXIT INT TERM
    for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
    rm -rf -- "$WORK"
}
trap cleanup EXIT INT TERM

fail=0
na=0
record() {
    local lane="$1" rc="$2" detail="$3"
    case "$rc" in
        0)   echo "LANE=$lane RESULT=PASS $detail" ;;
        none) echo "LANE=$lane RESULT=N/A $detail"; na=1 ;;
        *)   echo "LANE=$lane RESULT=FAIL $detail"; fail=1 ;;
    esac
}

probe_via_socks() {
    # $1 = local socks port. Returns curl exit; prints the http code.
    curl -sS -m "$TIMEOUT" -o /dev/null -w '%{http_code}' \
        --socks5-hostname "127.0.0.1:$1" https://www.gstatic.com/generate_204 2>/dev/null || true
}

next_local_port() {
    # Do not reserve a fleet-wide fixed port: an unrelated local process (or a
    # previous interrupted acceptance run) must not turn a healthy lane into a
    # false failure.  The client is started immediately after allocation and
    # cleanup kills every client PID on exit.
    python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

# extract_uuid prints the first client id from whichever source the host uses.
xray_reality_facts() {
    python3 - "$XRAY_CONF" <<'PY'
import glob, json, os, sys
conf = sys.argv[1]
for path in sorted(glob.glob(os.path.join(conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        clients = ((ib.get('settings') or {}).get('clients') or [])
        if not clients:
            continue
        names = rs.get('serverNames') or []
        sids = [s for s in (rs.get('shortIds') or []) if s]
        if not names or not sids:
            continue
        print('\t'.join([
            str(ib.get('port')), clients[0].get('id', ''),
            names[0], rs.get('publicKey', ''), sids[0],
            clients[0].get('flow', ''),
        ]))
        raise SystemExit(0)
raise SystemExit(3)
PY
}

sb_reality_facts() {
    python3 - "$SB_SOURCE_DIR" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
for path in sorted(glob.glob(os.path.join(root, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        reality = ((ib.get('tls') or {}).get('reality')) or {}
        if not reality or not reality.get('enabled'):
            continue
        users = ib.get('users') or []
        sids = [s for s in (reality.get('short_id') or []) if s]
        if not users or not sids or not reality.get('private_key'):
            continue
        print('\t'.join([
            str(ib.get('listen_port')), users[0].get('uuid', ''),
            ((ib.get('tls') or {}).get('server_name') or ''), sids[0],
            users[0].get('flow', ''), reality['private_key'],
        ]))
        raise SystemExit(0)
raise SystemExit(3)
PY
}

hy2_facts() {
    python3 - "$SB_SOURCE_DIR" "$OBFS_PORT" "$OBFS_PASSWORD_FILE" <<'PY'
import glob, json, os, sys
root, want_obfs, password_file = sys.argv[1:4]
expected_obfs = ''
wanted_password = ''
if password_file:
    try:
        with open(password_file, encoding='utf-8') as handle:
            wanted_password = handle.read().strip()
    except OSError:
        raise SystemExit(3)
    if wanted_password:
        for path in sorted(glob.glob(os.path.join(root, '*.json'))):
            try:
                data = json.load(open(path, encoding='utf-8'))
            except Exception:
                continue
            for ib in data.get('inbounds', []) or []:
                obfs = ib.get('obfs') or {}
                if (
                    ib.get('type') == 'hysteria2'
                    and obfs.get('type') == 'salamander'
                    and obfs.get('password') == wanted_password
                ):
                    expected_obfs = str(ib.get('listen_port'))
        if expected_obfs != want_obfs:
            raise SystemExit(3)
base = obfs = ''
for path in sorted(glob.glob(os.path.join(root, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        if ib.get('type') != 'hysteria2':
            continue
        users = ib.get('users') or []
        if not users:
            continue
        tls = ib.get('tls') or {}
        obfs_type = (ib.get('obfs') or {}).get('type', '')
        line = '\t'.join([
            str(ib.get('listen_port')), users[0].get('password', ''),
            tls.get('server_name', ''), obfs_type,
            (ib.get('obfs') or {}).get('password', ''),
        ])
        if obfs_type:
            obfs = line
        elif not obfs_type and not base:
            base = line
print('BASE\t' + base)
print('OBFS\t' + obfs)
PY
}

derive_reality_public_key() {
    # sing-box keeps only the X25519 private key in its source fragment. Do the
    # RFC 7748 Montgomery ladder locally so this probe does not depend on a
    # subscription file or an unavailable sing-box generate subcommand.
    python3 - "$1" <<'PY'
import base64
import sys

p = 2**255 - 19
a24 = 121665
encoded = sys.argv[1]
raw = base64.urlsafe_b64decode(encoded + '=' * ((4 - len(encoded) % 4) % 4))
if len(raw) != 32:
    raise SystemExit(2)

scalar = bytearray(raw)
scalar[0] &= 248
scalar[31] &= 127
scalar[31] |= 64
x1, x2, z2, x3, z3, swap = 9, 1, 0, 9, 1, 0
for bit in range(254, -1, -1):
    bit_value = (scalar[bit // 8] >> (bit & 7)) & 1
    swap ^= bit_value
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    swap = bit_value
    a = (x2 + z2) % p
    aa = a * a % p
    b = (x2 - z2) % p
    bb = b * b % p
    e = (aa - bb) % p
    c = (x3 + z3) % p
    d = (x3 - z3) % p
    da = d * a % p
    cb = c * b % p
    x3 = (da + cb) ** 2 % p
    z3 = x1 * (da - cb) ** 2 % p
    x2 = aa * bb % p
    z2 = e * (aa + a24 * e) % p
if swap:
    x2, x3 = x3, x2
    z2, z3 = z3, z2
public_key = (x2 * pow(z2, p - 2, p) % p).to_bytes(32, 'little')
print(base64.urlsafe_b64encode(public_key).rstrip(b'=').decode())
PY
}

# --- REALITY (each core probed independently when present) ---
reality_sources=0
if [ -x "$XRAY_BINARY" ] && [ -d "$XRAY_CONF" ]; then
    if facts="$(xray_reality_facts 2>/dev/null)" && [ -n "$facts" ]; then
        IFS=$'\t' read -r rport ruuid rsni rpk rsid rflow <<<"$facts"
        x_socks_port="$(next_local_port)"
        python3 - "$WORK/x.json" "$x_socks_port" "$rport" "$ruuid" "$rflow" "$rsni" "$rpk" "$rsid" <<'PY'
import json
import sys

out, socks, port, uuid, flow, sni, public_key, short_id = sys.argv[1:]
data = {
        "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": "127.0.0.1", "port": int(port), "users": [{"id": uuid, "flow": flow, "encryption": "none"}]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": sni, "fingerprint": "chrome", "publicKey": public_key, "shortId": short_id}},
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
        "$XRAY_BINARY" run -c "$WORK/x.json" >"$WORK/x.log" 2>&1 &
        PIDS+=("$!")
        sleep 3
        code="$(probe_via_socks "$x_socks_port")"
        if [ "$code" = '204' ]; then record reality-xray 0 "port=$rport sid=$rsid"; else record reality-xray 1 "port=$rport code=${code:-none}"; fi
        reality_sources=1
    fi
fi
if [ ! -x "$SB_BINARY" ] || [ ! -d "$SB_SOURCE_DIR" ]; then
    # Hysteria2 is optional on an Xray-only host.  Absence is N/A, while an
    # unreadable source on a host that advertises sing-box is a real failure.
    record hysteria2-base none "sing-box lane not present"
    record hysteria2-obfs none "sing-box lane not present"
elif [ -x "$SB_BINARY" ] && [ -d "$SB_SOURCE_DIR" ]; then
    if facts="$(sb_reality_facts 2>/dev/null)" && [ -n "$facts" ]; then
        IFS=$'\t' read -r rport ruuid rsni rsid rflow private_key <<<"$facts"
        reality_sources=1
        pubkey="$(derive_reality_public_key "$private_key" 2>/dev/null || true)"
        if [ -z "$pubkey" ]; then
            echo "LANE=reality-singbox RESULT=FAIL reason=public_key_derivation port=$rport" >&2
            fail=1
        else
            unset private_key
            sb_socks_port="$(next_local_port)"
            python3 - "$WORK/sb.json" "$sb_socks_port" "$rport" "$ruuid" "$rflow" "$rsni" "$pubkey" "$rsid" <<'PY'
import json
import sys

out, socks, port, uuid, flow, sni, public_key, short_id = sys.argv[1:]
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{
        "type": "vless", "server": "127.0.0.1", "server_port": int(port), "uuid": uuid, "flow": flow,
        "tls": {"enabled": True, "server_name": sni, "utls": {"enabled": True, "fingerprint": "chrome"}, "reality": {"enabled": True, "public_key": public_key, "short_id": short_id}},
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$SB_BINARY" run -c "$WORK/sb.json" >"$WORK/sb.log" 2>&1 &
            PIDS+=("$!")
            sleep 3
            code="$(probe_via_socks "$sb_socks_port")"
            if [ "$code" = '204' ]; then record reality-singbox 0 "port=$rport sid=$rsid"; else record reality-singbox 1 "port=$rport code=${code:-none}"; fi
        fi
    fi
fi
if [ "$reality_sources" -eq 0 ]; then record reality none "no usable reality source"; fi

# --- Hysteria2 base + obfs (sing-box hosts only) ---
if [ -x "$SB_BINARY" ] && [ -d "$SB_SOURCE_DIR" ]; then
    if hy2="$(hy2_facts 2>/dev/null)"; then
        hy2_facts_ok=1
    else
        hy2_facts_ok=0
        hy2=""
    fi
else
    hy2_facts_ok=0
    hy2=""
fi
if [ "$hy2_facts_ok" -ne 1 ]; then
    if [ -n "$OBFS_PASSWORD_FILE" ]; then
        reason=obfs_password_mismatch
    else
        reason=source_unreadable
    fi
    echo "LANE=hysteria2-base RESULT=FAIL reason=$reason" >&2
    echo "LANE=hysteria2-obfs RESULT=FAIL reason=$reason" >&2
    fail=1
fi
if [ "$hy2_facts_ok" -eq 1 ]; then
    base_line="$(printf '%s\n' "$hy2" | sed -n 's/^BASE\t//p')"
    obfs_line="$(printf '%s\n' "$hy2" | sed -n 's/^OBFS\t//p')"
    if [ -n "$base_line" ]; then
        IFS=$'\t' read -r bport bpass bsni _ _ <<<"$base_line"
        hy2_base_socks_port="$(next_local_port)"
        python3 - "$WORK/hb.json" "$hy2_base_socks_port" "$bport" "$bpass" "$bsni" <<'PY'
import json
import sys

out, socks, port, password, sni = sys.argv[1:]
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": "127.0.0.1", "server_port": int(port), "password": password, "tls": {"enabled": True, "server_name": sni, "insecure": True, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
        "$SB_BINARY" run -c "$WORK/hb.json" >"$WORK/hb.log" 2>&1 &
        PIDS+=("$!")
        sleep 4
        code="$(probe_via_socks "$hy2_base_socks_port")"
        if [ "$code" = '204' ]; then record hysteria2-base 0 "port=$bport"; else record hysteria2-base 1 "port=$bport code=${code:-none}"; fi
    else
        record hysteria2-base none "no base hysteria2 inbound"
    fi
    if [ -n "$obfs_line" ]; then
        IFS=$'\t' read -r oport opass osni otype osalt <<<"$obfs_line"
        if [ -n "$OBFS_PASSWORD_FILE" ] && { [ "$oport" != "$OBFS_PORT" ] || [ "$otype" != 'salamander' ]; }; then
            echo "LANE=hysteria2-obfs RESULT=FAIL reason=obfs_contract port=$oport" >&2
            fail=1
        else
            hy2_obfs_socks_port="$(next_local_port)"
            python3 - "$WORK/ho.json" "$hy2_obfs_socks_port" "$oport" "$opass" "$osni" "$otype" "$osalt" <<'PY'
import json
import sys

out, socks, port, password, sni, obfs_type, obfs_password = sys.argv[1:]
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": "127.0.0.1", "server_port": int(port), "password": password, "obfs": {"type": obfs_type, "password": obfs_password}, "tls": {"enabled": True, "server_name": sni, "insecure": True, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$SB_BINARY" run -c "$WORK/ho.json" >"$WORK/ho.log" 2>&1 &
            PIDS+=("$!")
            sleep 4
            code="$(probe_via_socks "$hy2_obfs_socks_port")"
            if [ "$code" = '204' ]; then record hysteria2-obfs 0 "port=$oport obfs=$otype"; else record hysteria2-obfs 1 "port=$oport code=${code:-none}"; fi
        fi
    else
        record hysteria2-obfs none "obfs lane not configured"
    fi
fi

if [ "$fail" -eq 0 ]; then
    if [ "$na" -eq 0 ]; then
        echo 'ACCEPTANCE_RESULT=PASS'
    else
        echo 'ACCEPTANCE_RESULT=PARTIAL'
    fi
    exit 0
fi
echo 'ACCEPTANCE_RESULT=FAIL'
exit 1
'@.Replace("__OBFS_PORT__", "$ObfsPort").Replace("__TIMEOUT__", "$TimeoutSeconds")

$remotePasswordFile = ''
try {
  if ($ObfsPassword) {
    # Operator-assigned salt: validate against the deployed secret on the host
    # without putting it through PowerShell expansion or a raw bash payload.
    if ($ObfsPassword -notmatch '^[A-Za-z0-9._-]{1,64}$') {
      throw "-ObfsPassword must be 1..64 chars of [A-Za-z0-9._-]."
    }
    $remotePasswordFile = "/tmp/vps-ssh-launcher-obfs-password-$([guid]::NewGuid().ToString('N'))"
    $passwordPayload = [Convert]::ToBase64String(
      [Text.Encoding]::UTF8.GetBytes($ObfsPassword)
    )
    $uploadCommand = "umask 077; printf %s '$passwordPayload' | base64 -d > '$remotePasswordFile'"
    Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $uploadCommand -RemoteTempPrefix "proxy-password"
    $remoteCommand = $remoteCommand.Replace("__OBFS_PASSWORD_FILE__", $remotePasswordFile)
  }
  else {
    $remoteCommand = $remoteCommand.Replace("__OBFS_PASSWORD_FILE__", "")
  }
  Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "proxy-acceptance"
}
finally {
  if ($remotePasswordFile) {
    $cleanupCommand = "rm -f -- '$remotePasswordFile'"
    Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $cleanupCommand -RemoteTempPrefix "proxy-password-cleanup"
  }
}
