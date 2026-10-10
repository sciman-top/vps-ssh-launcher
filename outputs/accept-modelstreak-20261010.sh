#!/usr/bin/env bash
# 2026-10-10 controlled host-side A/B acceptance for the per-model streak on
# lane-scoped capacity refusals (see the modelstreak change-evidence doc).
#
# Production shape being reproduced: upstream sheds are model-correlated even
# though the signal text is account-level ("server_is_overloaded"). The fake
# upstream answers gpt-6-luna with a 200 + lane marker (no Retry-After) and
# every other model with a normal 200. The driven sequence is mixed traffic:
#   luna fail -> sol success -> luna fail -> luna again -> sol again
# Expected split:
#   OLD (lane-streak only): the sibling's success keeps resetting the lane
#     streak, so luna rides a full upstream round trip on every attempt and
#     the 3rd luna refusal finally opens the lane and the trailing sol is
#     refused with it (collateral 429).
#   NEW (per-model streak): the 3rd luna request is answered locally with
#     reason=model_cooldown without touching the upstream, the lane stays
#     open, and the trailing sol is served normally.
# Two generations of cpa-admission.py run against one fake upstream inside a
# single throwaway network namespace; the live service, CPA and journal are
# untouched. Everything happens under /tmp/cpa-msa-accept.
set -Eeuo pipefail

DEPLOYED=/opt/cliproxyapi/cpa-admission.py
DEPLOYED_CFG=/opt/cliproxyapi/cpa-admission.json
# The 20261010T115147Z backup is the parallel-session transaction that rotated
# the production generation to the modelstreak file; its .old side was verified
# to be exactly the previous generation (183fdd1f...).
OLD_BACKUP=/root/cpa-admission-modelscope-backup-20261010T115147Z
if [ ! -f "$OLD_BACKUP/cpa-admission.py.old" ]; then
  echo "REFUSE old generation backup not found at $OLD_BACKUP"
  exit 2
fi
OLD_SHA=$(sha256sum "$OLD_BACKUP/cpa-admission.py.old" | cut -d' ' -f1)
if [ "$OLD_SHA" != "183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6" ]; then
  echo "REFUSE backup .old is not the previous generation: $OLD_SHA"
  exit 2
fi
OLD_SCRIPT="$OLD_BACKUP/cpa-admission.py.old"
OLD_CFG="$OLD_BACKUP/cpa-admission.json.old"
WORK=/tmp/cpa-msa-accept
RUN=/tmp/cpa-msa-driver.sh
UPSTREAM_LOG=/tmp/cpa-msa-upstream.log

export OLD_BACKUP DEPLOYED DEPLOYED_CFG OLD_SCRIPT OLD_CFG WORK UPSTREAM_LOG

echo "== preflight: generations =="
sha256sum "$DEPLOYED" "$DEPLOYED_CFG" "$OLD_SCRIPT" "$OLD_CFG"
echo "new_gen_streak_comment=$(grep -c 'model-correlated' "$DEPLOYED" || true)"
echo "old_gen_streak_comment=$(grep -c 'model-correlated' "$OLD_SCRIPT" || true)"

echo "== preflight: tooling =="
command -v unshare && echo "TOOL_unshare=ok"
command -v curl && echo "TOOL_curl=ok"
echo "production_service=$(systemctl is-active cpa-admission.service || true)"

rm -rf "$WORK"
mkdir -m 700 "$WORK"
rm -f "$UPSTREAM_LOG"

cat > "$WORK/fake_upstream.py" <<'PY'
import http.server
import json
import socketserver

LOG = "/tmp/cpa-msa-upstream.log"
MARKER = b'{"error":{"type":"upstream_error","message":"upstream execution failed: server_is_overloaded"}}'
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
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s\n" % model)
        capacity = model == "gpt-6-luna"
        body = MARKER if capacity else OK
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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

dump_healthz() {
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
    #        luna#1   sol#1    luna#2   luna#3           sol#2
    # OLD CASE4 hits lane streak 2 -> lane cooldown; CASE5 sol queues inside
    # the cooldown budget and leaves as the recovery probe (~10s), so it still
    # returns 200 -- the discriminator is CASE4 plus the upstream hit count.
    NEW_lanemarker) echo "200 -|200 -|200 -|429 model_cooldown|200 -" ;;
    OLD_lanemarker) echo "200 -|200 -|200 -|200 -|200 -" ;;
    *) echo "|-|-|-|-|" ;;
  esac
}

run_generation() {
  local label=$1
  local script=$2
  local cfg=$3
  local port=$4
  local expected_hits=$5
  local ready=0
  local expect e1 e2 e3 e4 e5 c1 c2 c3 c4 c5 hits
  expect=$(expect_for "$label")
  IFS='|' read -r e1 e2 e3 e4 e5 <<< "$expect"
  echo "== ${label}: script=$script listen=$port =="
  echo "${label}_EXPECT luna1='${e1}' sol1='${e2}' luna2='${e3}' luna3='${e4}' sol2='${e5}'"
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
  require "${label}_CASE1" "$c1" "$e1"
  c2=$(call gpt-6.1-sol "$port")
  echo "${label}_CASE2_SOL=$c2"
  require "${label}_CASE2" "$c2" "$e2"
  c3=$(call gpt-6-luna "$port")
  echo "${label}_CASE3_LUNA=$c3"
  require "${label}_CASE3" "$c3" "$e3"
  c4=$(call gpt-6-luna "$port")
  echo "${label}_CASE4_LUNA=$c4"
  echo "${label}_CASE4_BODY=$(head -c 160 "$WORK/last_body")"
  require "${label}_CASE4" "$c4" "$e4"
  c5=$(call gpt-6.1-sol "$port")
  echo "${label}_CASE5_SOL=$c5"
  require "${label}_CASE5" "$c5" "$e5"
  hits=$(grep -c 'gpt-6-luna' "$UPSTREAM_LOG" 2>/dev/null || true)
  echo "${label}_UPSTREAM_LUNA_HITS=$hits"
  require "${label}_UPSTREAM_LUNA_HITS" "$hits" "$expected_hits"
  dump_healthz "$label" "$port" after-five
  echo "== ${label} upstream hits per model =="
  sort "$UPSTREAM_LOG" | uniq -c || true
  echo "== ${label} admission journal tail =="
  tail -n 14 "$WORK/$label.admission.out"
  kill "$ADM_PID" 2>/dev/null || true
  wait "$ADM_PID" 2>/dev/null || true
  ADM_PID=""
  return 0
}

echo "== A: OLD generation, mixed sequence: luna keeps riding upstream (3 hits), sol recovers as the queued probe =="
: > "$UPSTREAM_LOG"
run_generation OLD_lanemarker "$OLD_SCRIPT" "$OLD_CFG" 8318 3

echo "== B: NEW generation, same sequence: luna parked locally after 2 hits, sol stays served =="
: > "$UPSTREAM_LOG"
run_generation NEW_lanemarker "$DEPLOYED" "$DEPLOYED_CFG" 8318 2

echo "== upstream totals (OLD then NEW) =="
cat "$UPSTREAM_LOG" 2>/dev/null | sort | uniq -c || true

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
echo "stray_proc=$(pgrep -af cpa-msa-accept || echo none)"
if ls -d "$WORK" 2>/dev/null; then
  echo "TEMP_DIR_PRESENT"
else
  echo "TEMP_DIR_REMOVED"
fi
rm -rf "$WORK" "$RUN"

echo "== post-check: upstream request log =="
cat "$UPSTREAM_LOG" 2>/dev/null || echo "UPSTREAM_LOG_ABSENT"
rm -f "$UPSTREAM_LOG"

echo "== post-check: production admission still serving =="
echo "production_service=$(systemctl is-active cpa-admission.service || true)"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz > /tmp/cpa-msa-prod.json < /dev/null
python3 - <<'PY'
import json

h = json.load(open("/tmp/cpa-msa-prod.json", encoding="utf-8"))
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
rm -f /tmp/cpa-msa-prod2.json 2>/dev/null || true
rm -f /tmp/cpa-msa-prod.json

echo "ACCEPT_WRAPPER_EXIT=$AB_FAIL"
exit "$AB_FAIL"
