#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$ObfsPort = 8444,
  [int]$HopPort = 20001,
  [string]$ObfsPassword,
  [string]$ObfsPasswordFile,
  [string]$PublicName,
  [Alias("XrayPublicPort")]
  [int]$RealityPublicPort,
  # A freshly spawned Xray probe client has to load its geo data and complete
  # its first REALITY handshake before curl's own timer means anything. On a
  # loaded host that cold start regularly exceeds 12s: the 2026-11-09 zz run
  # failed a different 3-of-12 subset on two consecutive passes with
  # curl-exit-35, while the same server answered 30/30 handshakes and 3/3 manual
  # probes at the harness's exact timing, and the identical command passed 12/12
  # with -TimeoutSeconds 30. The default now covers the cold start instead of
  # encoding a fast-machine assumption; pass a smaller value to probe strictly.
  [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

if ($ObfsPassword -and $ObfsPasswordFile) {
  throw "-ObfsPassword and -ObfsPasswordFile are mutually exclusive."
}
if ($ObfsPasswordFile) {
  $ObfsPasswordFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ObfsPasswordFile)
  if (-not (Test-Path -LiteralPath $ObfsPasswordFile -PathType Leaf)) {
    throw "-ObfsPasswordFile not found: $ObfsPasswordFile"
  }
  $ObfsPassword = (Get-Content -LiteralPath $ObfsPasswordFile -Raw).Trim()
}

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
if ($HopPort -lt 20000 -or $HopPort -gt 20999) {
  throw "-HopPort must be inside the managed 20000..20999 range."
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
HOP_PORT="__HOP_PORT__"
PROFILE="__PROFILE__"
TIMEOUT="__TIMEOUT__"
OBFS_PASSWORD_FILE="__OBFS_PASSWORD_FILE__"
PUBLIC_NAME="__PUBLIC_NAME__"
REALITY_PUBLIC_PORT="__REALITY_PUBLIC_PORT__"
# Probe clients pin IPv4. zz is a NAT host with no global IPv6 address and no
# v6 default route, yet getaddrinfo still returns AAAA records for the public
# endpoints after the v4 set, so a freedom-style outbound using the default
# strategy could pick a destination that can never connect. The probe is not an
# IPv6 reachability test, so it pins IPv4 and a lane verdict stops depending on
# a host property unrelated to the proxy stack. (This is a robustness guard,
# not the cause of the 2026-11-09 flakiness: see PROBE_URL below.)
# Probe endpoint. www.gstatic.com is routed through this fleet google_ipv4_out
# (the Google IPv4 pin); measured 2026-11-09 it loses about one attempt in
# twenty from the host while cp.cloudflare.com is 20/20 through the same
# tunnel in the same window. A lane verdict must not inherit flakiness from
# whichever destination it happens to pick, so the probe uses a neutral
# anycast 204 endpoint and falls back to the gstatic one only when that
# endpoint itself is down.
PROBE_URL="${VPS_ACCEPT_PROBE_URL:-https://cp.cloudflare.com/generate_204}"
PROBE_FALLBACK_URL="https://www.gstatic.com/generate_204"
XRAY_BINARY="${VPS_ACCEPT_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
XRAY_CONF="${VPS_ACCEPT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_BINARY="${VPS_ACCEPT_SB_BINARY:-/etc/v2ray-agent/sing-box/sing-box}"
SB_SOURCE_DIR="${VPS_ACCEPT_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_ROOT="${VPS_ACCEPT_NGINX_ROOT:-/usr/share/nginx/html}"
NGINX_CONF_D="${VPS_ACCEPT_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
WORK="$(mktemp -d)"
chmod 700 "$WORK"
PIDS=()

cleanup() {
    trap - EXIT INT TERM HUP
    for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
    rm -rf -- "$WORK"
}
# HUP rides in on a dropped channel (2026-11-03 orphan evidence); the probe
# clients must die with it like on INT/TERM.
trap cleanup EXIT INT TERM HUP

fail=0
na=0
record() {
    local lane="$1" rc="$2" detail="$3"
    case "$rc" in
        0)   echo "LANE=$lane RESULT=PASS $detail" ;;
        # none: the lane is genuinely absent on this host architecture and
        # stays free (an Xray-only host is not permanently PARTIAL). na: the
        # lane applies to this host but could not be probed, so the run only
        # reaches PARTIAL, per the verdict contract in the header comment.
        none) echo "LANE=$lane RESULT=N/A $detail" ;;
        na)  echo "LANE=$lane RESULT=N/A $detail"; na=1 ;;
        *)   echo "LANE=$lane RESULT=FAIL $detail"; fail=1 ;;
    esac
}

record_nonfatal_failure() {
    local lane="$1" detail="$2"
    echo "LANE=$lane RESULT=FAIL $detail"
    fail=1
}

probe_last_code=""
probe_last_exit=0
probe_via_socks_once() {
    # $1 = local socks port. Store both values in the parent shell. Calling
    # this through $(...) loses probe_last_exit because the assignment runs in
    # a subshell; that defect made every previously reported failure look like
    # curl-exit-0 regardless of the real transport error.
    local port="$1"
    probe_last_code=""
    probe_last_exit=0
    probe_last_code="$(curl -sS -m "$TIMEOUT" -o /dev/null -w '%{http_code}' \
        --socks5-hostname "127.0.0.1:$port" \
        "$PROBE_URL" 2>/dev/null)" || probe_last_exit=$?
    if [ "$probe_last_code" != '204' ]; then
        # Only a second *destination* is tried, never a second attempt at
        # the same one: that keeps "this lane is down" distinct from "this
        # endpoint answered slowly".
        local fallback_code fallback_exit=0
        fallback_code="$(curl -sS -m "$TIMEOUT" -o /dev/null -w '%{http_code}' \
            --socks5-hostname "127.0.0.1:$port" \
            "$PROBE_FALLBACK_URL" 2>/dev/null)" || fallback_exit=$?
        if [ "$fallback_code" = '204' ]; then
            probe_last_code="$fallback_code"
            probe_last_exit=0
        fi
    fi
}

probe_via_socks() {
    # A just-restarted kernel/host can briefly fail the first REALITY
    # handshake while the same listener immediately succeeds on the next
    # attempt. Keep this retry bounded so persistent failures remain FAIL,
    # while a single cold-start transient does not poison all lanes.
    local port="$1" attempt
    for attempt in 1 2; do
        probe_via_socks_once "$port"
        if [ "$probe_last_code" = '204' ]; then
            break
        fi
        if [ "$attempt" -eq 1 ]; then
            sleep 2
        fi
    done
    printf '%s' "$probe_last_code"
}

probe_via_socks_detail() {
    # Same bounded retry, but retain code and curl exit in the parent shell so
    # a DNS failure, refused TCP connection and timeout remain distinguishable.
    local port="$1" attempt
    for attempt in 1 2; do
        probe_via_socks_once "$port"
        if [ "$probe_last_code" = '204' ]; then
            break
        fi
        if [ "$attempt" -eq 1 ]; then
            sleep 2
        fi
    done
}

probe_failure_code() {
    local code="$1" exit_code="$2"
    if [ -n "$code" ] && [ "$code" != '000' ]; then
        printf '%s' "$code"
    elif [ "$exit_code" -eq 5 ] || [ "$exit_code" -eq 6 ]; then
        printf 'dns-unresolved'
    elif [ "$exit_code" -eq 7 ]; then
        printf 'transport-refused'
    elif [ "$exit_code" -eq 28 ]; then
        printf 'timeout'
    elif [ "$exit_code" -eq 97 ]; then
        printf 'udp-unfiltered-QUIC-inconclusive'
    else
        printf 'curl-exit-%s' "${exit_code:-none}"
    fi
}

client_log_class() {
    # Return a fixed, non-secret class instead of raw Xray output. A failure
    # verdict must distinguish "client never started" from a tunnel timeout,
    # REALITY rejection or process crash without leaking IDs or destinations.
    local log="$1"
    if [ ! -s "$log" ]; then
        printf 'empty'
    elif grep -aEqi 'panic:|fatal error:' "$log"; then
        printf 'crash'
    elif grep -aEqi 'address already in use|bind:.*in use' "$log"; then
        printf 'bind-conflict'
    elif grep -aEqi 'connection refused|connect: no route' "$log"; then
        printf 'connect-failed'
    elif grep -aEqi 'context deadline exceeded|i/o timeout|timed out' "$log"; then
        printf 'timeout'
    elif grep -aEqi 'REALITY:.*(invalid|reject|fallback)' "$log"; then
        printf 'reality-rejected'
    elif grep -aEqi 'tls:|x509:|handshake' "$log"; then
        printf 'tls-failed'
    elif grep -aEqi 'reset by peer|broken pipe|unexpected EOF' "$log"; then
        printf 'peer-close'
    else
        printf 'other'
    fi
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

# A fixed sleep races process startup on loaded hosts. The SOCKS listener is
# the first observable readiness point; curl's own timeout then covers the
# subsequent tunnel handshake.
wait_for_local_socks_listener() {
    local port="$1" attempt
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$port" && return 0
        sleep 0.5
    done
    return 1
}

stop_background_process() {
    local pid="$1"
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
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
            # Unit separator, not a tab: names[0]/flow can be empty and a
            # whitespace IFS collapses the gap, shifting later columns.
            found = '\x1f'.join([
                str(ib.get('port')), clients[0].get('id', ''),
                names[0], rs.get('publicKey', ''), sids[0],
                clients[0].get('flow', ''),
                names[1] if len(names) > 1 else '',
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
            # Unit separator, not a tab: flow can be empty and a whitespace
            # IFS collapses the resulting double tab, shifting every later
            # column into the wrong variable.
            found = '\x1f'.join([
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
        line = '\x1f'.join([
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
    python3 - "$XRAY_CONF" "$NGINX_CONF_D" <<'PY'
import glob, json, os, re, sys

conf, nginx_dir = sys.argv[1:3]
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
    # After xhttp_nginx_front.ps1 the dokodemo wrapper points at nginx on a
    # loopback front port instead of at the inbound, so match either hop.
    # bwg and zz name the same managed front differently; checking only bwg's
    # file skipped zz's 10094 lane while the verdict still said PASS.
    front = None
    for name in ('vps-launcher-xhttp-front.conf', 'vps-launcher-zz-xhttp-front.conf'):
        front_conf = os.path.join(nginx_dir, name)
        if not os.path.isfile(front_conf):
            continue
        try:
            text = open(front_conf, encoding='utf-8').read()
        except OSError:
            continue
        match = re.search(r'listen\s+(?:[\d.]+:)?(\d+)\s+ssl;', text)
        if match:
            front = int(match.group(1))
            break
    for other in inbounds:
        if other.get('protocol') != 'dokodemo-door' or loopback(other.get('listen')):
            continue
        settings = other.get('settings') or {}
        if str(settings.get('address')) not in ('127.0.0.1', 'localhost'):
            continue
        if settings.get('port') in (port, front):
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
    elif security in ('tls', 'none'):
        # After xhttp_nginx_front.ps1 the inbound is plain HTTP behind nginx;
        # the client still speaks TLS to the public port, so the lane keeps its
        # tls shape here.
        sni = tls.get('serverName') or xhttp.get('host') or ''
        if not sni:
            invalid = True
            continue
    else:
        continue
    # Unit separator, not a tab: the TLS lane leaves publicKey/shortId empty
    # and `read` with a whitespace IFS collapses those empty fields, shifting
    # every later column into the wrong variable.
    # `none` is the fronted TLS lane (nginx terminates TLS), so both shapes
    # report under the same xhttp-tls name.
    kind = 'reality' if security == 'reality' else 'tls'
    lines.append('\x1f'.join([
        kind, str(connect), clients[0].get('id', ''), sni, public_key,
        short_id, xhttp.get('path') or '/', xhttp.get('host') or sni,
    ]))
if invalid:
    raise SystemExit(4)
if not lines:
    raise SystemExit(3)
print('\n'.join(lines))
PY
}

# cdn_facts describes the Cloudflare-fronted VLESS+WS lane. The published
# address is the edge, not this host: the client's TLS ends at Cloudflare, the
# edge reaches the origin nginx on 2087, and nginx proxies the secret path to
# a loopback inbound. A decoy-page 200 proves only the origin; this lane is
# the only one that survives an origin-IP block, so it gets a real handshake.
cdn_facts() {
    python3 - "$XRAY_CONF" "$NGINX_CONF_D" <<'PY'
import glob, json, os, re, sys

conf_dir, nginx_dir = sys.argv[1:3]
conf_path = os.path.join(nginx_dir, 'vps-launcher-cdn.conf')
if not os.path.isfile(conf_path):
    raise SystemExit(3)
try:
    text = open(conf_path, encoding='utf-8').read()
except OSError:
    raise SystemExit(4)
host = port = path = None
for line in text.splitlines():
    line = line.strip()
    match = re.search(r'^listen\s+(\d+)\s+ssl;', line)
    if match:
        port = int(match.group(1))
    if line.startswith('server_name '):
        host = line.split(None, 1)[1].rstrip(';')
    if line.startswith('location ^~ '):
        path = line.split(None, 2)[2].rstrip(' {')
if not host or not port or not path:
    raise SystemExit(4)

uuid = None
inbound_path = None
invalid = False
for candidate in sorted(glob.glob(os.path.join(conf_dir, '*.json'))):
    try:
        data = json.load(open(candidate, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        if ib.get('tag') != 'vps-launcher-cdn-ws':
            continue
        clients = (ib.get('settings') or {}).get('clients') or []
        if clients:
            uuid = clients[0].get('id')
            inbound_path = ((ib.get('streamSettings') or {}).get('wsSettings') or {}).get('path')
if invalid:
    raise SystemExit(4)
if not uuid:
    raise SystemExit(4)
if inbound_path and inbound_path != path:
    raise SystemExit(4)
print('\x1f'.join([host, str(port), host, path, uuid]))
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
        IFS=$'\x1f' read -r rport ruuid rsni rpk rsid rflow ralt <<<"$facts"
        x_socks_port="$(next_local_port)"
        VPS_PROBE_UUID="$ruuid" python3 - "$WORK/x.json" "$x_socks_port" "$rport" "$rflow" "$rsni" "$rpk" "$rsid" <<'PY'
import json
import os
import sys

out, socks, port, flow, sni, public_key, short_id = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
data = {
        "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": "127.0.0.1", "port": int(port), "users": [{"id": uuid, "flow": flow, "encryption": "none"}]}]},
        # sockopt belongs inside streamSettings (the transport dialer): as a
        # sibling key Xray drops it silently and the IPv4 pin never applies.
        "streamSettings": {
            "network": "tcp", "security": "reality",
            "realitySettings": {"serverName": sni, "fingerprint": "chrome", "publicKey": public_key, "shortId": short_id},
            "sockopt": {"domainStrategy": "UseIPv4"},
        },
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
        "$XRAY_BINARY" run -c "$WORK/x.json" >"$WORK/x.log" 2>&1 &
        x_pid=$!
        PIDS+=("$x_pid")
        if ! wait_for_local_socks_listener "$x_socks_port"; then
            record reality-xray 1 "port=$rport client_not_listening client_log=$(client_log_class "$WORK/x.log")"
        else
            probe_via_socks_detail "$x_socks_port"
            code="$probe_last_code"
            probe_exit="$probe_last_exit"
            if [ "$code" = '204' ]; then
                record reality-xray 0 "port=$rport sid=$rsid"
            else
                failure="$(probe_failure_code "$code" "$probe_exit")"
                record reality-xray 1 "port=$rport code=${code:-none} failure=$failure client_log=$(client_log_class "$WORK/x.log")"
            fi
        fi
        stop_background_process "$x_pid"
        # The alt-SNI lane is the only hardening write face without a runtime
        # probe: a drifted alt vhost or a dropped second serverName would keep
        # every other record green while the published -alt node is dead.
        if [ -n "$ralt" ]; then
            alt_socks_port="$(next_local_port)"
            VPS_PROBE_UUID="$ruuid" python3 - "$WORK/x-alt.json" "$alt_socks_port" "$rport" "$rflow" "$ralt" "$rpk" "$rsid" <<'PY'
import json
import os
import sys

out, socks, port, flow, sni, public_key, short_id = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
data = {
        "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": "127.0.0.1", "port": int(port), "users": [{"id": uuid, "flow": flow, "encryption": "none"}]}]},
        # sockopt belongs inside streamSettings (the transport dialer): as a
        # sibling key Xray drops it silently and the IPv4 pin never applies.
        "streamSettings": {
            "network": "tcp", "security": "reality",
            "realitySettings": {"serverName": sni, "fingerprint": "chrome", "publicKey": public_key, "shortId": short_id},
            "sockopt": {"domainStrategy": "UseIPv4"},
        },
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$XRAY_BINARY" run -c "$WORK/x-alt.json" >"$WORK/x-alt.log" 2>&1 &
            alt_pid=$!
            PIDS+=("$alt_pid")
            if ! wait_for_local_socks_listener "$alt_socks_port"; then
                record reality-xray-alt 1 "port=$rport client_not_listening client_log=$(client_log_class "$WORK/x-alt.log")"
            else
                probe_via_socks_detail "$alt_socks_port"
                code="$probe_last_code"
                probe_exit="$probe_last_exit"
                if [ "$code" = '204' ]; then
                    record reality-xray-alt 0 "port=$rport alt=$ralt"
                else
                    failure="$(probe_failure_code "$code" "$probe_exit")"
                    record reality-xray-alt 1 "port=$rport code=${code:-none} failure=$failure client_log=$(client_log_class "$WORK/x-alt.log")"
                fi
            fi
            stop_background_process "$alt_pid"
        else
            record reality-xray-alt na "no alt serverName"
        fi
        # Unauthenticated probes are what an active prober sends. REALITY must
        # relay them to the local decoy vhost, so a 200 carrying the decoy page
        # is the evidence that the camouflage half of the lane still works.
        decoy_sha="$(sha256sum "$NGINX_ROOT/index.html" 2>/dev/null | awk '{print $1}')"
        fallback_code="$(curl -sk -m "$TIMEOUT" -o "$WORK/fallback.html" -w '%{http_code}' \
            --resolve "${rsni}:${rport}:127.0.0.1" "https://${rsni}:${rport}/" 2>/dev/null || true)"
        got_sha="$(sha256sum "$WORK/fallback.html" 2>/dev/null | awk '{print $1}')"
        if [ -z "$decoy_sha" ]; then
            record reality-fallback na "no decoy root index"
        elif [ "$fallback_code" = '200' ] && [ "$got_sha" = "$decoy_sha" ]; then
            record reality-fallback 0 "port=$rport page=decoy"
        else
            record reality-fallback 1 "port=$rport code=${fallback_code:-none} decoy_match=no"
        fi
        if [ -n "$PUBLIC_NAME" ]; then
            public_socks_port="$(next_local_port)"
            VPS_PROBE_UUID="$ruuid" python3 - "$WORK/x-public.json" "$public_socks_port" "$REALITY_PUBLIC_PORT" "$rflow" "$rsni" "$rpk" "$rsid" "$PUBLIC_NAME" <<'PY'
import json
import os
import sys

out, socks, port, flow, sni, public_key, short_id, address = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
data = {
    "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": address, "port": int(port), "users": [{"id": uuid, "flow": flow, "encryption": "none"}]}]},
        # sockopt belongs inside streamSettings (the transport dialer): as a
        # sibling key Xray drops it silently and the IPv4 pin never applies.
        # This probe dials the public name, so the pin is what keeps the
        # verdict off the host's AAAA ordering (see the header note above).
        "streamSettings": {
            "network": "tcp", "security": "reality",
            "realitySettings": {"serverName": sni, "fingerprint": "chrome", "publicKey": public_key, "shortId": short_id},
            "sockopt": {"domainStrategy": "UseIPv4"},
        },
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$XRAY_BINARY" run -c "$WORK/x-public.json" >"$WORK/x-public.log" 2>&1 &
            public_pid=$!
            PIDS+=("$public_pid")
            if ! wait_for_local_socks_listener "$public_socks_port"; then
                record reality-xray-public 1 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT client_not_listening client_log=$(client_log_class "$WORK/x-public.log")"
            else
                probe_via_socks_detail "$public_socks_port"
                code="$probe_last_code"
                probe_exit="$probe_last_exit"
                if [ "$code" = '204' ]; then
                    record reality-xray-public 0 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT"
                else
                    failure="$(probe_failure_code "$code" "$probe_exit")"
                    record reality-xray-public 1 "name=$PUBLIC_NAME port=$REALITY_PUBLIC_PORT code=${code:-none} failure=$failure client_log=$(client_log_class "$WORK/x-public.log")"
                fi
            fi
            stop_background_process "$public_pid"
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
            VPS_PROBE_UUID="$xuuid" python3 - "$WORK/xhttp-$xkind.json" "$x_socks" "$xport" "$xsni" "$xpk" "$xsid" "$xpath" "$xhost" "$xkind" <<'PY'
import json
import os
import sys

out, socks, port, sni, public_key, short_id, path, host, kind = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
stream = {
    "network": "xhttp",
    # The published TLS-XHTTP node deliberately selects packet-up: stream-one
    # over Go 1.27/H2 has an open connection-close failure mode upstream
    # (#6797/#7296) and was intermittently dead on zz while packet-up was
    # stable on both hosts. REALITY-XHTTP remains on auto.
    "xhttpSettings": {
        "host": host,
        "path": path,
        "mode": "packet-up" if kind == "tls" else "auto",
    },
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
# The transport dialer pins IPv4 like every other public-name probe; as a
# sibling of streamSettings the key would be silently dropped by Xray.
stream["sockopt"] = {"domainStrategy": "UseIPv4"}
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
            x_client_pid=$!
            PIDS+=("$x_client_pid")
            if ! wait_for_local_socks_listener "$x_socks"; then
                record "xhttp-$xkind" 1 "port=$xport client_not_listening client_log=$(client_log_class "$WORK/xhttp-$xkind.log")"
                stop_background_process "$x_client_pid"
                continue
            fi
            probe_via_socks_detail "$x_socks"
            code="$probe_last_code"
            x_probe_exit="$probe_last_exit"
            if [ "$code" = '204' ]; then
                record "xhttp-$xkind" 0 "port=$xport path=$xpath"
            else
                failure="$(probe_failure_code "$code" "$x_probe_exit")"
                record "xhttp-$xkind" 1 "port=$xport code=${code:-none} failure=$failure client_log=$(client_log_class "$WORK/xhttp-$xkind.log")"
            fi
            stop_background_process "$x_client_pid"
        done <<<"$xhttp_lines"
    fi
fi

# --- CDN VLESS+WS (Cloudflare edge -> origin nginx -> loopback Xray) ---
if [ -x "$XRAY_BINARY" ] && [ -d "$XRAY_CONF" ]; then
    cdn_line=''
    cdn_rc=0
    cdn_line="$(cdn_facts 2>/dev/null)" || cdn_rc=$?
    if [ "$cdn_rc" -eq 3 ]; then
        record cdn-ws none "no cloudflare ws lane"
    elif [ "$cdn_rc" -ne 0 ]; then
        record cdn-ws 1 "source_unreadable"
    else
        IFS=$'\x1f' read -r cdn_host cdn_port cdn_sni cdn_path cdn_uuid <<<"$cdn_line"
        c_socks="$(next_local_port)"
        VPS_PROBE_UUID="$cdn_uuid" python3 - "$WORK/cdn.json" "$c_socks" "$cdn_host" "$cdn_port" "$cdn_sni" "$cdn_path" <<'PY'
import json
import os
import sys

out, socks, host, port, sni, path = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
data = {
    "inbounds": [{"listen": "127.0.0.1", "port": int(socks), "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": host, "port": int(port), "users": [{"id": uuid, "encryption": "none"}]}]},
        "streamSettings": {
            "network": "ws",
            "security": "tls",
            "tlsSettings": {"serverName": sni, "fingerprint": "chrome", "alpn": ["http/1.1"]},
            "wsSettings": {"path": path, "headers": {"Host": sni}},
        "sockopt": {"domainStrategy": "UseIPv4"},
        },
    }],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
        "$XRAY_BINARY" run -c "$WORK/cdn.json" >"$WORK/cdn.log" 2>&1 &
        cdn_pid=$!
        PIDS+=("$cdn_pid")
        if ! wait_for_local_socks_listener "$c_socks"; then
            record cdn-ws 1 "host=$cdn_host port=$cdn_port client_not_listening client_log=$(client_log_class "$WORK/cdn.log")"
        else
            probe_via_socks_detail "$c_socks"
            code="$probe_last_code"
            cdn_probe_exit="$probe_last_exit"
            if [ "$code" = '204' ]; then
                record cdn-ws 0 "host=$cdn_host port=$cdn_port"
            else
                failure="$(probe_failure_code "$code" "$cdn_probe_exit")"
                record cdn-ws 1 "host=$cdn_host port=$cdn_port code=${code:-none} failure=$failure client_log=$(client_log_class "$WORK/cdn.log")"
            fi
        fi
        stop_background_process "$cdn_pid"
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
        IFS=$'\x1f' read -r rport ruuid rsni rsid rflow private_key <<<"$facts"
        reality_sources=1
        pubkey="$(derive_reality_public_key "$private_key" 2>/dev/null || true)"
        if [ -z "$pubkey" ]; then
            echo "LANE=reality-singbox RESULT=FAIL reason=public_key_derivation port=$rport" >&2
            fail=1
        else
            unset private_key
            sb_socks_port="$(next_local_port)"
            VPS_PROBE_UUID="$ruuid" python3 - "$WORK/sb.json" "$sb_socks_port" "$rport" "$rflow" "$rsni" "$pubkey" "$rsid" <<'PY'
import json
import os
import sys

out, socks, port, flow, sni, public_key, short_id = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
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
                VPS_PROBE_UUID="$ruuid" python3 - "$WORK/sb-public.json" "$sb_public_socks_port" "$REALITY_PUBLIC_PORT" "$rflow" "$rsni" "$pubkey" "$rsid" "$PUBLIC_NAME" <<'PY'
import json
import os
import sys

out, socks, port, flow, sni, public_key, short_id, address = sys.argv[1:]
uuid = os.environ['VPS_PROBE_UUID']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{
        "type": "vless", "server": address, "server_port": int(port), "uuid": uuid, "flow": flow,
        # This probe dials the public name; sing-box's outbound-level dialer
        # strategy is the counterpart of the Xray streamSettings.sockopt pin.
        "domain_strategy": "ipv4_only",
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
if [ "$reality_sources" -eq 0 ]; then record reality na "no usable reality source"; fi

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
        IFS=$'\x1f' read -r bport bpass bsni _ _ <<<"$base_line"
        hy2_base_socks_port="$(next_local_port)"
        VPS_PROBE_PASSWORD="$bpass" python3 - "$WORK/hb.json" "$hy2_base_socks_port" "$bport" "$bsni" <<'PY'
import json
import os
import sys

out, socks, port, sni = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": "127.0.0.1", "server_port": int(port), "password": password, "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
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
            VPS_PROBE_PASSWORD="$bpass" python3 - "$WORK/hb-public.json" "$hy2_base_public_socks_port" "$bport" "$bsni" "$PUBLIC_NAME" <<'PY'
import json
import os
import sys

out, socks, port, sni, address = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
# Public-name dialers pin IPv4 at the outbound level (the sing-box dialer
# strategy), matching the Xray streamSettings.sockopt pins: the probe must
# not inherit the host's AAAA ordering.
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": address, "server_port": int(port), "password": password, "domain_strategy": "ipv4_only", "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
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
        record hysteria2-base na "no base hysteria2 inbound"
    fi
    if [ "$hy2_password_check_failed" -eq 1 ]; then
        record hysteria2-obfs 1 "password_check_failed"
    fi
    if [ -n "$obfs_line" ]; then
        IFS=$'\x1f' read -r oport opass osni otype osalt <<<"$obfs_line"
        if [ "$hy2_password_check_failed" -eq 1 ]; then
            :
        elif [ -n "$OBFS_PASSWORD_FILE" ] && { [ "$oport" != "$OBFS_PORT" ] || [ "$otype" != 'salamander' ]; }; then
            echo "LANE=hysteria2-obfs RESULT=FAIL reason=obfs_contract port=$oport" >&2
            fail=1
        else
            hy2_obfs_socks_port="$(next_local_port)"
            VPS_PROBE_PASSWORD="$opass" VPS_PROBE_SALT="$osalt" python3 - "$WORK/ho.json" "$hy2_obfs_socks_port" "$oport" "$osni" "$otype" <<'PY'
import json
import os
import sys

out, socks, port, sni, obfs_type = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
obfs_password = os.environ['VPS_PROBE_SALT']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": "127.0.0.1", "server_port": int(port), "password": password, "obfs": {"type": obfs_type, "password": obfs_password}, "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
            "$SB_BINARY" run -c "$WORK/ho.json" >"$WORK/ho.log" 2>&1 &
            PIDS+=("$!")
            sleep 4
            code="$(probe_via_socks "$hy2_obfs_socks_port")"
            if [ "$code" = '204' ]; then
                record hysteria2-obfs 0 "port=$oport obfs=$otype"
            else
                # UDP has no TCP-style refusal on the server. Re-run without
                # salamander and with a deliberately wrong password; if that
                # also times out, a local QUIC/TLS failure or a UDP mapping
                # problem must not be presented as "the obfs lane is alive".
                VPS_PROBE_PASSWORD="$opass" python3 - "$WORK/ho-noobfs.json" "$hy2_obfs_socks_port" "$oport" "$osni" <<'PY'
import json
import os
import sys

out, socks, port, sni = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": "127.0.0.1", "server_port": int(port), "password": password, "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                VPS_PROBE_PASSWORD="$opass" python3 - "$WORK/ho-wrong-password.json" "$hy2_obfs_socks_port" "$oport" "$osni" "$otype" <<'PY'
import json
import os
import sys

out, socks, port, sni, obfs_type = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": "127.0.0.1", "server_port": int(port), "password": password, "obfs": {"type": obfs_type, "password": "vps-launcher-wrong-password"}, "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                "$SB_BINARY" run -c "$WORK/ho-noobfs.json" >"$WORK/ho-noobfs.log" 2>&1 &
                noobfs_pid=$!
                PIDS+=("$noobfs_pid")
                sleep 4
                probe_via_socks_detail "$hy2_obfs_socks_port"
                noobfs_code="$probe_last_code"
                noobfs_exit="$probe_last_exit"
                kill "$noobfs_pid" 2>/dev/null || true
                wait "$noobfs_pid" 2>/dev/null || true
                "$SB_BINARY" run -c "$WORK/ho-wrong-password.json" >"$WORK/ho-wrong-password.log" 2>&1 &
                wrong_pid=$!
                PIDS+=("$wrong_pid")
                sleep 4
                probe_via_socks_detail "$hy2_obfs_socks_port"
                wrong_code="$probe_last_code"
                wrong_exit="$probe_last_exit"
                kill "$wrong_pid" 2>/dev/null || true
                wait "$wrong_pid" 2>/dev/null || true
                noobfs_failure="$(probe_failure_code "$noobfs_code" "$noobfs_exit")"
                wrong_failure="$(probe_failure_code "$wrong_code" "$wrong_exit")"
                if [ "$noobfs_code" = '204' ]; then
                    record_nonfatal_failure hysteria2-obfs "port=$oport noobfs_204=obfs_ineffective"
                elif [ "$wrong_code" = '204' ]; then
                    record_nonfatal_failure hysteria2-obfs "port=$oport wrong_password_204=auth_bypass"
                else
                    # curl 97 means UDP reached a QUIC peer, but the probe's
                    # socks path still lacked a complete response. That is
                    # inconclusive; a timeout/SYN-like refusal is a real fail.
                    if [ "$noobfs_failure" = 'udp-unfiltered-QUIC-inconclusive' ] || [ "$wrong_failure" = 'udp-unfiltered-QUIC-inconclusive' ]; then
                        record hysteria2-obfs na "port=$oport noobfs=$noobfs_failure wrong=$wrong_failure"
                    else
                        record hysteria2-obfs 1 "port=$oport code=${code:-none} noobfs=$noobfs_failure wrong=$wrong_failure"
                    fi
                fi
            fi
            if [ -n "$PUBLIC_NAME" ]; then
                hy2_obfs_public_socks_port="$(next_local_port)"
                VPS_PROBE_PASSWORD="$opass" VPS_PROBE_SALT="$osalt" python3 - "$WORK/ho-public.json" "$hy2_obfs_public_socks_port" "$oport" "$osni" "$otype" "$PUBLIC_NAME" <<'PY'
# NOTE: keep the outbound-level "domain_strategy": "ipv4_only" pin (see the
# header note): this probe dials the public name.
import json
import os
import sys

out, socks, port, sni, obfs_type, address = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
obfs_password = os.environ['VPS_PROBE_SALT']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": address, "server_port": int(port), "password": password, "domain_strategy": "ipv4_only", "obfs": {"type": obfs_type, "password": obfs_password}, "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                "$SB_BINARY" run -c "$WORK/ho-public.json" >"$WORK/ho-public.log" 2>&1 &
                PIDS+=("$!")
                sleep 4
                code="$(probe_via_socks "$hy2_obfs_public_socks_port")"
                if [ "$code" = '204' ]; then
                    record hysteria2-obfs-public 0 "name=$PUBLIC_NAME port=$oport obfs=$otype"
                else
                    public_socks="$(next_local_port)"
                    VPS_PROBE_PASSWORD="$opass" python3 - "$WORK/ho-public-noobfs.json" "$public_socks" "$oport" "$osni" "$PUBLIC_NAME" <<'PY'
import json
import os
import sys

out, socks, port, sni, address = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": address, "server_port": int(port), "password": password, "domain_strategy": "ipv4_only", "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                    "$SB_BINARY" run -c "$WORK/ho-public-noobfs.json" >"$WORK/ho-public-noobfs.log" 2>&1 &
                    public_noobfs_pid=$!
                    PIDS+=("$public_noobfs_pid")
                    sleep 4
                    probe_via_socks_detail "$public_socks"
                    public_noobfs_code="$probe_last_code"
                    public_noobfs_exit="$probe_last_exit"
                    kill "$public_noobfs_pid" 2>/dev/null || true
                    wait "$public_noobfs_pid" 2>/dev/null || true
                    public_failure="$(probe_failure_code "$public_noobfs_code" "$public_noobfs_exit")"
                    # Classify with the no-obfs control probe only. DNS failure
                    # or a plain timeout means the UDP path itself is unusable
                    # from the public network (real fail); a QUIC response
                    # without a complete reply is inconclusive about the obfs
                    # lane. The former draft compared a never-assigned
                    # public_noobfs_failure and died under set -u before the
                    # verdict line could be recorded.
                    if [ "$public_failure" = 'dns-unresolved' ] || [ "$public_failure" = 'timeout' ]; then
                        record hysteria2-obfs-public 1 "name=$PUBLIC_NAME port=$oport code=${code:-none} noobfs=$public_failure"
                    else
                        record hysteria2-obfs-public na "name=$PUBLIC_NAME port=$oport noobfs=$public_failure"
                    fi
                fi
            fi
            # BWG's directly assigned public IP does not hairpin through
            # PREROUTING from the host itself. Its hop rule is proven by a
            # cross-host client and the nft counter; zz is the in-host twin.
            if [ "$PROFILE" = 'zz' ] && [ -n "$PUBLIC_NAME" ]; then
                hy2_obfs_hop_socks_port="$(next_local_port)"
                VPS_PROBE_PASSWORD="$opass" VPS_PROBE_SALT="$osalt" python3 - "$WORK/ho-hop-public.json" "$hy2_obfs_hop_socks_port" "$HOP_PORT" "$osni" "$otype" "$PUBLIC_NAME" <<'PY'
import json
import os
import sys

out, socks, port, sni, obfs_type, address = sys.argv[1:]
password = os.environ['VPS_PROBE_PASSWORD']
obfs_password = os.environ['VPS_PROBE_SALT']
data = {
    "log": {"level": "error"},
    "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": int(socks)}],
    "outbounds": [{"type": "hysteria2", "server": address, "server_port": int(port), "password": password, "domain_strategy": "ipv4_only", "obfs": {"type": obfs_type, "password": obfs_password}, "tls": {"enabled": True, "server_name": sni, "alpn": ["h3"]}, "up_mbps": 100, "down_mbps": 50}],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
PY
                "$SB_BINARY" run -c "$WORK/ho-hop-public.json" >"$WORK/ho-hop-public.log" 2>&1 &
                PIDS+=("$!")
                sleep 4
                code="$(probe_via_socks "$hy2_obfs_hop_socks_port")"
                if [ "$code" = '204' ]; then
                    record hysteria2-obfs-hop-public 0 "name=$PUBLIC_NAME port=$HOP_PORT obfs=$otype"
                else
                    record hysteria2-obfs-hop-public 1 "name=$PUBLIC_NAME port=$HOP_PORT code=${code:-none}"
                fi
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
'@.Replace("__OBFS_PORT__", "$ObfsPort").Replace("__HOP_PORT__", "$HopPort").Replace("__PROFILE__", $Profile).Replace("__PUBLIC_NAME__", $PublicName).Replace("__REALITY_PUBLIC_PORT__", "$RealityPublicPort").Replace("__TIMEOUT__", "$TimeoutSeconds")

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
    # The shared salt must not ride argv (/proc/*/cmdline on the host): force
    # the mode-600 temp-file transport even though the payload is tiny.
    Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $uploadCommand -RemoteTempPrefix "proxy-password" -ViaTempFile
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
