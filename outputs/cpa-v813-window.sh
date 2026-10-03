#!/usr/bin/env bash
# v8.0.13 canary gate-window: wait (bounded) for the slot-1 ai.input.im upstream
# to recover, let CPA re-register the declared models, then run the same
# generation gate the updater uses before invoking the canary transaction.
# Read-only until the gate passes; never mutates the image on its own.
set -u
DIR=/opt/cliproxyapi
LOG=$DIR/v813-window.log
ATTEMPTS=${1:-24}
INTERVAL=${2:-300}
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

probe_upstream() {
  python3 - <<'PY'
import json, urllib.request, urllib.error, yaml
cfg = yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
p = [x for x in cfg['openai-compatibility'] if 'ai.input.im' in str(x.get('base-url',''))][0]
k = p['api-key-entries'][0]['api-key']
base = p['base-url'].rstrip('/')
ok = fail = 0
for model in ('deepseek-v4.1-flash', 'gpt-6-astra', 'gpt-6.1-sol'):
    b = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': 'Reply OK'}], 'max_tokens': 16}).encode()
    rq = urllib.request.Request(base + '/chat/completions', data=b,
                                headers={'Authorization': 'Bearer ' + k, 'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(rq, timeout=45)
        ok += 1
    except Exception:
        fail += 1
print('PROBE_OK=%d PROBE_FAIL=%d' % (ok, fail))
PY
}

gate_ok() {
  # The updater gate: catalog complete AND the non-OAuth generation target answers.
  python3 "$DIR/cpa-health.py" generation >/dev/null 2>&1
}

log "WINDOW_START attempts=$ATTEMPTS interval=$INTERVAL target=v8.0.13"
for i in $(seq 1 "$ATTEMPTS"); do
  P=$(probe_upstream)
  log "attempt=$i $P"
  if gate_ok; then
    log "GATE_OPEN at attempt=$i"
    bash /root/cpa-canary-v813.sh
    rc=$?
    log "CANARY_EXIT=$rc"
    exit $rc
  fi
  if [ "$i" -lt "$ATTEMPTS" ]; then sleep "$INTERVAL"; fi
done
log "WINDOW_TIMEOUT after $ATTEMPTS attempts; image unchanged"
exit 42
