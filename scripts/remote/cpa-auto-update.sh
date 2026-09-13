#!/usr/bin/env bash
# BWG's existing timer entrypoint: mature releases only, one smoke, rollback.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/auto-update.log"
exec 9>"$DIR/auto-update.lock"
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 1; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
MODE=${1:---apply}
[[ "$MODE" == --check || "$MODE" == --apply ]] || exit 2

# Select the highest semver present in both official releases and Docker Hub,
# aged at least 72h in both. A new release must not starve mature updates.
SELECTION=$(python3 - "$DIR/compose.yml" <<'PY'
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'bwg-cpa-updater'})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

def version(tag):
    return tuple(map(int, tag[1:].split('.')))

def mature(timestamp):
    when = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() >= 259200

text = Path(sys.argv[1]).read_text()
matches = re.findall(r'image:\s*eceasy/cli-proxy-api:(v\d+\.\d+\.\d+)(?:@sha256:[0-9a-f]{64})?\s*(?:\n|$)', text)
if len(matches) != 1:
    raise SystemExit('REFUSE unexpected image declaration')
current = matches[0]
releases = fetch('https://api.github.com/repos/router-for-me/CLIProxyAPI/releases?per_page=100')
eligible = {r['tag_name'] for r in releases if not r['draft'] and not r['prerelease']
            and re.fullmatch(r'v\d+\.\d+\.\d+', r['tag_name']) and mature(r['published_at'])}
tags = fetch('https://hub.docker.com/v2/repositories/eceasy/cli-proxy-api/tags?page_size=100')['results']
candidates = [t for t in tags if t['name'] in eligible and mature(t['last_updated'])
              and version(t['name']) > version(current)
              and re.fullmatch(r'sha256:[0-9a-f]{64}', t.get('digest', ''))]
if candidates:
    chosen = max(candidates, key=lambda t: version(t['name']))
    print(current, chosen['name'], chosen['digest'])
else:
    print(current, current, '-')
PY
)
read -r CUR TARGET DIGEST <<<"$SELECTION"
log "CANDIDATE current=$CUR target=$TARGET soak=72h"
[[ "$MODE" == --apply ]] || exit 0
health() { python3 "$DIR/cpa-health.py" "$1"; }
if [[ "$CUR" == "$TARGET" ]]; then
  health generation
  log "OK: no newer mature release; current=$CUR health verified"
  exit 0
fi
if ! health generation; then
  log 'DEFER: pre-update health unavailable; image unchanged'
  exit 1
fi

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%SZ)-from-$CUR"
mkdir -m 700 -p "$BK"
cp -a "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/cpa-health.py" "$BK/"
mkdir -m 700 "$BK/auth"
find "$DIR/auth" -maxdepth 1 -type f \( -name '*.json' -o -name '*.cds' \) -exec cp -a -t "$BK/auth" {} +
# The old local image stays available; never prune images or auth backups here.
docker image inspect "$(python3 - "$DIR/compose.yml" <<'PY'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])
PY
)" >/dev/null
MUTATED=0
rollback() {
  local code=$?
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml"
    if (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) && health readiness; then
      log "ROLLBACK restored=$CUR backup=$BK"
    else
      log "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  [[ "$code" != 0 ]] || code=1
  exit "$code"
}
trap rollback ERR INT TERM
IMAGE="eceasy/cli-proxy-api:$TARGET@$DIGEST"
docker pull "$IMAGE" >>"$LOG" 2>&1
docker image inspect "$IMAGE" >/dev/null
MUTATED=1
python3 - "$DIR/compose.yml" "$IMAGE" <<'PY'
import os, re, sys
from pathlib import Path
p = Path(sys.argv[1])
text, n = re.subn(r'image:\s*eceasy/cli-proxy-api:[^\s]+', 'image: '+sys.argv[2], p.read_text())
if n != 1:
    raise SystemExit('REFUSE image replacement count')
tmp = p.with_suffix('.yml.new')
tmp.write_text(text)
os.chmod(tmp, p.stat().st_mode & 0o777)
os.replace(tmp, p)
PY
(cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never >>"$LOG" 2>&1)
RESULT=0
health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  log 'WAIT: upstream unavailable; one recheck after 65s'
  sleep 65
  RESULT=0
  health generation || RESULT=$?
fi
if [[ "$RESULT" == 10 ]]; then
  # Keep a locally healthy image; report uncertainty instead of restart churn.
  health readiness
  trap - ERR INT TERM
  log "UNVERIFIED: upstream unavailable after update; retained=$TARGET backup=$BK"
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR INT TERM
log "OK: updated $CUR -> $TARGET digest=$DIGEST backup=$BK"
