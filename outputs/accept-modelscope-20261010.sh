#!/usr/bin/env bash
# 2026-10-10 controlled host-side A/B acceptance for the model-scoped capacity
# breaker (see docs/change-evidence/20261010-bwg-admission-model-scoped-capacity-breaker.md).
#
# Two generations of cpa-admission.py run against one fake upstream inside a
# single throwaway network namespace, so both keep the reviewed production bind
# contract (listen 127.0.0.1:8318, upstream 127.0.0.1:8317) without touching the
# live service, the live CPA, or the live journal.
#   NEW = /opt/cliproxyapi/cpa-admission.py            (deployed, model-scoped)
#   OLD = <backup>/cpa-admission.py.old                (previous, lane-scoped)
#
# The fake upstream answers the model-capacity marker for gpt-6-luna only and a
# normal 200 for every other model, in two upstream shapes:
#   marker       -> 200 + marker, no Retry-After (streak-driven window)
#   marker_retry -> 200 + marker + "Retry-After: 600" (immediate window)
# The second shape is the production one on 2026-10-10: a long upstream
# Retry-After is what made the sibling model fail with a bare 429 instead of
# being queued. Read-only with respect to /opt/cliproxyapi and
# /etc/vps-ssh-launcher; everything happens under /tmp/cpa-ms-accept.
set -Eeuo pipefail

BACKUP=/root/cpa-admission-modelscope-backup-20261010T041140Z
DEPLOYED=/opt/cliproxyapi/cpa-admission.py
DEPLOYED_CFG=/opt/cliproxyapi/cpa-admission.json
OLD_SCRIPT="$BACKUP/cpa-admission.py.old"
OLD_CFG="$BACKUP/cpa-admission.json.old"
WORK=/tmp/cpa-ms-accept
RUN=/tmp/cpa-ms-driver.sh
UPSTREAM_LOG=/tmp/cpa-ms-upstream.log

export BACKUP DEPLOYED DEPLOYED_CFG OLD_SCRIPT OLD_CFG WORK

echo "== preflight: generations =="
ls -l "$BACKUP"
sha256sum "$DEPLOYED" "$DEPLOYED_CFG" "$OLD_SCRIPT" "$OLD_CFG"
echo "old_gen_model_scope_hits=$(grep -c 'model_capacity_markers' "$OLD_SCRIPT" || true)"
echo "old_gen_cooldown_scope_hits=$(grep -c 'cooldown_scope' "$OLD_SCRIPT" || true)"
echo "new_gen_model_scope_hits=$(grep -c 'model_capacity_markers' "$DEPLOYED" || true)"
echo "new_gen_cooldown_scope_hits=$(grep -c 'cooldown_scope' "$DEPLOYED" || true)"

echo "== preflight: tooling =="
command -v unshare && echo "TOOL_unshare=ok"
command -v ip && echo "TOOL_ip=ok"
command -v curl && echo "TOOL_curl=ok"
command -v python3 && echo "TOOL_python3=ok"
echo "production_service=$(systemctl is-active cpa-admission.service || true)"
echo "leftover_temp=$(ls -d "$WORK" 2>/dev/null || echo absent)"
echo "leftover_proc=$(pgrep -af cpa-ms-accept || echo none)"

echo "== preflight: chatgpt-oauth lane config, both generations =="
python3 - <<'PY'
import json
import os

for label, path in (
    ("OLD", os.environ["OLD_CFG"]),
    ("NEW", os.environ["DEPLOYED_CFG"]),
):
    data = json.load(open(path, encoding="utf-8"))
    lane = next(l for l in data["lanes"] if l["name"] == "chatgpt-oauth")
    print(
        label,
        "listen",
        data["listen_host"],
        data["listen_port"],
        "upstream",
        data["upstream_host"],
        data["upstream_port"],
    )
    print(label, "models", lane["models"])
    print(label, "capacity_markers", lane["capacity_markers"])
    print(label, "model_capacity_markers", lane.get("model_capacity_markers"))
PY

echo "== preflight: production healthz (host namespace) =="
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz > /tmp/cpa-ms-prod.json < /dev/null
python3 - <<'PY'
import json

h = json.load(open("/tmp/cpa-ms-prod.json", encoding="utf-8"))
print("PROD_STATUS=" + str(h.get("status")))
for name, lane in sorted(h.get("lanes", {}).items()):
    state = lane.get("state", {})
    print(
        "PROD_LANE",
        name,
        "inflight=",
        state.get("inflight"),
        "cooldown_scope=",
        state.get("cooldown_scope"),
        "cooldown_remaining=",
        state.get("cooldown_remaining"),
        "model_cooldowns=",
        state.get("model_cooldowns"),
    )
PY
rm -f /tmp/cpa-ms-prod.json

rm -rf "$WORK"
mkdir -m 700 "$WORK"
rm -f "$UPSTREAM_LOG"

cat > "$WORK/fake_upstream.py" <<'PY'
import http.server
import json
import socketserver

LOG = "/tmp/cpa-ms-upstream.log"
MODE = "/tmp/cpa-ms-accept/upstream.mode"
MARKER = b'{"error":{"type":"capacity_error","message":"Selected model is at capacity"}}'
OK = b'{"output_text":"ok"}'


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            model = json.loads(raw).get("model")
        except Exception:
            model = None
        try:
            mode = open(MODE, encoding="utf-8").read().strip()
        except OSError:
            mode = "marker"
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (mode, model))
        capacity = model == "gpt-6-luna"
        body = MARKER if capacity else OK
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if capacity and mode == "marker_retry":
            self.send_header("Retry-After", "600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


Server(("127.0.0.1", 8317), Handler).serve_forever()
PY

cat > "$RUN" <<'INNER'
set -Eeuo pipefail

WORK=${WORK:?}
DEPLOYED=${DEPLOYED:?}
DEPLOYED_CFG=${DEPLOYED_CFG:?}
OLD_SCRIPT=${OLD_SCRIPT:?}
OLD_CFG=${OLD_CFG:?}

ASSERT_FAILURES=0

require() {
  local label=$1
  local actual=$2
  local expected=$3
  if [ "$actual" = "$expected" ]; then
    echo "ASSERT_OK ${label} '${actual}'"
  else
    echo "ASSERT_FAIL ${label} expected='${expected}' actual='${actual}'"
    ASSERT_FAILURES=$((ASSERT_FAILURES + 1))
  fi
}

require_gt1() {
  local label=$1
  local actual=$2
  case "$actual" in
    ''|*[!0-9]*) ok=0 ;;
    *) if [ "$actual" -gt 1 ]; then ok=1; else ok=0; fi ;;
  esac
  if [ "$ok" = 1 ]; then
    echo "ASSERT_OK ${label} '${actual}' (>1)"
  else
    echo "ASSERT_FAIL ${label} expected='>1' actual='${actual}'"
    ASSERT_FAILURES=$((ASSERT_FAILURES + 1))
  fi
}

ip link set lo up
echo "NETNS_LO=up"

ADM_PID=""
UP_PID=""
cleanup() {
  for pid in "$ADM_PID" "$UP_PID"; do
    [ -n "$pid" ] || continue
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  rm -rf "$WORK"
}
trap cleanup EXIT

python3 "$WORK/fake_upstream.py" > "$WORK/upstream.out" 2>&1 < /dev/null &
UP_PID=$!

up_ready=0
for _ in $(seq 1 20); do
  if python3 -c 'import socket;socket.create_connection(("127.0.0.1",8317),1).close()' < /dev/null 2>/dev/null; then
    up_ready=1
    break
  fi
  sleep 1
done
echo "UPSTREAM_READY=$up_ready"
if [ "$up_ready" != 1 ]; then
  cat "$WORK/upstream.out"
  exit 1
fi

call() {
  local model=$1 port=$2 code reason
  : > "$WORK/last_body"
  : > "$WORK/last_hdr"
  code=$(curl --noproxy '*' -sS -o "$WORK/last_body" -D "$WORK/last_hdr" \
    --max-time 40 -H 'Content-Type: application/json' \
    -X POST "http://127.0.0.1:$port/v1/responses" \
    -d "{\"model\":\"$model\",\"input\":\"hello\"}" -w '%{http_code}' < /dev/null 2>/dev/null || echo 000)
  reason=$(grep -i '^x-cpa-admission-reason:' "$WORK/last_hdr" 2>/dev/null |
    tr -d '\r' | awk '{print $2}' | tail -n 1 || true)
  printf '%s %s' "$code" "${reason:--}"
}

retry_after_seen() {
  grep -i '^retry-after:' "$WORK/last_hdr" 2>/dev/null |
    tr -d '\r' | awk '{print $2}' | tail -n 1 || true
}

dump_healthz() {
  # Separate statements: `local a=$1 b=...$a` expands the later word before the
  # earlier assignment, which `set -u` rejects as an unbound variable.
  local label=$1
  local port=$2
  local tag=$3
  local file="$WORK/${label}.${tag}.json"
  curl --noproxy '*' -fsS --max-time 5 "http://127.0.0.1:$port/healthz" > "$file" < /dev/null
  python3 - "$label" "$tag" "$file" <<'PY'
import json
import sys

label, tag, path = sys.argv[1], sys.argv[2], sys.argv[3]
health = json.load(open(path, encoding="utf-8"))
for name, lane in sorted(health.get("lanes", {}).items()):
    state = lane.get("state", {})
    print(
        label,
        tag,
        "LANE",
        name,
        "inflight=",
        state.get("inflight"),
        "cooldown_active=",
        state.get("cooldown_active"),
        "cooldown_remaining=",
        state.get("cooldown_remaining"),
        "cooldown_scope=",
        state.get("cooldown_scope"),
        "model_cooldowns=",
        state.get("model_cooldowns"),
        "failure_streak=",
        state.get("failure_streak"),
    )
PY
}

expect_for() {
  case "$1" in
    NEW_marker) echo "200 -|200 -|429 model_cooldown|200 -" ;;
    NEW_marker_retry) echo "200 -|429 model_cooldown|429 model_cooldown|200 -" ;;
    OLD_marker) echo "200 -|200 -|200 -|200 -" ;;
    OLD_marker_retry) echo "200 -|429 cooldown|429 cooldown|429 cooldown" ;;
    *) echo "|-|-|-" ;;
  esac
}

run_generation() {
  local label=$1
  local script=$2
  local cfg=$3
  local port=$4
  local mode=$5
  local probe_wait=$6
  local ready=0
  local expect e1 e2 e3 e4 c1 c2 c3 c4 ra
  expect=$(expect_for "$label")
  IFS='|' read -r e1 e2 e3 e4 <<< "$expect"
  echo "== ${label}: script=$script cfg=$cfg listen=$port upstream_mode=$mode =="
  echo "${label}_EXPECT case1='${e1}' case2='${e2}' case3='${e3}' case4='${e4}'"
  printf '%s\n' "$mode" > "$WORK/upstream.mode"
  ADM_PID=""
  python3 "$script" "$cfg" > "$WORK/$label.admission.out" 2>&1 < /dev/null &
  ADM_PID=$!
  for _ in $(seq 1 20); do
    if curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$port/healthz" > /dev/null 2>&1 < /dev/null; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" != 1 ]; then
    echo "${label}_START=FAIL"
    tail -n 20 "$WORK/$label.admission.out"
    kill "$ADM_PID" 2>/dev/null || true
    wait "$ADM_PID" 2>/dev/null || true
    ADM_PID=""
    ASSERT_FAILURES=$((ASSERT_FAILURES + 1))
    return 0
  fi
  echo "${label}_PID=$ADM_PID"
  c1=$(call gpt-6-luna "$port")
  echo "${label}_CASE1_LUNA=$c1"
  echo "${label}_CASE1_BODY=$(head -c 140 "$WORK/last_body")"
  require "${label}_CASE1" "$c1" "$e1"
  c2=$(call gpt-6-luna "$port")
  echo "${label}_CASE2_LUNA=$c2"
  echo "${label}_CASE2_RETRY_AFTER=$(retry_after_seen)"
  require "${label}_CASE2" "$c2" "$e2"
  c3=$(call gpt-6-luna "$port")
  ra=$(retry_after_seen)
  echo "${label}_CASE3_LUNA=$c3"
  echo "${label}_CASE3_RETRY_AFTER=$ra"
  echo "${label}_CASE3_BODY=$(head -c 200 "$WORK/last_body")"
  require "${label}_CASE3" "$c3" "$e3"
  if [ "${c3%% *}" = "429" ]; then
    require_gt1 "${label}_CASE3_RETRY_AFTER" "$ra"
  fi
  c4=$(call gpt-6.1-sol "$port")
  echo "${label}_CASE4_SOL=$c4"
  echo "${label}_CASE4_BODY=$(head -c 200 "$WORK/last_body")"
  require "${label}_CASE4" "$c4" "$e4"
  dump_healthz "$label" "$port" after-four
  if [ "$probe_wait" = 1 ]; then
    sleep 11
    echo "${label}_CASE5_LUNA_AFTER_11S=$(call gpt-6-luna "$port")"
    echo "${label}_CASE5_BODY=$(head -c 140 "$WORK/last_body")"
    dump_healthz "$label" "$port" after-probe
  fi
  echo "== ${label} admission journal tail =="
  tail -n 16 "$WORK/$label.admission.out"
  kill "$ADM_PID" 2>/dev/null || true
  wait "$ADM_PID" 2>/dev/null || true
  ADM_PID=""
  return 0
}

echo "== B1: NEW, marker only: a streak-built model window must not block the sibling =="
run_generation NEW_marker "$DEPLOYED" "$DEPLOYED_CFG" 8318 marker 1

echo "== B2: NEW, marker + Retry-After 600 (production shape): sibling stays served =="
run_generation NEW_marker_retry "$DEPLOYED" "$DEPLOYED_CFG" 8318 marker_retry 0

echo "== A1: OLD, marker only: lane window opens and queues the sibling behind the probe =="
run_generation OLD_marker "$OLD_SCRIPT" "$OLD_CFG" 8318 marker 0

echo "== A2: OLD, marker + Retry-After 600: the sibling is refused with a lane 429 =="
run_generation OLD_marker_retry "$OLD_SCRIPT" "$OLD_CFG" 8318 marker_retry 0

echo "ASSERT_FAILURES=$ASSERT_FAILURES"
if [ "$ASSERT_FAILURES" != 0 ]; then
  exit 1
fi
INNER

chmod 700 "$RUN"

echo "== A/B inside one throwaway network namespace =="
AB_FAIL=0
if unshare -n bash "$RUN" < /dev/null; then
  echo "AB_RESULT=PASS"
else
  echo "AB_RESULT=FAIL"
  AB_FAIL=1
fi

echo "== post-check: no stray temp process or directory =="
echo "stray_proc=$(pgrep -af cpa-ms-accept || echo none)"
if ls -d "$WORK" 2>/dev/null; then
  echo "TEMP_DIR_PRESENT"
else
  echo "TEMP_DIR_REMOVED"
fi
rm -rf "$WORK" "$RUN"

echo "== post-check: upstream request log (mode, model) =="
cat "$UPSTREAM_LOG" 2>/dev/null || echo "UPSTREAM_LOG_ABSENT"
rm -f "$UPSTREAM_LOG"

echo "== post-check: production admission still serving =="
echo "production_service=$(systemctl is-active cpa-admission.service || true)"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz > /tmp/cpa-ms-prod2.json < /dev/null
python3 - <<'PY'
import json

h = json.load(open("/tmp/cpa-ms-prod2.json", encoding="utf-8"))
print("PROD_STATUS=" + str(h.get("status")))
for name, lane in sorted(h.get("lanes", {}).items()):
    state = lane.get("state", {})
    print(
        "PROD_LANE",
        name,
        "inflight=",
        state.get("inflight"),
        "cooldown_scope=",
        state.get("cooldown_scope"),
        "model_cooldowns=",
        state.get("model_cooldowns"),
    )
PY
rm -f /tmp/cpa-ms-prod2.json

echo "ACCEPT_WRAPPER_EXIT=$AB_FAIL"
exit "$AB_FAIL"
