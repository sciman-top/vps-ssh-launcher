#requires -Version 7
# Read-only audit for copies of current WorkBuddy access tokens outside the
# production auth files. Only aggregate counts and paths are emitted; tokens
# are never printed. This script does not modify the host or credentials.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
printf 'AUDIT_STARTED_UTC=' 
date -u +%FT%TZ
PRODUCTION_ROOT=/opt/cliproxyapi
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

production_root = Path(os.environ.get('PRODUCTION_ROOT', '/opt/cliproxyapi'))
auth_root = production_root
auth_dir = auth_root / 'auth'
search_roots = ((Path('/root'), Path('/var/tmp'), Path('/tmp'))
                if str(production_root) == '/opt/cliproxyapi'
                else (auth_root / 'backups', Path('/tmp')))
needles = {}
for path in auth_dir.glob('*.json'):
    if not path.is_file() or path.name.startswith('.'):
        continue
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        continue
    auth = data.get('auth') if isinstance(data, dict) else None
    if not isinstance(auth, dict):
        continue
    domain = str(auth.get('domain', '')).lower()
    region = str(auth.get('region', '')).lower()
    if 'codebuddy' not in domain and region not in ('cn', 'intl', 'global'):
        continue
    for key in ('accessToken', 'refreshToken'):
        token = auth.get(key)
        if isinstance(token, str) and len(token) >= 20:
            needles[token.encode()] = key

files_scanned = 0
matches = []
for root in search_roots:
    for path in root.rglob('*'):
        try:
            if not path.is_file() or path.stat().st_size > 4_000_000:
                continue
            body = path.read_bytes()
        except (OSError, PermissionError):
            continue
        files_scanned += 1
        hit_keys = sorted({needles[token] for token in needles if token in body})
        if hit_keys:
            matches.append((str(path), ','.join(hit_keys)))

print(f'TOKEN_KINDS={",".join(sorted(set(needles.values())))} tokens={len(needles)}')
print(f'FILES_SCANNED={files_scanned} FILES_WITH_CURRENT_TOKEN={len(matches)}')
for path, kinds in sorted(matches):
    print(f'MATCH_PATH={path} KINDS={kinds}')
print('AUDIT_RESULT=' + ('COPIES_FOUND' if matches else 'NO_CURRENT_TOKEN_COPIES'))
PY
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 90 -HardTimeoutSeconds 180
