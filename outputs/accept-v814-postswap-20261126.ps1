#requires -Version 7
# 2026-11-26 post-swap acceptance for the v8.1.4 fork generation. Sequential
# loopback single-shots through admission (127.0.0.1:8318), each gated on lane
# health before and after; a 429/5xx or an unexpected signal stops the run
# with no retry. Keys and tokens are never echoed. Read-only apart from the
# probe requests themselves.
param(
  [switch]$SkipParallel
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$parallel = if ($SkipParallel) { 'skip' } else { 'run' }
$command = @'
set -u
set -o pipefail
umask 077
ROOT=$(mktemp -d /root/v814-accept-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
PARALLEL='__PARALLEL__'
API_KEY=$(python3 -c 'import yaml,pathlib;print(yaml.safe_load(pathlib.Path("/opt/cliproxyapi/config.yaml").read_text())["access"]["api-keys"][0])')
lanes_healthy() {
  python3 - "$1" <<'PY'
import json, sys, urllib.request

lane_name = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    d = json.load(response)
st = (d.get("lanes", {}).get(lane_name) or {}).get("state", {})
if st.get("hold_reason") is not None or st.get("cooldown_active"):
    print("LANE_BLOCKED %s hold=%r cooldown=%s" % (lane_name, st.get("hold_reason"), st.get("cooldown_active")))
    raise SystemExit(4)
if st.get("inflight", 0) or st.get("pending", 0):
    print("LANE_BUSY %s inflight=%s pending=%s" % (lane_name, st.get("inflight"), st.get("pending")))
    raise SystemExit(5)
print("LANE_IDLE %s" % lane_name)
PY
}
# A hold or an active cooldown on the lane about to be probed aborts; a lane
# that is merely serving the user's own request is waited for, briefly.
gate() {
  if ! lanes_healthy "$2"; then
    rc=$?
    if [ "$rc" != 5 ]; then echo "ABORT=$1"; exit 4; fi
    waited=0
    while [ "$waited" -lt 90 ]; do
      sleep 10; waited=$((waited + 10))
      lanes_healthy "$2" && return 0
      rc=$?
      [ "$rc" = 5 ] || { echo "ABORT=$1"; exit 4; }
    done
    echo "ABORT=$1 lane_still_busy"
    exit 4
  fi
  return 0
}
# chat-completions streaming single shot
chat_single() {
  model="$1"; body="$2"; out="$ROOT/$model.body"
  st=$(curl --silent --show-error --max-time 180 --output "$out" --write-out "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
    --data "$body" http://127.0.0.1:8318/v1/chat/completions)
  tag=$(echo "$model" | tr '.-' '__' | tr 'a-z' 'A-Z')
  echo "${tag}_HTTP=$st"
  python3 - "$out" "$model" "$st" <<'PY'
import json, sys
path, model, status = sys.argv[1], sys.argv[2], sys.argv[3]
tag = model.upper().replace(".", "_").replace("-", "_")
text = open(path, encoding="utf-8", errors="replace").read()
if status != "200":
    print("%s_NON_200_BODY=%r" % (tag, text[:240]))
    raise SystemExit(6)
parts, terminal, served, calls = [], False, set(), {}
for line in text.splitlines():
    if not line.startswith("data: "):
        continue
    payload = line[6:].strip()
    if payload == "[DONE]":
        terminal = True
        continue
    try:
        ev = json.loads(payload)
    except ValueError:
        continue
    if ev.get("model"):
        served.add(str(ev["model"]))
    for choice in ev.get("choices") or []:
        for call in (choice.get("delta") or {}).get("tool_calls") or []:
            idx = call.get("index")
            fn = call.get("function") or {}
            slot = calls.setdefault(idx, {"deltas": 0, "args": "", "ids": set(), "names": set()})
            slot["deltas"] += 1
            slot["args"] += fn.get("arguments") or ""
            if call.get("id"):
                slot["ids"].add(call["id"])
            if fn.get("name"):
                slot["names"].add(fn["name"])
        parts.append(str((choice.get("delta") or {}).get("content") or ""))
print("%s_TERMINAL=%s TEXT=%r" % (tag, str(terminal).lower(), "".join(parts).strip()[:80]))
print("%s_SERVED_MODEL=%s" % (tag, ", ".join(sorted(served)) or "unknown"))
if served and model not in served:
    print("%s_SUBSTITUTED=YES" % tag)
if calls:
    ids = []
    for idx in sorted(calls):
        slot = calls[idx]
        ok = False
        try:
            json.loads(slot["args"])
            ok = True
        except ValueError:
            pass
        ids.extend(slot["ids"])
        print("%s_TOOL_CALL index=%s deltas=%d complete_json=%s name=%s args=%r" % (
            tag, idx, slot["deltas"], str(ok).lower(), ",".join(sorted(slot["names"])), slot["args"][:70]))
    print("%s_TOOL_INDICES=%s UNIQUE_IDS=%s" % (tag, sorted(calls), str(len(ids) == len(set(ids))).lower()))
for marker in ("VALIDATION_REQUIRED", "PERMISSION_DENIED", "operator_review_required", "TOS_VIOLATION", "server_is_overloaded", "repeat_rejected", "account_restricted"):
    if marker in text:
        print("%s_SIGNAL=%s" % (tag, marker))
PY
}
# responses non-stream single shot
responses_single() {
  model="$1"; input="$2"; extra="$3"; out="$ROOT/$model.rbody"
  st=$(curl --silent --show-error --max-time 180 --output "$out" --write-out "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
    --data "{\"model\":\"$model\",\"stream\":false,\"store\":false,\"max_output_tokens\":256,\"input\":$input$extra}" \
    http://127.0.0.1:8318/v1/responses)
  tag=$(echo "$model" | tr '.-' '__' | tr 'a-z' 'A-Z')
  echo "${tag}_HTTP=$st"
  python3 - "$out" "$model" "$st" <<'PY'
import json, sys
path, model, status = sys.argv[1], sys.argv[2], sys.argv[3]
tag = model.upper().replace(".", "_").replace("-", "_")
text = open(path, encoding="utf-8", errors="replace").read()
try:
    d = json.loads(text)
except ValueError:
    print("%s_NON_JSON=%r" % (tag, text[:240]))
    raise SystemExit(6)
if status != "200" or d.get("error"):
    err = d.get("error") or {}
    print("%s_ERROR type=%r code=%r message=%r" % (tag, err.get("type"), err.get("code"), str(err.get("message"))[:160]))
    raise SystemExit(6)
out_text = "".join(
    part.get("text", "")
    for item in d.get("output") or [] if isinstance(item, dict) and item.get("type") == "message"
    for part in item.get("content") or [] if isinstance(part, dict))
usage = d.get("usage") or {}
print("%s_STATUS=%s MODEL=%r TEXT=%r IN=%s CACHED=%s OUT=%s" % (
    tag, d.get("status"), d.get("model"), out_text.strip()[:60], usage.get("input_tokens"),
    (usage.get("input_tokens_details") or {}).get("cached_tokens"), usage.get("output_tokens")))
for marker in ("VALIDATION_REQUIRED", "PERMISSION_DENIED", "operator_review_required", "TOS_VIOLATION", "server_is_overloaded", "repeat_rejected", "11128", "account_restricted"):
    if marker in text:
        print("%s_SIGNAL=%s" % (tag, marker))
raise SystemExit(0 if d.get("status") == "completed" and out_text.strip() else 7)
PY
}
step() { echo "== $1"; }

step "runtime"
docker inspect cli-proxy-api --format 'IMAGE={{.Config.Image}} STARTED={{.State.StartedAt}} RESTARTS={{.RestartCount}}'
docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -c1-64 | sed 's/^/RUNTIME_BINARY_SHA=/'

gate lanes_not_idle_before_flash antigravity-oauth
step "probe 1/5: gemini-3.8-flash-high (antigravity lane, chat stream)"
chat_single "gemini-3.8-flash-high" '{"model":"gemini-3.8-flash-high","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}'
gate lanes_not_idle_after_flash antigravity-oauth

if [ "$PARALLEL" = run ]; then
  step "probe 1b: gemini-3.8-flash-high parallel tool calls (issue 7966 contract, upstream fe571afe)"
  chat_single "gemini-3.8-flash-high" '{"model":"gemini-3.8-flash-high","stream":true,"parallel_tool_calls":true,"tool_choice":"auto","messages":[{"role":"user","content":"Call get_time for the time zones UTC, America/Santiago and Asia/Tokyo. Make all three calls in the same turn; do not answer in text."}],"tools":[{"type":"function","function":{"name":"get_time","description":"Return the current time in a time zone.","parameters":{"type":"object","properties":{"tz":{"type":"string"}},"required":["tz"]}}}]}'
  gate lanes_not_idle_after_parallel antigravity-oauth
fi

gate lanes_before_deepseek workbuddy-oauth
step "probe 2/5: deepseek-v4.1-flash (workbuddy lane, plugin 0.21.31)"
chat_single "deepseek-v4.1-flash" '{"model":"deepseek-v4.1-flash","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}'
gate lanes_not_idle_after_deepseek workbuddy-oauth

gate lanes_before_glm54 workbuddy-oauth
step "probe 3/5: glm-5.4 (workbuddy lane, responses wire with a developer item)"
responses_single "glm-5.4" '[{"type":"message","role":"developer","content":[{"type":"input_text","text":"You are a terse assistant. Follow the user instruction exactly."}]},{"type":"message","role":"user","content":[{"type":"input_text","text":"Reply with exactly: GLM54_OK"}]}]' ''
gate lanes_not_idle_after_glm54 workbuddy-oauth

gate lanes_before_terra chatgpt-oauth
step "probe 4/5: gpt-6.1-terra (chatgpt lane, responses wire)"
responses_single "gpt-6.1-terra" '[{"type":"message","role":"user","content":[{"type":"input_text","text":"Reply with exactly: OK"}]}]' ',"reasoning":{"effort":"low"}'
gate lanes_not_idle_after_terra chatgpt-oauth

gate lanes_before_glm53flash zhipu-coding-plan
step "probe 5/5: glm-5.3-flash (zhipu coding plan, responses wire)"
responses_single "glm-5.3-flash" '[{"type":"message","role":"user","content":[{"type":"input_text","text":"Reply with exactly: OK"}]}]' ''
gate lanes_after_glm53flash zhipu-coding-plan

step "final lane states"
for lane in antigravity-oauth workbuddy-oauth chatgpt-oauth zhipu-coding-plan; do lanes_healthy "$lane" || true; done

step "journal: probe window upstream results"
journalctl -u cpa-admission --since "6 minutes ago" --no-pager -o cat 2>/dev/null | python3 -c '
import re, sys
for ln in sys.stdin:
    if "upstream_result" in ln or "protocol_normalized" in ln or "lane_reject" in ln or "lane_hold" in ln:
        m = re.search(r"(upstream_result lane=\S+ model=\S+ status=\S+ capacity=\S+)|((?:antigravity_\w+|agent_identity_neutralized|codex_agent_message))", ln)
        u = re.search(r"usage_in=(\d+) usage_cached=(\d+) usage_out=(\d+)", ln)
        print("  " + (" ".join(x for x in [m.group(1) or m.group(2)] if x) if m else ln[:90]) + (" usage_in=%s cached=%s out=%s" % u.groups() if u else ""))
' | tail -14
echo ACCEPTANCE_DONE
'@
$command = $command.Replace('__PARALLEL__', $parallel)
if ($command -match '__PARALLEL__') { throw 'Unfilled placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1500
