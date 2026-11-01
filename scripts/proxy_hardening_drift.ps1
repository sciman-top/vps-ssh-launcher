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
# Both profiles carry a Cloudflare fallback lane since the zz parity work
# (bwg=cdn.sciman.top, zz=zzcdn.sciman.top); an empty host skips the section
# so each profile keeps reporting exactly the lanes it actually runs.
$cdnHost = @{ bwg = "cdn.sciman.top"; zz = "zzcdn.sciman.top" }[$Profile]
$mergedSub = "1"

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
MERGED_SUB="__MERGED_SUB__"
XRAY_CONF="${VPS_DRIFT_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_SOURCE_DIR="${VPS_DRIFT_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
NGINX_ROOT="${VPS_DRIFT_NGINX_ROOT:-/usr/share/nginx/html}"
NGINX_CONF_D="${VPS_DRIFT_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
DPKG_EXCLUDE_CONF="${VPS_DRIFT_DPKG_CONF:-/etc/dpkg/dpkg.cfg.d/vps-launcher-camouflage}"
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
# The stock pages are package payload: every nginx upgrade re-unpacks them and
# reverts the promotion (monthly maintenance upgraded nginx 1.31.x -> 1.31.8
# on 2026-11-01 and both hosts flipped back to "Welcome to nginx!" while every
# service stayed green). The camouflage lane therefore projects a dpkg
# path-exclude for exactly those files; without it the next upgrade silently
# undoes the lane again, so its absence is drift in its own right.
dpkg_hits=0
for exclude_line in \
    'path-exclude=/usr/share/nginx/html/index.html' \
    'path-exclude=/usr/share/nginx/html/50x.html' \
    'path-exclude=/usr/share/nginx/html/index.nginx-debian.html'; do
    if [ -f "$DPKG_EXCLUDE_CONF" ] && grep -Fqx -- "$exclude_line" "$DPKG_EXCLUDE_CONF"; then
        dpkg_hits=$((dpkg_hits + 1))
    fi
done
case "$dpkg_hits" in
    3) echo 'dpkg_exclude=present' ;;
    0) echo 'dpkg_exclude=missing' ;;
    *) echo 'dpkg_exclude=partial' ;;
esac
if [ "$dpkg_hits" -ne 3 ]; then
    cam_state=drift
fi
# Template-distribution residue (PSD sources, theme zip bundles, macOS
# __MACOSX sidecars, and the builder's Dockerfile/README/manifest/service-worker
# files) is a hand-assembled-decoy tell that an active prober exposes by
# requesting a raw asset path. A vasma reinstall re-fetches the decoy template
# and brings this back, so its reappearance is drift and re-running the
# camouflage lane quarantines it. Top-level only, and never when a served page
# references the item, so live content that ships a theme asset is not flagged.
residue_drift=0
while IFS= read -r item; do
    [ -n "$item" ] || continue
    rel="${item#"$NGINX_ROOT/"}"
    base="$(basename "$item")"
    # PSD sources, archiver/thumbnail sidecars and the builder `check` marker
    # are never served content, so a textual "reference" to them cannot make
    # them live: report and move on (prose containing the word "check" must
    # not protect the 0-byte marker file).
    case "$item" in
        *.psd|*.PSD|*/.DS_Store|*/Thumbs.db|*/__MACOSX|*/check|*/.|*/._*)
            echo "camouflage=residue file=$rel"
            residue_drift=1
            continue ;;
    esac
    if grep -rIl --exclude-dir=s -- "$rel" "$NGINX_ROOT"/*.html "$NGINX_ROOT"/en 2>/dev/null | head -n1 | grep -q . ||
       grep -rIl --exclude-dir=s -- "$base" "$NGINX_ROOT"/*.html "$NGINX_ROOT"/en 2>/dev/null | head -n1 | grep -q .; then
        continue
    fi
    echo "camouflage=residue file=$rel"
    residue_drift=1
done < <(find "$NGINX_ROOT" -path "$NGINX_ROOT/s" -prune -o \
    \( -iname '*.psd' -o -iname '*.zip' -o -iname 'README.md' \
       -o -iname 'Dockerfile' -o -iname 'manifest.json' \
       -o -iname 'precache-manifest.*' -o -iname '*.worker.js' \
       -o -iname '.DS_Store' -o -iname 'Thumbs.db' -o -name '._*' \
       -o -iname '__MACOSX' -o -iname 'psd' -o -iname 'check' \) -print 2>/dev/null)
if [ "$residue_drift" -eq 1 ]; then
    cam_state=drift
fi
# The camouflage page alone is not enough: an unknown path must answer with the
# site's own branded 404, not nginx's stock "<center>nginx</center>" error page.
# That default page is an active-probe tell, and a reinstall that drops the
# http-level error_page drop-in restores it while every service stays green.
if [ -f "$NGINX_ROOT/404.html" ] &&
   [ -f "$NGINX_CONF_D/vps-launcher-camouflage-errors.conf" ] &&
   grep -qE 'error_page[[:space:]]+404([[:space:]]+=[0-9]+)?[[:space:]]+/404\.html' \
       "$NGINX_CONF_D/vps-launcher-camouflage-errors.conf" 2>/dev/null; then
    echo 'camouflage=404-branded'
else
    echo 'camouflage=404-stock-tell'
    cam_state=drift
fi
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
front_state="$(python3 - "$XRAY_CONF" "$NGINX_CONF_D/vps-launcher-xhttp-front.conf" "$NGINX_CONF_D/vps-launcher-zz-xhttp-front.conf" <<'PY'
import glob
import json
import os
import re
import subprocess
import sys

conf_dir = sys.argv[1]
# bwg and zz name the same managed front differently; use whichever exists.
target = next((p for p in sys.argv[2:] if os.path.isfile(p)), sys.argv[2])
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
stream = entry.get('streamSettings') or {}
xhttp = stream.get('xhttpSettings') or {}
inner_path = xhttp.get('path')
inner_sni = (stream.get('tlsSettings') or {}).get('serverName') or xhttp.get('host') or ''
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
conf_cert = None
match = re.search(r'ssl_certificate\s+(\S+);', conf_text)
if match:
    conf_cert = match.group(1)
conf_sni = None
match = re.search(r'server_name\s+([^;]+);', conf_text)
if match:
    conf_sni = match.group(1).strip()
cert_ok = False
if conf_cert and inner_sni and conf_sni == inner_sni and os.path.isfile(conf_cert):
    try:
        result = subprocess.run(
            ['openssl', 'x509', '-in', conf_cert, '-noout', '-checkhost', inner_sni],
            capture_output=True, text=True, check=False,
        )
        cert_ok = result.returncode == 0 and 'does match' in result.stdout
    except OSError:
        cert_ok = False

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
    and cert_ok
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

echo '==reality-alt-sni=='
ALT_MARKER='/etc/v2ray-agent/.vps-launcher-alt-sni'
ALT_VHOST='/etc/nginx/conf.d/vps-launcher-alt-sni.conf'
if [ ! -f "$ALT_MARKER" ]; then
    echo 'reality_alt_sni=not-applied'
else
    alt_name="$(head -1 "$ALT_MARKER" 2>/dev/null | tr -d '[:space:]')"
    echo "alt_sni_name=${alt_name:-unset}"
    alt_missing="$(python3 - "$XRAY_CONF" "$alt_name" <<'PY'
import glob
import json
import os
import sys

xray_dir, alt = sys.argv[1:3]
total = 0
missing = 0
for path in sorted(glob.glob(os.path.join(xray_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        total += 1
        if alt not in (rs.get('serverNames') or []):
            missing += 1
print('%d/%d' % (total, missing))
PY
)" || alt_missing='0/0'
    # vasma regenerates its REALITY files in place and silently drops unknown
    # serverNames; the marker records that this lane is expected on the host,
    # so a regenerated config is drift instead of a quiet downgrade to a
    # single-SNI fleet.
    if [ -z "$alt_name" ] || [ "${alt_missing%%/*}" = '0' ] || [ "${alt_missing##*/}" != '0' ]; then
        echo "reality_alt_sni=drift missing=$alt_missing"
        drift+=('reality-alt-sni')
    else
        echo "reality_alt_sni=ok missing=$alt_missing"
    fi
    if grep -qs "server_name $alt_name;" "$ALT_VHOST" && nginx -T 2>/dev/null | grep -qs "server_name $alt_name;"; then
        echo 'alt_vhost=ok'
    else
        echo "alt_vhost=missing name=$alt_name"
        drift+=('reality-alt-sni')
    fi
fi

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
runtime_config="${VPS_DRIFT_SB_CONF_DIR:-/etc/v2ray-agent/sing-box/conf}/config.json"
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
    fragment_state="$(python3 - "$fragment" \
        "$runtime_config" "$OBFS_PORT" <<'PY'
import json
import sys

def configured_hy2(path, port):
    data = json.load(open(path, encoding='utf-8'))
    found = [
        ib for ib in data.get('inbounds', []) or []
        if ib.get('type') == 'hysteria2' and ib.get('listen_port') == port
    ]
    if len(found) != 1:
        return None
    ib = found[0]
    obfs = ib.get('obfs') or {}
    if obfs.get('type') != 'salamander' or not obfs.get('password'):
        return None
    if not ib.get('users') or ((ib.get('tls') or {}).get('enabled') is not True):
        return None
    return (ib.get('users') or [{}])[0].get('password'), obfs.get('password')

source_password = runtime_password = ''
try:
    port = int(sys.argv[3])
    source = configured_hy2(sys.argv[1], port)
    runtime = configured_hy2(sys.argv[2], port)
except Exception:
    raise SystemExit(2)
if source is not None:
    source_password = source[1]
if runtime is not None:
    runtime_password = runtime[1]
if source is not None and runtime is not None and source == runtime:
    print('obfs_contract=ok')
elif source is None:
    print('obfs_contract=source-bad')
elif runtime is None:
    print('obfs_contract=runtime-missing')
else:
    print('obfs_contract=runtime-password-drift')
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
    cdn_cert="$(sed -n 's/^\s*ssl_certificate\s\+\([^;]*\);/\1/p' "$cdn_conf" 2>/dev/null | head -1 || true)"
    cdn_server_name="$(sed -n 's/^\s*server_name\s\+\([^;]*\);/\1/p' "$cdn_conf" 2>/dev/null | head -1 || true)"
    cdn_cert_ok=0
    if [ -n "$cdn_cert" ] && [ "$cdn_server_name" = "$CDN_HOST" ] &&
       [ -f "$cdn_cert" ] &&
       openssl x509 -in "$cdn_cert" -noout -checkhost "$CDN_HOST" 2>/dev/null |
       grep -q 'does match'; then
        cdn_cert_ok=1
    fi
    if [ -f "$cdn_conf" ] &&
        grep -q 'listen 2087 ssl;' "$cdn_conf" &&
        grep -q 'proxy_pass http://127.0.0.1:45989;' "$cdn_conf" &&
        grep -q 'proxy_set_header Upgrade \$http_upgrade;' "$cdn_conf" &&
        [ "$cdn_cert_ok" -eq 1 ]; then
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

echo '==merged-sub=='
if [ -z "$MERGED_SUB" ]; then
    echo 'merged_sub=not-applicable'
else
    merged_ok=1
    token_file="${VPS_DRIFT_MERGED_TOKEN_FILE:-/var/lib/vps-ssh-launcher/merged-sub.token}"
    merged_root="${VPS_DRIFT_MERGED_ROOT:-/usr/share/nginx/html}/s/merged"
    token=""
    [ -f "$token_file" ] && token="$(cat "$token_file" 2>/dev/null || true)"
    merged_file="$merged_root/${token:-none}"
    live_uuid="$(python3 - "$XRAY_CONF" <<'PY_MERGED'
import glob
import json
import os
import sys

for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for entry in data.get('inbounds', []) or []:
        stream = entry.get('streamSettings') or {}
        if entry.get('protocol') != 'vless' or (stream.get('security') != 'reality'):
            continue
        for client in (entry.get('settings') or {}).get('clients') or []:
            if client.get('id'):
                print(client['id'])
                raise SystemExit(0)
PY_MERGED
)"
    nodes=0
    file_uuid=""
    if [ -n "$token" ] && [ -f "$merged_file" ]; then
        nodes="$(base64 -d < "$merged_file" 2>/dev/null | grep -cE '^(vless|hysteria2)://' || true)"
        file_uuid="$(base64 -d < "$merged_file" 2>/dev/null |
            sed -n 's/^vless:\/\/\([0-9a-f-]\{36\}\)@.*/\1/p' | head -1)"
    fi
    merged_nodes="$nodes"
    # The published list carries the alternate-hostname node as well, so the
    # floor is the six lanes; the exact count follows what the host runs.
    alt_expected=""
    [ -f /etc/v2ray-agent/.vps-launcher-alt-sni ] && \
        alt_expected="$(head -1 /etc/v2ray-agent/.vps-launcher-alt-sni | tr -d '[:space:]')"
    alt_node=1
    if [ -n "$alt_expected" ] && [ -f "$merged_file" ]; then
        base64 -d < "$merged_file" 2>/dev/null | grep -q "sni=$alt_expected" || alt_node=0
    fi
    expected_nodes="$(python3 - "$XRAY_CONF" "$SB_SOURCE_DIR" "$NGINX_CONF_D" <<'PY_COUNT'
import glob
import json
import os
import sys

count = 0
front_count = 0
for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        stream = ib.get('streamSettings') or {}
        clients = (ib.get('settings') or {}).get('clients') or []
        if ib.get('protocol') == 'vless' and clients:
            if (stream.get('network') or 'tcp') == 'tcp' and stream.get('security') == 'reality':
                count += 2 if len(stream.get('realitySettings', {}).get('serverNames') or []) > 1 else 1
            elif stream.get('network') == 'xhttp' and stream.get('security') == 'reality':
                count += 1
            elif stream.get('network') == 'xhttp' and stream.get('security') != 'reality' and str(ib.get('listen') or '') in ('127.0.0.1', '::1', 'localhost'):
                # The inner loopback XHTTP inbound is published through nginx
                # only when one of the managed front files exists.
                front_count += 1
for path in sorted(glob.glob(os.path.join(sys.argv[2], '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        if ib.get('type') == 'hysteria2' and ib.get('users'):
            count += 1
if front_count == 1 and any(
    os.path.isfile(os.path.join(sys.argv[3], name))
    for name in ('vps-launcher-xhttp-front.conf', 'vps-launcher-zz-xhttp-front.conf')
):
    count += 1
if os.path.isfile(os.path.join(sys.argv[3], 'vps-launcher-cdn.conf')):
    count += 1
print(count)
PY_COUNT
)" || expected_nodes=0
    if [ "$nodes" = "$expected_nodes" ] && [ -n "$live_uuid" ] && [ "$file_uuid" = "$live_uuid" ] && [ "$alt_node" -eq 1 ]; then
        merged_state='ok'
    else
        # A vasma reinstall can rotate the UUID or the Hysteria2 password; the
        # published list then hands every device credentials that no longer
        # authenticate, so a mismatch has to surface as drift.
        merged_state='stale'
        merged_ok=0
    fi
    echo "merged_sub=$merged_state nodes=${nodes:-0}"
    if [ "$merged_ok" -ne 1 ]; then
        drift+=('merged-sub')
    fi
fi

echo '==merged-sub-fetch=='
if [ -z "$MERGED_SUB" ]; then
    echo 'merged_sub_fetch=not-applicable'
else
    merged_token_file="${VPS_DRIFT_MERGED_TOKEN_FILE:-/var/lib/vps-ssh-launcher/merged-sub.token}"
    merged_token=""
    [ -f "$merged_token_file" ] && merged_token="$(cat "$merged_token_file" 2>/dev/null || true)"
    direct_port="$(python3 - "$NGINX_CONF_D" "$EXPECTED_MASQUERADE_URL" <<'PY'
import glob
import re
import sys

want_host = sys.argv[2].split('://', 1)[-1].split('/', 1)[0].split(':', 1)[0]
for path in sorted(glob.glob(sys.argv[1] + '/*.conf')):
    try:
        text = open(path, encoding='utf-8').read()
    except OSError:
        continue
    if 'ssl_reject_handshake' in text and re.search(r'^[ \t]*server_name[ \t]+' + re.escape(want_host) + r';', text, re.M):
        match = re.search(r'listen\s+(?:[\d.]+:)?(\d+)\s+ssl;', text)
        if match:
            print(match.group(1))
            raise SystemExit(0)
print('')
PY
)"
    fetch_code="$(curl -sk -m 12 --resolve "$EXPECTED_REALITY_HOST:${direct_port:-443}:127.0.0.1" \
        -o /dev/null -w '%{http_code}' \
        "https://$EXPECTED_REALITY_HOST:${direct_port:-443}/s/merged/$merged_token" 2>/dev/null || true)"
    import_file="$(mktemp)"
    curl -sk -m 12 --resolve "$EXPECTED_REALITY_HOST:${direct_port:-443}:127.0.0.1" \
        -o "$import_file" "https://$EXPECTED_REALITY_HOST:${direct_port:-443}/s/merged/$merged_token" 2>/dev/null || true
    # The base64 subscription may wrap, so decode it in Python instead of
    # relying on the local base64(1) flavor.
    import_state="$(python3 - "$nodes" "$import_file" "$merged_root/${merged_token:-none}" <<'PY_IMPORT'
import base64
import sys

try:
    want_nodes = int(sys.argv[1])
except ValueError:
    want_nodes = -1
try:
    encoded = open(sys.argv[2], encoding='ascii').read().strip()
    payload = base64.b64decode(encoded, validate=False).decode('utf-8', errors='replace')
except (OSError, ValueError):
    try:
        encoded = open(sys.argv[3], encoding='ascii').read().strip()
        payload = base64.b64decode(encoded, validate=False).decode('utf-8', errors='replace')
    except (OSError, ValueError):
        payload = ''
links = [line for line in payload.splitlines() if line.startswith(("vless://", "hysteria2://"))]
if not links:
    links = [
        line
        for line in open(sys.argv[2], encoding='utf-8', errors='replace')
        .read().splitlines()
        if line.startswith(("vless://", "hysteria2://"))
    ]
obfs = [line for line in links if line.startswith("hysteria2://") and "obfs=salamander" in line]
fields = obfs[0].split("://", 1)[1].split("@", 1) if obfs else []
if not links or len(links) != want_nodes:
    print("incomplete")
elif not obfs or len(obfs) != 1 or len(fields) != 2 or not fields[0]:
    print("obfs-credential")
else:
    print("ok")
PY_IMPORT
)" || import_state=invalid
    # mktemp created this file. Truncate it rather than deleting it so this
    # read-only gate keeps its no-filesystem-delete contract.
    : > "$import_file"
    unset import_file
    # A bare prefix count accepts a Hy2 URL whose userinfo password vanished
    # during import. Require the userinfo half and one salamander endpoint.
    if [ "$fetch_code" = '200' ] && [ "$import_state" = 'ok' ]; then
        echo "merged_sub_fetch=ok http=200 imported=$import_state"
    else
        echo "merged_sub_fetch=drift http=${fetch_code:-000} imported=${import_state:-invalid}"
        drift+=('merged-sub-fetch')
    fi
fi

echo '==vasma-sub=='
# The merged list is the primary URL, but vasma's own /s/default/<md5> is still
# handed to clients that imported it before the merge. zz republishes that
# file from live configs; writing only subscribe_local left the served copy on
# the retired 22752 node while the lane reported "applied".
served_dir="${VPS_DRIFT_SERVED_SUB_DIR:-/etc/v2ray-agent/subscribe/default}"
vision_port="$(python3 - "$XRAY_CONF" <<'PY_SERVED'
import glob
import json
import os
import sys

front = {}
inbounds = []
for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for ib in data.get('inbounds', []) or []:
        settings = ib.get('settings') or {}
        if (
            ib.get('protocol') == 'dokodemo-door'
            and str(settings.get('address')) in ('127.0.0.1', 'localhost')
        ):
            front[settings.get('port')] = ib.get('port')
        inbounds.append(ib)
for ib in inbounds:
    stream = ib.get('streamSettings') or {}
    if ib.get('protocol') != 'vless' or stream.get('security') != 'reality':
        continue
    if (stream.get('network') or 'tcp') != 'tcp':
        continue
    if not ((ib.get('settings') or {}).get('clients') or []):
        continue
    port = ib.get('port')
    print(front.get(port, port))
    raise SystemExit(0)
raise SystemExit(3)
PY_SERVED
)" || vision_port=""
if [ ! -d "$served_dir" ]; then
    echo 'vasma_sub=not-applicable'
else
    served_ok=0
    served_seen=0
    served_checked=0
    for served_file in "$served_dir"/*; do
        [ -f "$served_file" ] || continue
        served_seen=$((served_seen + 1))
        body="$(base64 -d < "$served_file" 2>/dev/null || true)"
        [ -n "$body" ] || continue
        served_checked=$((served_checked + 1))
        # The published TCP REALITY node must dial the live wrapper port.
        if [ -z "$vision_port" ] ||
           ! printf '%s\n' "$body" |
               grep -qE "^vless://[^#]*@[^:]+:${vision_port}\?[^#]*security=reality[^#]*type=tcp"; then
            continue
        fi
        # When an nginx-fronted XHTTP lane exists, the served list must carry
        # its TLS node; a Vision-only list hides the missing lane.
        if [ -f "$NGINX_CONF_D/vps-launcher-xhttp-front.conf" ] ||
           [ -f "$NGINX_CONF_D/vps-launcher-zz-xhttp-front.conf" ]; then
            printf '%s\n' "$body" |
                grep -qE '^vless://[^#]*security=tls[^#]*type=xhttp' || continue
        fi
        served_ok=1
    done
    echo "vasma_sub_served=${served_seen} checked=${served_checked} vision_port=${vision_port:-none} state=$([ "$served_ok" -eq 1 ] && echo ok || echo stale)"
    if [ "$served_ok" -ne 1 ]; then
        drift+=('vasma-sub')
    fi
fi

echo '==tls-expiry=='
# Every TLS lane (REALITY fallback, XHTTP front, CDN origin, Hysteria2) reads
# one of these files. Renewal can fail silently for weeks; the CDN lane is the
# last one that survives an origin-IP block, so an expiring certificate is a
# lane outage rather than a cosmetic warning.
expiry_bad=0
expiry_rows=0
while IFS= read -r cert_path; do
    [ -n "$cert_path" ] || continue
    case "$cert_path" in
        /*) ;;
        *) continue ;;
    esac
    expiry_rows=$((expiry_rows + 1))
    not_after="$(openssl x509 -in "$cert_path" -noout -enddate 2>/dev/null | sed 's/^notAfter=//')"
    if [ ! -f "$cert_path" ]; then
        echo "tls_cert=missing file=$cert_path"
        expiry_bad=1
    elif openssl x509 -in "$cert_path" -noout -checkend 1209600 >/dev/null 2>&1; then
        echo "tls_cert=ok file=$cert_path not_after=$not_after"
    else
        echo "tls_cert=expiring file=$cert_path not_after=$not_after"
        expiry_bad=1
    fi
done < <(
    nginx -T 2>/dev/null |
        sed -n 's/^[[:space:]]*ssl_certificate[[:space:]]\+\([^;]*\);.*/\1/p' |
        tr -d '"' | sort -u
)
if [ "$expiry_rows" -eq 0 ]; then
    echo 'tls_cert=none'
    expiry_bad=1
fi
if [ "$expiry_bad" -eq 0 ]; then
    echo "tls_expiry=ok certs=$expiry_rows"
else
    echo "tls_expiry=drift certs=$expiry_rows"
    drift+=('tls-expiry')
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

echo '==stale-firewall=='
if [ "$EXPECTED_REALITY_HOST" = 'fq.sciman.top' ]; then
    # ArgoX was temporary on the zz host. A matching named rule on another
    # host is not evidence of the same disabled service, so do not claim it.
    echo 'stale_firewall=not-applicable'
else
    # ArgoX was a temporary Cloudflare tunnel, not part of the proxy stack.
    # vasma's iptables table and the persisted file used to allow the named
    # 30000-30010/8080 rows after its unit was disabled. Match only those named
    # rules, never infer that arbitrary firewall ACCEPT rows are safe to remove.
    stale_firewall_rows="$(iptables-save -t filter 2>/dev/null |
        grep -F -- '-A INPUT' |
        grep -F -- '-j ACCEPT' |
        grep -E -- '--dport (3000[0-9]|30010|8080)( |$)' |
        grep -F -- '--comment "ArgoX PORT ' || true)"
    stale_firewall_count=0
    [ -n "$stale_firewall_rows" ] && stale_firewall_count="$(printf '%s\n' "$stale_firewall_rows" | wc -l | tr -d '[:space:]')"
    echo "stale_firewall_rules=$stale_firewall_count"
    if [ "$stale_firewall_rows" = '' ]; then
        echo 'stale_firewall=none'
    else
        printf '%s\n' "$stale_firewall_rows" | sed 's/^/stale_firewall_row=/'
        echo 'stale_firewall=argo-leftover'
        drift+=('stale-firewall')
    fi
fi

if [ "${#drift[@]}" -eq 0 ]; then
    echo 'HARDENING_DRIFT=NONE'
    exit 0
fi
echo "HARDENING_DRIFT=$(IFS=,; echo "${drift[*]}")"
exit 3
'@.Replace("__OBFS_PORT__", "$ObfsPort").Replace("__EXPECTED_MASQUERADE_URL__", $MasqueradeUrl).Replace("__EXPECTED_REALITY_HOST__", $realityHost).Replace("__CDN_HOST__", $cdnHost).Replace("__MERGED_SUB__", $mergedSub)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "hardening-drift"
