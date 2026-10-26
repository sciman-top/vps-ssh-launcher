#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
python3 - <<'PY'
import re, os, yaml
from pathlib import Path

p = Path('/opt/cliproxyapi/config.yaml')
before = yaml.safe_load(p.read_text())
section = before['plugins']['configs']['workbuddy']
assert isinstance(section, dict) and section.get('enabled') is True, 'section shape'
assert 'account_max_concurrent' not in section, 'already present'

s = p.read_text()
anchor = re.compile(r'^(?P<indent>[ \t]*)checkin_auto:[ \t]*true[ \t]*$', re.M)
hits = anchor.findall(s)
assert len(hits) == 1, f'checkin_auto anchor not unique: {len(hits)}'
m = anchor.search(s)
line = f"{m.group('indent')}account_max_concurrent: 1\n"
out = s[:m.start()] + line + s[m.start():]

# Atomic replace preserving mode/owner.
tmp = p.with_name('config.yaml.amc.new')
tmp.write_text(out)
os.chmod(tmp, p.stat().st_mode & 0o777)
os.replace(tmp, p)

after = yaml.safe_load(p.read_text())
target = after['plugins']['configs']['workbuddy']
assert target.get('account_max_concurrent') == 1, 'insert missed target section'
expected = dict(before['plugins']['configs']['workbuddy'])
expected['account_max_concurrent'] = 1
assert target == expected, 'unexpected side keys changed'
assert after.keys() == before.keys(), 'top-level keys changed'
print('CONFIG_AMC1_OK key=account_max_concurrent value=1 section=plugins.configs.workbuddy')
PY
'@
# Backup runs first and captures the PRE-edit file.
$preCommand = "set -Eeuo pipefail; umask 077; BK=/opt/cliproxyapi/backups/`$(date -u +%Y%m%dT%H%M%S.%NZ)-config-pre-amc1; mkdir -m 700 `"`$BK`"; cp -a /opt/cliproxyapi/config.yaml `"`$BK/`"; echo BACKUP_DIR=`"`$BK`""
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $preCommand -IdleTimeoutSeconds 60 -HardTimeoutSeconds 180
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 90 -HardTimeoutSeconds 240
