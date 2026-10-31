#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$ObfsPort = 8444,
  [string]$MasqueradeUrl
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
if (-not $MasqueradeUrl) {
  $MasqueradeUrl = @{
    "bwg" = "https://fq.sciman.top:33387/"
    "zz"  = "https://zz.sciman.top:12378/"
  }[$Profile]
}
if ($MasqueradeUrl -notmatch '^https://[A-Za-z0-9.:/_-]+$') {
  throw "-MasqueradeUrl must be an https:// URL literal without shell syntax."
}
# The REALITY fallback must keep relaying to this host's own camouflage vhost.
# vasma rewrites both `target` and `serverNames` from whatever domain the
# operator answers at reinstall time; picking a public third-party site would
# make 443 present that site's certificate under our SNI, which is the most
# obvious REALITY signature an active prober can look for.
$realityHost = ([uri]$MasqueradeUrl).Host
if (-not $realityHost) {
  throw "-MasqueradeUrl must contain a host name."
}
# The Cloudflare fallback lane exists only on bwg; an empty host skips the
# section so zz keeps reporting exactly the lanes it actually runs.
$cdnHost = if ($Profile -eq "bwg") { "cdn.sciman.top" } else { "" }

# Read-only drift gate for the anti-blocking lanes. A vasma reinstall or
# a menu re-configuration regenerates the REALITY source configs (restoring the
# empty shortId) and can re-fetch the decoy site, silently undoing the hooks
# without touching any service: everything keeps "working" while the endpoint
# gets noisier to probe. Nothing else in the fleet notices that, so this lane
# reports a single greppable verdict the operator can wire into a patrol.
#
# It never writes. `HARDENING_DRIFT=NONE` is the clean verdict;
# `HARDENING_DRIFT=<lane,...>` names what regressed and which script re-applies
# it, so the fix is a copy of the command rather than a memory exercise.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
OBFS_PORT="__OBFS_PORT__"
EXPECTED_MASQUERADE_URL="__EXPECTED_MASQUERADE_URL__"
EXPECTED_REALITY_HOST="__EXPECTED_REALITY_HOST__"
CDN_HOST="__CDN_HOST__"
XRAY_CONF="${VPS_DRIFT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_SOURCE_DIR="${VPS_DRIFT_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_ROOT="${VPS_DRIFT_NGINX_ROOT:-/usr/share/nginx/html}"
NGINX_CONF_D="${VPS_DRIFT_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
drift=()

echo '==camouflage-page=='
root_index="$NGINX_ROOT/index.html"
decoy_index="$NGINX_ROOT/en/index.html"
cam_state=ok
if [ ! -f "$root_index" ]; then
    echo 'camouflage=missing-root'
    cam_state=drift
elif grep -qE 'Welcome to nginx!|If you see this page' "$root_index" 2>/dev/null; then
    echo 'camouflage=stock-default'
    cam_state=drift
elif [ ! -f "$decoy_index" ]; then
    # A vasma reinstall that re-fetched the decoy can drop en/ while the root
    # page survives; that breaks the next promotion, so surface it as drift.
    echo 'camouflage=decoy-source-missing'
    cam_state=drift
elif ! cmp -s "$root_index" "$decoy_index"; then
    # A root page that is neither stock nor the current decoy is still a
    # plausible site; report it but keep the verdict distinct so an operator
    # can tell "someone customised it" from "the lane was reverted".
    echo 'camouflage=root-differs-from-decoy'
else
    echo 'camouflage=ok'
fi
# Stock nginx artifacts in the root are the other half of the default-page
# fingerprint: 50x.html answers 200 on every vhost that serves this root and
# comes back with an nginx (re)install, so its presence is drift.
for stock_file in 50x.html index.nginx-debian.html; do
    if [ -e "$NGINX_ROOT/$stock_file" ] || [ -L "$NGINX_ROOT/$stock_file" ]; then
        echo "camouflage=stock-artifact file=$stock_file"
        cam_state=drift
    fi
done
if [ "$cam_state" = 'drift' ]; then
    drift+=('camouflage')
fi

echo '==reality-shortid=='
empty_xray="$(python3 - "$XRAY_CONF" <<'PY'
import glob, json, os, sys
conf = sys.argv[1]
found = 0
invalid = False
for path in glob.glob(os.path.join(conf, '*.json')):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        if '' in (rs.get('shortIds') or []):
            found += 1
print(found)
raise SystemExit(2 if invalid else 0)
PY
)" || empty_xray=error
empty_sb="$(python3 - "$SB_SOURCE_DIR" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
found = 0
invalid = False
for path in glob.glob(os.path.join(root, '*.json')):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        reality = ((ib.get('tls') or {}).get('reality')) or {}
        if '' in (reality.get('short_id') or []):
            found += 1
print(found)
raise SystemExit(2 if invalid else 0)
PY
)" || empty_sb=error
echo "xray_empty_short_id=${empty_xray}"
echo "singbox_empty_short_id=${empty_sb}"
if [ "$empty_xray" != '0' ] || [ "$empty_sb" != '0' ]; then
    drift+=('reality-shortid')
fi

echo '==reality-target=='
target_state="$(python3 - "$XRAY_CONF" "$SB_SOURCE_DIR" "$EXPECTED_REALITY_HOST" <<'PY'
import glob
import json
import os
import sys

xray_dir, sb_dir, expected = sys.argv[1:4]
rows = []
invalid = False


def host_of(value):
    value = str(value or '').strip()
    if not value:
        return ''
    if value.startswith('[') and ']' in value:
        return value[1:value.index(']')]
    if value.count(':') == 1:
        return value.split(':', 1)[0]
    return value


for path in sorted(glob.glob(os.path.join(xray_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        # `dest` is the historical alias of `target`; vasma already writes the
        # new name, but a hand-edited or older tree may still carry the old one.
        target = rs.get('target') or rs.get('dest') or ''
        names = [str(name) for name in (rs.get('serverNames') or [])]
        ok = host_of(target) == expected and expected in names
        rows.append(
            f"xray port={ib.get('port')} target={target or 'none'} "
            f"server_names={','.join(names) or 'none'} state={'ok' if ok else 'drift'}"
        )

for path in sorted(glob.glob(os.path.join(sb_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        tls = ib.get('tls') or {}
        reality = tls.get('reality') or {}
        if not reality or not reality.get('enabled'):
            continue
        handshake = reality.get('handshake') or {}
        server = str(handshake.get('server') or '')
        port = handshake.get('server_port')
        target = f"{server}:{port}" if server and port else server
        name = str(tls.get('server_name') or '')
        ok = host_of(target) == expected and name == expected
        rows.append(
            f"sing-box port={ib.get('listen_port')} target={target or 'none'} "
            f"server_name={name or 'none'} state={'ok' if ok else 'drift'}"
        )

for row in rows:
    print(row)
if invalid:
    print('reality_target=invalid')
elif not rows:
    print('reality_target=not-applicable')
elif all(row.endswith('state=ok') for row in rows):
    print('reality_target=ok')
else:
    print('reality_target=drift')
PY
)" || target_state='reality_target=invalid'
printf '%s\n' "$target_state"
if ! printf '%s\n' "$target_state" | grep -qE '^reality_target=(ok|not-applicable)$'; then
    drift+=('reality-target')
fi

echo '==nginx-stealth=='
stealth_facts="$(python3 - "$XRAY_CONF" "$SB_SOURCE_DIR" <<'PY'
import glob
import json
import os
import sys

xray_dir, sb_dir = sys.argv[1:3]
seen = {}
invalid = False


def add(port, sni):
    if isinstance(port, int) and port > 0 and sni:
        seen.setdefault(port, sni)


def split_port(value):
    value = str(value or '').strip()
    if value.startswith('[') and ']' in value:
        try:
            return int(value[value.index(']') + 1:].lstrip(':') or 0)
        except ValueError:
            return 0
    if value.count(':') == 1:
        try:
            return int(value.split(':', 1)[1])
        except ValueError:
            return 0
    return 0


for path in sorted(glob.glob(os.path.join(xray_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        names = [str(name) for name in (rs.get('serverNames') or []) if name]
        add(split_port(rs.get('target') or rs.get('dest')), names[0] if names else '')

for path in sorted(glob.glob(os.path.join(sb_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        tls = ib.get('tls') or {}
        reality = tls.get('reality') or {}
        if not reality or not reality.get('enabled'):
            continue
        add((reality.get('handshake') or {}).get('server_port'), str(tls.get('server_name') or ''))

if invalid:
    print('stealth_facts=invalid')
    raise SystemExit(4)
if not seen:
    print('stealth_facts=not-applicable')
else:
    for port in sorted(seen):
        print(f"port={port}")
        print(f"sni={seen[port]}")
PY
)" || stealth_facts='stealth_facts=invalid'
if printf '%s\n' "$stealth_facts" | grep -q '^stealth_facts=invalid$'; then
    echo 'stealth=invalid'
    drift+=('nginx-stealth')
elif printf '%s\n' "$stealth_facts" | grep -q '^stealth_facts=not-applicable$'; then
    echo 'stealth=not-applicable'
else
    stealth_bad=0
    stealth_port=''
    while IFS= read -r stealth_line; do
        case "$stealth_line" in
            port=*) stealth_port="${stealth_line#port=}" ;;
            sni=*)
                stealth_sni="${stealth_line#sni=}"
                stealth_out="$(echo | timeout 8 openssl s_client -connect "127.0.0.1:$stealth_port" \
                    -servername vps-launcher-probe.invalid 2>&1 || true)"
                if printf '%s' "$stealth_out" | grep -q 'BEGIN CERTIFICATE'; then
                    echo "unknown_sni_answered port=$stealth_port"
                    stealth_bad=1
                else
                    echo "unknown_sni_rejected port=$stealth_port"
                fi
                stealth_header="$(curl -sk -m 8 -D - -o /dev/null \
                    --resolve "$stealth_sni:$stealth_port:127.0.0.1" \
                    "https://$stealth_sni:$stealth_port/" 2>/dev/null |
                    tr -d '\r' | grep -i '^server:' | head -1)"
                if printf '%s' "$stealth_header" | grep -qi 'nginx/[0-9]'; then
                    echo "server_version_leak port=$stealth_port"
                    stealth_bad=1
                fi
                # The reject vhost must not have swallowed the real SNI: the
                # camouflage vhost has to keep answering 200, or the REALITY
                # fallback target is dead while every config check stays green.
                stealth_live="$(curl -sk -m 8 -o /dev/null -w '%{http_code}' \
                    --resolve "$stealth_sni:$stealth_port:127.0.0.1" \
                    "https://$stealth_sni:$stealth_port/" 2>/dev/null || true)"
                if [ "$stealth_live" = '200' ]; then
                    echo "true_sni_ok port=$stealth_port"
                else
                    echo "true_sni_broken port=$stealth_port code=${stealth_live:-none}"
                    stealth_bad=1
                fi
                ;;
        esac
    done <<<"$stealth_facts"
    if [ -f "$NGINX_CONF_D/vps-launcher-stealth.conf" ]; then
        echo 'stealth_conf=present'
    else
        echo 'stealth_conf=absent'
        stealth_bad=1
    fi
    if [ "$stealth_bad" -eq 0 ]; then echo 'stealth=applied'; else echo 'stealth=drift'; drift+=('nginx-stealth'); fi
fi

echo '==xhttp-front=='
front_state="$(python3 - "$XRAY_CONF" "$NGINX_CONF_D/vps-launcher-xhttp-front.conf" <<'PY'
import glob
import json
import os
import re
import sys

conf_dir, target = sys.argv[1:3]
inbounds = []
invalid = False
for path in sorted(glob.glob(os.path.join(conf_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        inbounds.append(ib)


def loopback(value):
    return str(value or '') in ('127.0.0.1', '::1', 'localhost')


entry = None
for ib in inbounds:
    stream = ib.get('streamSettings') or {}
    if stream.get('network') != 'xhttp' or not loopback(ib.get('listen')):
        continue
    if not ((ib.get('settings') or {}).get('clients') or []):
        continue
    entry = ib
    break

if invalid:
    print('xhttp_front=invalid')
    raise SystemExit(0)
if not entry:
    print('xhttp_front=not-applicable')
    raise SystemExit(0)

inner = entry.get('port')
inner_path = ((entry.get('streamSettings') or {}).get('xhttpSettings') or {}).get('path')
conf_text = ''
if os.path.isfile(target):
    try:
        conf_text = open(target, encoding='utf-8').read()
    except OSError:
        conf_text = ''
front = None
match = re.search(r'listen\s+(?:[\d.]+:)?(\d+)\s+ssl;', conf_text)
if match:
    front = int(match.group(1))
conf_inner = None
match = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+);', conf_text)
if match:
    conf_inner = int(match.group(1))
conf_path = None
match = re.search(r'location \^~ (\S+)', conf_text)
if match:
    conf_path = match.group(1)

wrapper = None
wrapper_target = None
for ib in inbounds:
    if ib.get('protocol') != 'dokodemo-door':
        continue
    settings = ib.get('settings') or {}
    if str(settings.get('address')) not in ('127.0.0.1', 'localhost'):
        continue
    if loopback(ib.get('listen')):
        continue
    if settings.get('port') in (inner, front):
        wrapper = ib.get('port')
        wrapper_target = settings.get('port')
        break

security = (entry.get('streamSettings') or {}).get('security') or 'none'
if security == 'tls' and wrapper_target == inner:
    print('xhttp_front=bare')
elif (
    security == 'none'
    and wrapper_target == front
    and conf_inner == inner
    and front is not None
    and conf_path
    and inner_path
    and conf_path == inner_path
):
    # The nginx location prefix must match the xray path exactly: a vasma
    # menu edit that renames the xray path leaves the front proxying to a
    # location nginx no longer routes, killing the lane while both ports
    # stay up.
    print('xhttp_front=applied')
    print(f'front_port={front}')
    print(f'published_port={wrapper}')
elif (
    security == 'none'
    and wrapper_target == front
    and conf_inner == inner
    and front is not None
):
    print('xhttp_front=path-mismatch')
    print(f'xray_path={inner_path or "none"}')
    print(f'nginx_location={conf_path or "none"}')
else:
    print('xhttp_front=drift')
PY
)" || front_state='xhttp_front=invalid'
printf '%s\n' "$front_state"
case "$front_state" in
    *'xhttp_front=not-applicable'*) ;;
    *'xhttp_front=applied'*)
        front_port="$(printf '%s\n' "$front_state" | sed -n 's/^front_port=//p')"
        published_port="$(printf '%s\n' "$front_state" | sed -n 's/^published_port=//p')"
        if [ -n "$front_port" ] && ! ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:$front_port"; then
            echo "front_loopback_not_listening port=$front_port"
            drift+=('xhttp-front')
        fi
        if [ -n "$published_port" ] && ! ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ":$published_port\$"; then
            echo "published_port_not_listening port=$published_port"
            drift+=('xhttp-front')
        fi
        ;;
    *) drift+=('xhttp-front') ;;
esac

echo '==xray-port-collision=='
collision_state="$(python3 - "$XRAY_CONF" <<'PY'
import glob
import json
import os
import sys

conf = sys.argv[1]
seen = {}
invalid = False
for path in sorted(glob.glob(os.path.join(conf, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        port = ib.get('port')
        if not isinstance(port, int) or port <= 0:
            continue
        seen.setdefault(port, []).append(
            f"{os.path.basename(path)}:{ib.get('tag') or ib.get('protocol') or 'untagged'}"
        )
duplicates = [
    f"{port}=" + ",".join(labels)
    for port, labels in sorted(seen.items())
    if len(labels) > 1
]
print(f"xray_duplicate_ports={','.join(duplicates) if duplicates else 'none'}")
raise SystemExit(2 if invalid else 0)
PY
)" || collision_state='xray_duplicate_ports=invalid'
printf '%s\n' "$collision_state"
if ! printf '%s\n' "$collision_state" | grep -q '^xray_duplicate_ports=none$'; then
    drift+=('xray-port-collision')
fi

echo '==hysteria2-obfs=='
fragment="$SB_SOURCE_DIR/98_vps_ssh_launcher_hysteria2_obfs.json"
base_present=0
base_state=absent
if [ -f "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" ]; then
    base_state="$(python3 - "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    raise SystemExit(2)
found = any(
    ib.get('type') == 'hysteria2' and ib.get('listen_port') == 443
    for ib in data.get('inbounds', []) or []
)
print('present' if found else 'absent')
PY
    )" || base_state=invalid
    if [ "$base_state" = 'present' ] || [ "$base_state" = 'invalid' ]; then
        base_present=1
    fi
fi
if [ "$base_present" -eq 0 ]; then
    echo 'obfs=not-applicable'
elif [ ! -f "$fragment" ]; then
    echo 'obfs=fragment-missing'
    drift+=('hysteria2-obfs')
else
    # File existence is not the contract: the fragment must still carry a
    # salamander-obfuscated, TLS-enabled hysteria2 inbound on the obfs port
    # with at least one user, or the backup UDP lane is gone while the file
    # and the listener both look fine.
    fragment_state="$(python3 - "$fragment" "$OBFS_PORT" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    raise SystemExit(2)
port = int(sys.argv[2])
found = any(
    ib.get('type') == 'hysteria2'
    and ib.get('listen_port') == port
    and ((ib.get('obfs') or {}).get('type') == 'salamander')
    and (ib.get('users') or [])
    and ((ib.get('tls') or {}).get('enabled') is True)
    for ib in data.get('inbounds', []) or []
)
print('obfs_contract=ok' if found else 'obfs_contract=bad')
PY
)" || fragment_state='obfs_contract=invalid'
    echo "$fragment_state"
    if [ "$fragment_state" != 'obfs_contract=ok' ]; then
        drift+=('hysteria2-obfs')
    elif ! ss -lun 2>/dev/null | grep -q ":$OBFS_PORT "; then
        echo 'obfs=fragment-present-not-listening'
        drift+=('hysteria2-obfs')
    else
        echo 'obfs=ok'
    fi
fi

echo '==hysteria2-masquerade=='
if [ "$base_present" -eq 0 ]; then
    echo 'masquerade=not-applicable'
else
    masquerade_state="$(python3 - "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" \
        "$SB_SOURCE_DIR/97_vps_ssh_launcher_singbox_log.json" "$EXPECTED_MASQUERADE_URL" <<'PY'
import json
import os
import sys

base_path, log_path = sys.argv[1:3]
masquerade_ok = False
log_ok = False
url = ''
base_count = 0
try:
    data = json.load(open(base_path, encoding='utf-8'))
    for ib in data.get('inbounds', []) or []:
        if ib.get('type') != 'hysteria2' or ib.get('listen_port') != 443:
            continue
        base_count += 1
        masq = ib.get('masquerade') or {}
        if (
            masq.get('type') == 'proxy'
            and masq.get('rewrite_host') is True
            and str(masq.get('url') or '') == sys.argv[3]
        ):
            masquerade_ok = True
            url = str(masq.get('url'))
    if os.path.isfile(log_path):
        log_ok = json.load(open(log_path, encoding='utf-8')).get('log', {}).get('level') == 'info'
except Exception:
    masquerade_ok = False
    log_ok = False
print(f"masquerade_set={'yes' if masquerade_ok else 'no'}")
print(f"base_count={base_count}")
print(f"log_level_pinned={'yes' if log_ok else 'no'}")
print(f"masquerade_url={url or 'none'}")
PY
    )" || masquerade_state='masquerade=invalid'
    printf '%s\n' "$masquerade_state"
    if ! printf '%s\n' "$masquerade_state" | grep -q '^masquerade_set=yes$' ||
       ! printf '%s\n' "$masquerade_state" | grep -q '^base_count=1$' ||
       ! printf '%s\n' "$masquerade_state" | grep -q '^log_level_pinned=yes$'; then
        drift+=('hysteria2-masquerade')
    fi
fi

echo '==cdn-fallback=='
if [ -z "$CDN_HOST" ]; then
    echo 'cdn=not-applicable'
else
    cdn_ok=1
    cdn_conf="$NGINX_CONF_D/vps-launcher-cdn.conf"
    cdn_frag="$XRAY_CONF/15_vps_ssh_launcher_cdn_ws.json"
    if [ -f "$cdn_conf" ] &&
        grep -q 'listen 2087 ssl;' "$cdn_conf" &&
        grep -q 'proxy_pass http://127.0.0.1:45989;' "$cdn_conf" &&
        grep -q 'proxy_set_header Upgrade \$http_upgrade;' "$cdn_conf"; then
        cdn_conf_state='ok'
    else
        cdn_conf_state='drift'
        cdn_ok=0
    fi
    if [ -f "$cdn_frag" ] &&
        grep -q '"vps-launcher-cdn-ws"' "$cdn_frag" &&
        grep -q '"port": 45989' "$cdn_frag"; then
        cdn_inbound_state='ok'
    else
        cdn_inbound_state='drift'
        cdn_ok=0
    fi
    if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -q ':2087$' &&
        ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx '127.0.0.1:45989'; then
        cdn_listen_state='up'
    else
        cdn_listen_state='down'
        cdn_ok=0
    fi
    # A proxied record resolves to the Cloudflare edge, never to this origin.
    # Equality with the REALITY host means the record went DNS-only, which
    # silently destroys the only lane that survives an origin-IP block.
    cdn_ip="$(getent ahostsv4 "$CDN_HOST" 2>/dev/null | awk 'NR==1{print $1}')"
    origin_ip="$(getent ahostsv4 "$EXPECTED_REALITY_HOST" 2>/dev/null | awk 'NR==1{print $1}')"
    if [ -z "$cdn_ip" ]; then
        cdn_dns_state='missing'
        cdn_ok=0
    elif [ -n "$origin_ip" ] && [ "$cdn_ip" = "$origin_ip" ]; then
        cdn_dns_state='not-proxied'
        cdn_ok=0
    else
        cdn_dns_state='proxied'
    fi
    # Config, DNS and listeners can all be green while the edge is dead: a
    # zone-wide SSL-mode flip turns every request into a 52x without touching
    # the origin. One edge request per pass is the cheapest end-to-end proof.
    cdn_edge_code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$CDN_HOST:2087/" 2>/dev/null || true)"
    if [ "$cdn_edge_code" != '200' ]; then
        cdn_ok=0
    fi
    echo "cdn_conf=$cdn_conf_state cdn_inbound=$cdn_inbound_state cdn_listen=$cdn_listen_state cdn_dns=$cdn_dns_state cdn_edge=${cdn_edge_code:-000} cdn_ip=${cdn_ip:-none}"
    if [ "$cdn_ok" -ne 1 ]; then
        drift+=('cdn-fallback')
    fi
fi

echo '==listeners=='
probe_listening() {
    # $1 = tcp|udp, $2 = port. `ss` prints the local address in column 4; require
    # the port to be a trailing ":<port>" so ":443" cannot also match ":4433",
    # and require the address to be at least as long as the needle: when it is
    # shorter (e.g. "*:443" vs a ":22752" probe) index() answers 0 while the
    # suffix position computes to 0 too, and 0 == 0 would report any port up.
    # Explicit branches instead of a computed -Hl"$1"n: under an if-then this
    # form is the one already proven in this fleet's wrappers.
    if [ "$1" = 'udp' ]; then
        ss -Hlun 2>/dev/null | awk -v p=":$2" 'length($4) >= length(p) && index($4, p) == length($4) - length(p) + 1 { f = 1 } END { exit(f ? 0 : 1) }'
    else
        ss -Hltn 2>/dev/null | awk -v p=":$2" 'length($4) >= length(p) && index($4, p) == length($4) - length(p) + 1 { f = 1 } END { exit(f ? 0 : 1) }'
    fi
}
for probe in tcp:443 tcp:21470 tcp:22752 tcp:33387 tcp:12378 udp:443 "udp:$OBFS_PORT"; do
    proto="${probe%%:*}"
    port="${probe##*:}"
    if probe_listening "$proto" "$port"; then
        echo "listener_${proto}_${port}=up"
    else
        echo "listener_${proto}_${port}=down"
    fi
done

if [ "${#drift[@]}" -eq 0 ]; then
    echo 'HARDENING_DRIFT=NONE'
    exit 0
fi
echo "HARDENING_DRIFT=$(IFS=,; echo "${drift[*]}")"
exit 3
'@.Replace("__OBFS_PORT__", "$ObfsPort").Replace("__EXPECTED_MASQUERADE_URL__", $MasqueradeUrl).Replace("__EXPECTED_REALITY_HOST__", $realityHost).Replace("__CDN_HOST__", $cdnHost)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "hardening-drift"
