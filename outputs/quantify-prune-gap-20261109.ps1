#requires -Version 7
# Quantify what the old tag-based protection would have deleted.
#
# Section B of verify-restore-and-prune showed protected_old=1 vs
# protected_new=54. That gap is the whole point of the fix, but "54" alone
# does not say whether those 53 images are still on disk. This lists them.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
python3 - <<'PY'
import subprocess
from pathlib import Path

base = Path('/opt/cliproxyapi/backups')
present, absent = [], []
for d in sorted(p for p in base.iterdir() if p.is_dir()):
    rec = d / 'image-id'
    if not rec.is_file():
        continue
    val = rec.read_text().strip()
    if not val.startswith('sha256:'):
        continue
    rc = subprocess.run(['docker','image','inspect',val],
                        capture_output=True, text=True)
    (present if rc.returncode == 0 else absent).append((d.name, val))
print('recorded_total=%d still_present=%d gone=%d'
      % (len(present)+len(absent), len(present), len(absent)))
print('--- images the NEW logic protects that are on disk (sample)')
for name, val in present[:6]:
    print('  %s  %s' % (val[:19], name[:52]))
if absent:
    print('--- recorded but gone (should be zero after backfill)')
    for name, val in absent[:6]:
        print('  %s  %s' % (val[:19], name[:52]))
PY

echo
echo '=== how many on-disk cpa images would the OLD logic have deleted?'
python3 - <<'PY'
import re, subprocess
from pathlib import Path
base = Path('/opt/cliproxyapi/backups')
def short(x):
    return x.replace('sha256:','')[:12]
running = short(subprocess.run(['docker','inspect','--format','{{.Image}}','cli-proxy-api'],
                               capture_output=True, text=True).stdout.strip())
old = {running}
for d in sorted(p for p in base.iterdir() if p.is_dir()):
    c = d / 'compose.yml'
    if not c.is_file():
        continue
    try:
        import yaml
        ref = yaml.safe_load(c.read_text())['services']['cli-proxy-api']['image']
    except Exception:
        continue
    out = subprocess.run(['docker','image','inspect','--format','{{.ID}}',ref],
                         capture_output=True, text=True).stdout.strip()
    if out:
        old.add(short(out))
listing = subprocess.run(['docker','images','--format','{{.ID}} {{.Repository}}:{{.Tag}}'],
                         capture_output=True, text=True).stdout.splitlines()
cpa = [l for l in listing if 'cli-proxy-api' in l]
todelete = [l for l in cpa if short(l.split()[0]) not in old]
print('cpa_images_on_disk=%d old_logic_would_delete=%d' % (len(cpa), len(todelete)))
for line in todelete[:8]:
    print('  ' + line[:70])
PY
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 600
