#requires -Version 7
# Read-only image-retention probes. Does not remove images, prune or restart.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$launcherPython = Resolve-ProjectPython -ProjectRoot $repoRoot
$launcherConfig = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
echo '=== backup inventory modes'
printf 'backup_root='; stat -c 'mode=%a owner=%U type=%F' "$DIR/backups"
printf 'breakdown='; stat -c '%a %U %F' "$DIR/backups"
find "$DIR/backups" -mindepth 1 -maxdepth 1 -printf '%y %f\n' \
  | awk '{count[$1]++} END {for (k in count) print k, count[k]}' | sort
echo '=== backup inventory function (deployed)'
python3 - "$DIR/auto-update.sh" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
def extract(name):
    start = source.index(name + '() {')
    depth = 0
    for index in range(start, len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise SystemExit('unterminated ' + name)
print(extract('retained_backup_paths'))
PY
echo '=== latest rollback record identities'
for d in $(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d -name '*-from-v[0-9]*' -printf '%T@ %p\n' \
  | sort -rn | head -8 | cut -d' ' -f2-); do
  label=${d##*/}
  image=$(cat "$d/image-id" 2>/dev/null || true)
  ref=$(awk '/^[[:space:]]*image:/ {print $2; exit}' "$d/compose.yml" 2>/dev/null || true)
  printf 'backup=%s image=%s ref=%s\n' "$label" "${image:-missing}" "${ref:-missing}"
done
echo '=== image deletion candidates (read-only)'
python3 - "$DIR/auto-update.sh" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
def extract(name):
    start = source.index(name + '() {')
    depth = 0
    for index in range(start, len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise SystemExit('unterminated ' + name)
print(extract('image_candidates'))
PY
echo '=== retained backup path invocation (no deletion)'
find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -rn | head -8 | while IFS= read -r row; do
      p=${row#* }
      sha=$(cat "$p/image-id" 2>/dev/null || true)
      if [[ "$sha" =~ ^sha256:[0-9a-f]{64}$ ]] && docker image inspect "$sha" >/dev/null 2>&1; then
        echo "INVENTORY_ENTRY=ok image=$sha"
      else
        echo "INVENTORY_ENTRY=deferred path=$p"
      fi
    done
echo '=== verified checker invocation (read-only)'
verified_checker=$(mktemp /tmp/verified-checker-XXXXXXXX.sh)
trap 'rm -f -- "$verified_checker"' EXIT
cat > "$verified_checker" <<'CHECKER'
set -uo pipefail
image_id_file() { printf '%s/image-id\n' "$1"; }
CHECKER
python3 - "$DIR/auto-update.sh" <<'PY' >> "$verified_checker"
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
def extract(name):
    start = source.index(name + '() {')
    depth = 0
    for index in range(start, len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise SystemExit('unterminated ' + name)
print(extract('image_id_file'))
print(extract('retained_backup_paths'))
PY
cat >> "$verified_checker" <<'CHECKER'
count=0
while IFS= read -r -d "" p; do
  case "$p" in "$BACKUP_ROOT"/*-from-v*) count=$((count + 1));; esac
done < <(retained_backup_paths)
echo "VERIFIED_RETENTION_RC=$? COUNT=$count"
CHECKER
BACKUP_ROOT="$DIR/backups" RETENTION_KEEP_BACKUPS=8 bash "$verified_checker" --inspect-retention 2>&1 | tail -8
'@
Invoke-LauncherRemoteCommand -Python $launcherPython -ProjectRoot $repoRoot `
  -Config $launcherConfig -Profile bwg -Command $command `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
