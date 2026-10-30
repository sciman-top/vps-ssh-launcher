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
# handshake from the host itself (loopback + the same binaries the host already
# ships) instead of trusting "service active" or a config hash. Every lane is
# proven by fetching a 204 from gstatic through the tunnel; the CLI verdict is
# ACCEPTANCE_RESULT=PASS|FAIL so it is greppable in a receipt. No configuration
# is written: the transient client configs live under a private temp dir and
# are removed on exit.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
OBFS_PORT="__OBFS_PORT__"
OBFS_PASSWORD="__OBFS_PASSWORD__"
TIMEOUT="__TIMEOUT__"
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
record() {
    local lane="$1" rc="$2" detail="$3"
    case "$rc" in
        0)   echo "LANE=$lane RESULT=PASS $detail" ;;
        none) echo "LANE=$lane RESULT=N/A $detail" ;;
        *)   echo "LANE=$lane RESULT=FAIL $detail"; fail=1 ;;
    esac
}

probe_via_socks() {
    # $1 = local socks port. Returns curl exit; prints the http code.
    curl -sS -m "$TIMEOUT" -o /dev/null -w '%{http_code}' \
        --socks5-hostname "127.0.0.1:$1" https://www.gstatic.com/generate_204 2>/dev/null || true
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
        pk = reality.get('private_key')
        if not users or not sids:
            continue
        # sing-box source carries only the private key; the subscription uses a
        # derived public key, so the handshake probe relies on the public key
        # read from the generated subscription instead (see below).
        print('\t'.join([
            str(ib.get('listen_port')), users[0].get('uuid', ''),
            ((ib.get('tls') or {}).get('server_name') or ''), sids[0],
            users[0].get('flow', ''),
        ]))
        raise SystemExit(0)
raise SystemExit(3)
PY
}

sb_sub_public_key() {
    # The subscription is the only place the REALITY public key is stored.
    python3 - "$SB_SOURCE_DIR" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
candidates = glob.glob(os.path.join(root, '..', '..', '..', 'subscribe', 'sing-box', '*'))
for path in sorted(candidates):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ob in data.get('outbounds', []) or []:
        reality = ((ob.get('tls') or {}).get('reality')) or {}
        pk = reality.get('public_key')
        if pk:
            print(pk)
            raise SystemExit(0)
raise SystemExit(3)
PY
}

hy2_facts() {
    python3 - "$SB_SOURCE_DIR" "$OBFS_PORT" <<'PY'
import glob, json, os, sys
root, want_obfs = sys.argv[1], sys.argv[2]
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
        if obfs_type and str(ib.get('listen_port')) == want_obfs:
            obfs = line
        elif not obfs_type and not base:
            base = line
print('BASE\t' + base)
print('OBFS\t' + obfs)
PY
}

# --- REALITY (each core probed independently when present) ---
reality_sources=0
if [ -x "$XRAY_BINARY" ] && [ -d "$XRAY_CONF" ]; then
    if facts="$(xray_reality_facts 2>/dev/null)" && [ -n "$facts" ]; then
        IFS=$'\t' read -r rport ruuid rsni rpk rsid rflow <<<"$facts"
        cat >"$WORK/x.json" <<JSON
{"inbounds":[{"listen":"127.0.0.1","port":19101,"protocol":"socks","settings":{"udp":true}}],"outbounds":[{"protocol":"vless","settings":{"vnext":[{"address":"127.0.0.1","port":$rport,"users":[{"id":"$ruuid","flow":"$rflow","encryption":"none"}]}]},"streamSettings":{"network":"tcp","security":"reality","realitySettings":{"serverName":"$rsni","fingerprint":"chrome","publicKey":"$rpk","shortId":"$rsid"}}}]}
JSON
        "$XRAY_BINARY" run -c "$WORK/x.json" >"$WORK/x.log" 2>&1 &
        PIDS+=("$!")
        sleep 3
        code="$(probe_via_socks 19101)"
        if [ "$code" = '204' ]; then record reality-xray 0 "port=$rport sid=$rsid"; else record reality-xray 1 "port=$rport code=${code:-none}"; fi
        reality_sources=1
    fi
fi
if [ -x "$SB_BINARY" ] && [ -d "$SB_SOURCE_DIR" ]; then
    if facts="$(sb_reality_facts 2>/dev/null)" && [ -n "$facts" ] && pubkey="$(sb_sub_public_key 2>/dev/null)"; then
        IFS=$'\t' read -r rport ruuid rsni rsid rflow <<<"$facts"
        cat >"$WORK/sb.json" <<JSON
{"log":{"level":"error"},"inbounds":[{"type":"socks","listen":"127.0.0.1","listen_port":19102}],"outbounds":[{"type":"vless","server":"127.0.0.1","server_port":$rport,"uuid":"$ruuid","flow":"$rflow","tls":{"enabled":true,"server_name":"$rsni","utls":{"enabled":true,"fingerprint":"chrome"},"reality":{"enabled":true,"public_key":"$pubkey","short_id":"$rsid"}}}]}
JSON
        "$SB_BINARY" run -c "$WORK/sb.json" >"$WORK/sb.log" 2>&1 &
        PIDS+=("$!")
        sleep 3
        code="$(probe_via_socks 19102)"
        if [ "$code" = '204' ]; then record reality-singbox 0 "port=$rport sid=$rsid"; else record reality-singbox 1 "port=$rport code=${code:-none}"; fi
        reality_sources=1
    fi
fi
if [ "$reality_sources" -eq 0 ]; then record reality none "no usable reality source"; fi

# --- Hysteria2 base + obfs (sing-box hosts only) ---
if [ -x "$SB_BINARY" ] && [ -d "$SB_SOURCE_DIR" ]; then
    hy2="$(hy2_facts 2>/dev/null || true)"
    base_line="$(printf '%s\n' "$hy2" | sed -n 's/^BASE\t//p')"
    obfs_line="$(printf '%s\n' "$hy2" | sed -n 's/^OBFS\t//p')"
    if [ -n "$base_line" ]; then
        IFS=$'\t' read -r bport bpass bsni _ _ <<<"$base_line"
        cat >"$WORK/hb.json" <<JSON
{"log":{"level":"error"},"inbounds":[{"type":"socks","listen":"127.0.0.1","listen_port":19103}],"outbounds":[{"type":"hysteria2","server":"127.0.0.1","server_port":$bport,"password":"$bpass","tls":{"enabled":true,"server_name":"$bsni","insecure":true,"alpn":["h3"]},"up_mbps":100,"down_mbps":50}]}
JSON
        "$SB_BINARY" run -c "$WORK/hb.json" >"$WORK/hb.log" 2>&1 &
        PIDS+=("$!")
        sleep 4
        code="$(probe_via_socks 19103)"
        if [ "$code" = '204' ]; then record hysteria2-base 0 "port=$bport"; else record hysteria2-base 1 "port=$bport code=${code:-none}"; fi
    else
        record hysteria2-base none "no base hysteria2 inbound"
    fi
    if [ -n "$obfs_line" ]; then
        IFS=$'\t' read -r oport opass osni otype osalt <<<"$obfs_line"
        cat >"$WORK/ho.json" <<JSON
{"log":{"level":"error"},"inbounds":[{"type":"socks","listen":"127.0.0.1","listen_port":19104}],"outbounds":[{"type":"hysteria2","server":"127.0.0.1","server_port":$oport,"password":"$opass","obfs":{"type":"$otype","password":"$osalt"},"tls":{"enabled":true,"server_name":"$osni","insecure":true,"alpn":["h3"]},"up_mbps":100,"down_mbps":50}]}
JSON
        "$SB_BINARY" run -c "$WORK/ho.json" >"$WORK/ho.log" 2>&1 &
        PIDS+=("$!")
        sleep 4
        code="$(probe_via_socks 19104)"
        if [ "$code" = '204' ]; then record hysteria2-obfs 0 "port=$oport obfs=$otype"; else record hysteria2-obfs 1 "port=$oport code=${code:-none}"; fi
    else
        record hysteria2-obfs none "obfs lane not configured"
    fi
fi

if [ "$fail" -eq 0 ]; then
    echo 'ACCEPTANCE_RESULT=PASS'
    exit 0
fi
echo 'ACCEPTANCE_RESULT=FAIL'
exit 1
'@.Replace("__OBFS_PORT__", "$ObfsPort").Replace("__OBFS_PASSWORD__", $ObfsPassword).Replace("__TIMEOUT__", "$TimeoutSeconds")

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "proxy-acceptance"
