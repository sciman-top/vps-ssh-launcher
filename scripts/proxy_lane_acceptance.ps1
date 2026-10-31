#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$ObfsPort = 8444,
  [string]$ObfsPassword,
  [string]$PublicName,
  [Alias("XrayPublicPort")]
  [int]$RealityPublicPort,
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
if ($RealityPublicPort -lt 0 -or $RealityPublicPort -gt 65535) {
  throw "-RealityPublicPort must be between 1 and 65535 when supplied."
}
if ($PublicName -and $PublicName -notmatch '^[A-Za-z0-9._:\[\]-]+$') {
  throw "-PublicName must be a hostname or IP literal without shell syntax."
}
if ($PublicName -and $RealityPublicPort -eq 0) {
  throw "-RealityPublicPort is required with -PublicName so the public listener is explicit."
}

# Read-only controlled acceptance for the proxy lanes. Runs a real client
# handshake from the host itself against its active listeners, instead of
# trusting "service active", a config hash, or a stale subscription. Every
# probed lane fetches a 204 through the tunnel. Supplying -PublicName also
# exercises each lane through its public DNS name and public port instead of
# only the loopback inbound. The verdict is PASS, PARTIAL
# when a lane applicable to this host was omitted or unreadable, or FAIL; it is
# greppable in a receipt. A missing optional lane is N/A and does not set
# PARTIAL. No configuration is written.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
OBFS_PORT="__OBFS_PORT__"
TIMEOUT="__TIMEOUT__"
OBFS_PASSWORD_FILE="__OBFS_PASSWORD_FILE__"
PUBLIC_NAME="__PUBLIC_NAME__"
REALITY_PUBLIC_PORT="__REALITY_PUBLIC_PORT__"
XRAY_BINARY="${VPS_ACCEPT_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
XRAY_CONF="${VPS_ACCEPT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_BINARY="${VPS_ACCEPT_SB_BINARY:-/etc/v2ray-agent/sing-box/sing-box}"
SB_SOURCE_DIR="${VPS_ACCEPT_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_ROOT="${VPS_ACCEPT_NGINX_ROOT:-/usr/share/nginx/html}"
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
invalid = False
found = None
for path in sorted(glob.glob(os.path.join(conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
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
        if found is None:
            found = '\t'.join([
                str(ib.get('port')), clients[0].get('id', ''),
                names[0], rs.get('publicKey', ''), sids[0],
                clients[0].get('flow', ''),
            ])
if invalid:
    raise SystemExit(4)
if found:
    print(found)
    raise SystemExit(0)
raise SystemExit(3)
PY
}

sb_reality_facts() {
    python3 - "$SB_SOURCE_DIR" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
invalid = False
found = None
for path in sorted(glob.glob(os.path.join(root, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        reality = ((ib.get('tls') or {}).get('reality')) or {}
        if not reality or not reality.get('enabled'):
            continue
        users = ib.get('users') or []
        sids = [s for s in (reality.get('short_id') or []) if s]
        if not users or not sids or not reality.get('private_key'):
            continue
        if found is None:
            found = '\t'.join([
                str(ib.get('listen_port')), users[0].get('uuid', ''),
                ((ib.get('tls') or {}).get('server_name') or ''), sids[0],
                users[0].get('flow', ''), reality['private_key'],
            ])
if invalid:
    raise SystemExit(4)
if found:
    print(found)
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
invalid = False
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
                invalid = True
                continue
            for ib in data.get('inbounds', []) or []:
                obfs = ib.get('obfs') or {}
                if (
                    ib.get('type') == 'hysteria2'
                    and obfs.get('type') == 'salamander'
                    and obfs.get('password') == wanted_password
                ):
                    expected_obfs = str(ib.get('listen_port'))
base = obfs = ''
for path in sorted(glob.glob(os.path.join(root, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
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
if invalid:
    raise SystemExit(4)
if password_file and wanted_password and expected_obfs != want_obfs:
    raise SystemExit(3)
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

# xhttp_facts enumerates every XHTTP inbound that a client can actually reach.
# The TLS lane in this fleet listens on loopback and is published through a
# dokodemo-door wrapper, so the wrapper port is what a subscription dials; an
# inbound with no public wrapper is not a lane and is skipped instead of being
# probed at an address no client uses.
xhttp_facts() {
    python3 - "$XRAY_CONF" <<'PY'
import glob, json, os, sys

conf = sys.argv[1]
inbounds = []
invalid = False
for path in sorted(glob.glob(os.path.join(conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        inbounds.append(ib)


def loopback(value):
    return str(value or '') in ('127.0.0.1', '::1', 'localhost')


def public_port(ib):
    port = ib.get('port')
    if not isinstance(port, int):
        return None
    if not loopback(ib.get('listen')):
        return port
    for other in inbounds:
        if other.get('protocol') != 'dokodemo-door' or loopback(other.get('listen')):
            continue
        settings = other.get('settings') or {}
        if str(settings.get('address')) not in ('127.0.0.1', 'localhost'):
            continue
        if settings.get('port') == port:
            return other.get('port')
    return None


lines = []
for ib in inbounds:
    stream = ib.get('streamSettings') or {}
    if stream.get('network') != 'xhttp':
        continue
    clients = ((ib.get('settings') or {}).get('clients') or [])
    if not clients:
        continue
    connect = public_port(ib)
    if not connect:
        continue
    security = stream.get('security') or 'none'
    xhttp = stream.get('xhttpSettings') or {}
    reality = stream.get('realitySettings') or {}
    tls = stream.get('tlsSettings') or {}
    public_key = short_id = ''
    if security == 'reality':
        names = reality.get('serverNames') or []
        ids = [sid for sid in (reality.get('shortIds') or []) if sid]
        if not names or not ids or not reality.get('publicKey'):
            invalid = True
            continue
        sni, public_key, short_id = names[0], reality['publicKey'], ids[0]
    elif security == 'tls':
        sni = tls.get('serverName') or xhttp.get('host') or ''
        if not sni:
            invalid = True
            continue
    else:
        continue
    # Unit separator, not a tab: the TLS lane leaves publicKey/shortId empty
    # and `read` with a whitespace IFS collapses those empty fields, shifting
    # every later column into the wrong variable.
    lines.append('\x1f'.join([
        security, str(connect), clients[0].get('id', ''), sni, public_key,
        short_id, xhttp.get('path') or '/', xhttp.get('host') or sni,
    ]))
if invalid:
    raise SystemExit(4)
if not lines:
    raise SystemExit(3)
print('\n'.join(lines))
PY
}

# --- REALITY (each core probed independently when present) ---
reality_sources=0
if [ ! -e "$XRAY_BINARY" ] && [ ! -L "$XRAY_BINARY" ]; then
    # A dormant conf directory on a sing-box-only host is not an Xray lane.
    :
elif [ ! -x "$XRAY_BINARY" ]; then
    record reality-xray 1 "binary_or_source_unusable"
    reality_sources=1
elif [ ! -d "$XRAY_CONF" ]; then
    record reality-xray 1 "binary_or_source_unusable"
    reality_sources=1
else
    facts=''
    facts_rc=0
    facts="$(xray_reality_facts 2>/dev/null)" || facts_rc=$?
    if [ "$facts_rc" -eq 0 ] && [ -n "$facts" ]; then
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
        # Unauthenticated probes are what an active prober sends. REALITY must
        # relay them to the local decoy vhost, so a 200 carrying the decoy page
        # is the evidence that the camouflage half of the lane still works.
        decoy_sha="$(sha256sum "$NGINX_ROOT/index.html" 2>/dev/null | awk '{print $1}')"
        fallback_code="$(curl -sk -m "$TIMEOUT" -o "$WORK/fallback.html" -w '%{http_code}' \
            --resolve "${rsni}:${rport}:127.0.0.1" "https://${rsni}:${rport}/" 2>/dev/null || true)"
        got_sha="$(sha256sum "$WORK/fallback.html" 2>/dev/null | awk '{print $1}')"
        if [ -z "$decoy_sha" ]; then
            record reality-fallback none "no decoy root index"
        elif [ "$fallback_code" = '200' ] && [ "$got_sha" = "$decoy_sha" ]; then
            record reality-fallback 0 "port=$rport page=decoy"
        else
            record reality-fallback 1 "port=$rport code=${fallback_code:-none} decoy_match=no"
        fi
        if [ -n "$PUBLIC_NAME" ]; then
            public_socks_port="$(next_local_port)"
            python3 - "$WORK/x-public.json" "$public_socks_port" "$REALITY_PUBLIC_PORT" "$ruuid" "$rflow" "$rsni" "$rpk" "$rsid" "$PUBLIC_NAME" <<'PY'
import json
import sys

out, socks, port, uuid, flow, sni, public_key, short_id, address = sys.argv[1:]
data = {
    "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": address, "port": int(port), "users": [{"id": uuid, "flow": flow, "encryption": "none"}]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": sni, "fingerprint": "chrome", "publicKey": public_key, "shortId": short_id}},
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$XRAY_BINARY" run -c "$WORK/x-public.json" >"$WORK/x-public.log" 2>&1 &
            PIDS+=("$!")
            sleep 3
            code="$(probe_via_socks "$public_socks_port")"
            if [ "$code" = '204' ]; then record reality-xray-public 0 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT"; else record reality-xray-public 1 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT code=${code:-none}"; fi
        fi
        reality_sources=1
    else
        if [ "$facts_rc" -eq 4 ]; then
            record reality-xray 1 "source_invalid"
        else
            record reality-xray 1 "source_unreadable"
        fi
        reality_sources=1
    fi
fi
# --- XHTTP (VMess-style fallback transports: REALITY and TLS variants) ---
if [ -x "$XRAY_BINARY" ] && [ -d "$XRAY_CONF" ]; then
    xhttp_lines=''
    xhttp_rc=0
    xhttp_lines="$(xhttp_facts 2>/dev/null)" || xhttp_rc=$?
    if [ "$xhttp_rc" -eq 3 ]; then
        record xhttp none "no publicly reachable xhttp inbound"
    elif [ "$xhttp_rc" -ne 0 ]; then
        record xhttp 1 "source_unreadable"
    else
        while IFS=$'\x1f' read -r xkind xport xuuid xsni xpk xsid xpath xhost; do
            [ -n "$xkind" ] || continue
            x_socks="$(next_local_port)"
            python3 - "$WORK/xhttp-$xkind.json" "$x_socks" "$xport" "$xuuid" "$xsni" "$xpk" "$xsid" "$xpath" "$xhost" "$xkind" <<'PY'
import json
import sys

out, socks, port, uuid, sni, public_key, short_id, path, host, kind = sys.argv[1:]
stream = {
    "network": "xhttp",
    "xhttpSettings": {"host": host, "path": path, "mode": "auto"},
}
if kind == 'reality':
    stream["security"] = "reality"
    stream["realitySettings"] = {
        "serverName": sni, "fingerprint": "chrome",
        "publicKey": public_key, "shortId": short_id,
    }
else:
    stream["security"] = "tls"
    stream["tlsSettings"] = {
        "serverName": sni, "fingerprint": "chrome", "alpn": ["h2"],
    }
data = {
    "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": "127.0.0.1", "port": int(port), "users": [{"id": uuid, "encryption": "none"}]}]},
        "streamSettings": stream,
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$XRAY_BINARY" run -c "$WORK/xhttp-$xkind.json" >"$WORK/xhttp-$xkind.log" 2>&1 &
            PIDS+=("$!")
            sleep 3
            code="$(probe_via_socks "$x_socks")"
            if [ "$code" = '204' ]; then
                record "xhttp-$xkind" 0 "port=$xport path=$xpath"
            else
                record "xhttp-$xkind" 1 "port=$xport code=${code:-none}"
            fi
        done <<<"$xhttp_lines"
    fi
fi
if [ ! -e "$SB_BINARY" ] && [ ! -L "$SB_BINARY" ]; then
    # Hysteria2 is optional on an Xray-only host.  A completely absent
    # sing-box installation is N/A; a present-but-unusable installation is a
    # real failure and must not be hidden as an omitted lane.
    record hysteria2-base none "sing-box lane not present"
    record hysteria2-obfs none "sing-box lane not present"
elif [ ! -x "$SB_BINARY" ]; then
    record hysteria2-base 1 "binary_unusable"
    record hysteria2-obfs 1 "binary_unusable"
elif [ ! -d "$SB_SOURCE_DIR" ]; then
    record hysteria2-base 1 "source_unreadable"
    record hysteria2-obfs 1 "source_unreadable"
else
    # On a bwg dual-core host sing-box runs only addon lanes (Hysteria2); its
    # REALITY lives in Xray. Probe sing-box REALITY only when a fragment
    # actually carries one, so an absent addon REALITY is not a FAIL.
    facts=''
    facts_rc=0
    facts="$(sb_reality_facts 2>/dev/null)" || facts_rc=$?
    if [ "$facts_rc" -eq 0 ] && [ -n "$facts" ]; then
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
            if [ -n "$PUBLIC_NAME" ]; then
                sb_public_socks_port="$(next_local_port)"
                python3 - "$WORK/sb-public.json" "$sb_public_socks_port" "$REALITY_PUBLIC_PORT" "$ruuid" "$rflow" "$rsni" "$pubkey" "$rsid" "$PUBLIC_NAME" <<'PY'
import json
import sys

out, socks, port, uuid, flow, sni, public_key, short_id, address = sys.argv[1:]
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{
        "type": "vless", "server": address, "server_port": int(port), "uuid": uuid, "flow": flow,
        "tls": {"enabled": True, "server_name": sni, "utls": {"enabled": True, "fingerprint": "chrome"}, "reality": {"enabled": True, "public_key": public_key, "short_id": short_id}},
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                "$SB_BINARY" run -c "$WORK/sb-public.json" >"$WORK/sb-public.log" 2>&1 &
                PIDS+=("$!")
                sleep 3
                code="$(probe_via_socks "$sb_public_socks_port")"
                if [ "$code" = '204' ]; then record reality-singbox-public 0 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT"; else record reality-singbox-public 1 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT code=${code:-none}"; fi
            fi
        fi
    elif [ "$facts_rc" -eq 4 ]; then
        record reality-singbox 1 "source_invalid"
        reality_sources=1
    fi
fi
if [ "$reality_sources" -eq 0 ]; then record reality none "no usable reality source"; fi

# --- Hysteria2 base + obfs (sing-box hosts only) ---
if [ ! -e "$SB_BINARY" ] && [ ! -L "$SB_BINARY" ]; then
    # The optional sing-box lane is already recorded as N/A above.
    hy2_facts_ok=0
    hy2=""
elif [ ! -x "$SB_BINARY" ] || [ ! -d "$SB_SOURCE_DIR" ]; then
    # The present-but-unusable cases are already recorded as FAIL above.
    hy2_facts_ok=0
    hy2=""
else
    hy2_password_check_failed=0
    if hy2="$(hy2_facts 2>/dev/null)"; then
        hy2_facts_ok=1
    else
        hy2_rc=$?
        if [ "$hy2_rc" -eq 3 ]; then
            hy2_facts_ok=1
            hy2_password_check_failed=1
            hy2="$(OBFS_PASSWORD_FILE= hy2_facts 2>/dev/null || true)"
        else
            hy2_facts_ok=0
            record hysteria2-base 1 "source_invalid"
            record hysteria2-obfs 1 "source_invalid"
        fi
    fi
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
        if [ -n "$PUBLIC_NAME" ]; then
            hy2_base_public_socks_port="$(next_local_port)"
            python3 - "$WORK/hb-public.json" "$hy2_base_public_socks_port" "$bport" "$bpass" "$bsni" "$PUBLIC_NAME" <<'PY'
import json
import sys

out, socks, port, password, sni, address = sys.argv[1:]
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": address, "server_port": int(port), "password": password, "tls": {"enabled": True, "server_name": sni, "insecure": True, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$SB_BINARY" run -c "$WORK/hb-public.json" >"$WORK/hb-public.log" 2>&1 &
            PIDS+=("$!")
            sleep 4
            code="$(probe_via_socks "$hy2_base_public_socks_port")"
            if [ "$code" = '204' ]; then record hysteria2-base-public 0 "name=$PUBLIC_NAME port=$bport"; else record hysteria2-base-public 1 "name=$PUBLIC_NAME port=$bport code=${code:-none}"; fi
        fi
    else
        record hysteria2-base none "no base hysteria2 inbound"
    fi
    if [ "$hy2_password_check_failed" -eq 1 ]; then
        record hysteria2-obfs 1 "password_check_failed"
    fi
    if [ -n "$obfs_line" ]; then
        IFS=$'\t' read -r oport opass osni otype osalt <<<"$obfs_line"
        if [ "$hy2_password_check_failed" -eq 1 ]; then
            :
        elif [ -n "$OBFS_PASSWORD_FILE" ] && { [ "$oport" != "$OBFS_PORT" ] || [ "$otype" != 'salamander' ]; }; then
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
            if [ -n "$PUBLIC_NAME" ]; then
                hy2_obfs_public_socks_port="$(next_local_port)"
                python3 - "$WORK/ho-public.json" "$hy2_obfs_public_socks_port" "$oport" "$opass" "$osni" "$otype" "$osalt" "$PUBLIC_NAME" <<'PY'
import json
import sys

out, socks, port, password, sni, obfs_type, obfs_password, address = sys.argv[1:]
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": address, "server_port": int(port), "password": password, "obfs": {"type": obfs_type, "password": obfs_password}, "tls": {"enabled": True, "server_name": sni, "insecure": True, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                "$SB_BINARY" run -c "$WORK/ho-public.json" >"$WORK/ho-public.log" 2>&1 &
                PIDS+=("$!")
                sleep 4
                code="$(probe_via_socks "$hy2_obfs_public_socks_port")"
                if [ "$code" = '204' ]; then record hysteria2-obfs-public 0 "name=$PUBLIC_NAME port=$oport obfs=$otype"; else record hysteria2-obfs-public 1 "name=$PUBLIC_NAME port=$oport code=${code:-none}"; fi
            fi
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
'@.Replace("__OBFS_PORT__", "$ObfsPort").Replace("__PUBLIC_NAME__", $PublicName).Replace("__REALITY_PUBLIC_PORT__", "$RealityPublicPort").Replace("__TIMEOUT__", "$TimeoutSeconds")

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
