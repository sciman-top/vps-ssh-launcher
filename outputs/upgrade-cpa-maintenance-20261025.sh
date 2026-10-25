#!/usr/bin/env bash
# One authorized maintenance window, restoring nginx on every exit.
set -Eeuo pipefail
umask 077
BK="/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-v8042-ingress-window"
mkdir -m 700 "$BK"
export CPA_WINDOW_BACKUP="$BK"
printf '%s' '__CPA_HOST_SCRIPT_BASE64__' | base64 -d > "$BK/host.sh"
printf '%s' '__CPA_PLUGIN_SCRIPT_BASE64__' | base64 -d > "$BK/plugin.sh"
bash -n "$BK/host.sh"
bash -n "$BK/plugin.sh"
restore_ingress() {
  local code=$? restore_code=0
  trap - EXIT HUP INT TERM
  python3 - <<'PY' || restore_code=$?
import json, os, shutil
from pathlib import Path
bk = Path(os.environ['CPA_WINDOW_BACKUP'])
manifest = bk / 'nginx.json'
if manifest.exists():
    for name, backup in json.loads(manifest.read_text()).items():
        p = Path(name)
        original = Path(backup).read_bytes()
        patched = original.replace(b'proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;', b'add_header Retry-After 30 always;\n        return 503; # CPA_TEMP_MAINTENANCE_20261025\n        proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;')
        current = p.read_bytes()
        if current == patched or current == original:
            shutil.copy2(backup, name)
        else:
            marker = b'add_header Retry-After 30 always;\n        return 503; # CPA_TEMP_MAINTENANCE_20261025\n        '
            assert current.count(marker) == 1, 'REFUSE concurrent ingress drift needs review'
            p.write_bytes(current.replace(marker, b''))
            print('INGRESS_RESTORE_PRESERVED_CONCURRENT_EDIT')
PY
  if [[ "$restore_code" == 0 ]]; then
    nginx -t && nginx -s reload || restore_code=$?
  fi
  if [[ "$restore_code" != 0 ]]; then
    echo "INGRESS_RESTORE_FAILED backup=$BK"
    exit "$restore_code"
  fi
  echo "INGRESS_RESTORED backup=$BK"
  exit "$code"
}
trap restore_ingress EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
python3 - <<'PY'
import json, os, re, shutil, subprocess
from pathlib import Path
bk = Path(os.environ['CPA_WINDOW_BACKUP'])
dump = subprocess.run(['nginx','-T'], capture_output=True, text=True, check=True).stdout
current = None
files = set()
needle = b'proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;'
for line in dump.splitlines():
    m = re.match(r'# configuration file (.+):$', line)
    if m:
        current = m.group(1)
    if needle.decode() in line:
        assert current
        files.add(current)
assert files == {'/etc/nginx/conf.d/cpa-gateway.conf', '/etc/nginx/conf.d/subscribe.conf'}, 'REFUSE ingress drift'
manifest = {}
for i, name in enumerate(sorted(files)):
    p = Path(name)
    assert p.read_bytes().count(needle) == 1
    dest = bk / f'nginx-{i}.conf'
    shutil.copy2(name, dest)
    manifest[name] = str(dest)
(bk / 'nginx.json').write_text(json.dumps(manifest))
for name in sorted(files):
    p = Path(name)
    p.write_bytes(p.read_bytes().replace(needle, b'add_header Retry-After 30 always;\n        return 503; # CPA_TEMP_MAINTENANCE_20261025\n        ' + needle))
print('INGRESS_MAINTENANCE_PREPARED paths=2 retry_after=30')
PY
nginx -t
nginx -s reload
echo "INGRESS_MAINTENANCE_ACTIVE backup=$BK"
sleep 2
bash "$BK/host.sh"
bash "$BK/plugin.sh"
echo 'JOINT_UPGRADE_PASS'
