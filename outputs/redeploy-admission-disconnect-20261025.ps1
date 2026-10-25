#requires -Version 7
param(
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$candidateBytes = [IO.File]::ReadAllBytes(
    (Join-Path $repoRoot 'scripts/remote/cpa-admission.py')
)
$candidateText = [Text.Encoding]::UTF8.GetString($candidateBytes).Replace(
    "`r`n", "`n"
)
$candidateBytes = [Text.Encoding]::UTF8.GetBytes($candidateText)
$sha = [Security.Cryptography.SHA256]::HashData($candidateBytes)
$newPin = [Convert]::ToHexString($sha).ToLowerInvariant()
$expectedPin = (Get-Content -LiteralPath (Join-Path $repoRoot 'scripts/remote/cpa-admission-integrity-pin.txt') -Raw).Trim()
if ($expectedPin -ne $newPin) {
    throw 'Candidate source does not match the reviewed repository integrity pin.'
}
$payload = [Convert]::ToBase64String($candidateBytes)
$mode = if ($DryRun) { 'dry-run' } else { 'apply-paused' }
$hostTransport = (
    Get-Content (Join-Path $PSScriptRoot 'deploy-admission-disconnect-20261025.sh') -Raw
).Replace("`r`n", "`n")
for ($index = 0; $index -lt 2; $index++) {
    $hostTransport = $hostTransport.
        Replace('__OLD_PIN__', '621847118304eb7b5206ac994c01c72bd0d885a2d9718c4e40b8ca64cfd78623').
        Replace('__NEW_PIN__', $newPin).
        Replace('__PAYLOAD__', $payload).
        Replace('__MODE__', $mode)
}
if ($hostTransport -match '__[A-Z_]+__') {
    throw 'Unfilled admission transaction placeholder.'
}

$remoteWrapper = @'
#!/usr/bin/env bash
# Apply only the reviewed admission code/pin. Existing requests drain first.
set -Eeuo pipefail
umask 077
BK="/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-ingress-window"
mkdir -m 700 "$BK"
export CPA_WINDOW_BACKUP="$BK"
printf '%s' '__HOST_TRANSPORT__' | base64 -d > "$BK/admission.sh"
bash -n "$BK/admission.sh"
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
bash "$BK/admission.sh"
echo 'ADMISSION_WINDOW_PASS'
'@
$remoteWrapper = $remoteWrapper.Replace(
    '__HOST_TRANSPORT__',
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($hostTransport))
)
if ($remoteWrapper -match '__[A-Z_]+__') {
    throw 'Unfilled ingress transaction placeholder.'
}

if ($DryRun) {
    Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
      -Config $config -Profile bwg -Command $hostTransport `
      -IdleTimeoutSeconds 120 -HardTimeoutSeconds 180
    Write-Output 'DRY_RUN_OK no_ingress_or_admission_mutation'
    return
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $remoteWrapper `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 600
