#requires -Version 7
# 2026-11-26 post-apply acceptance for the alias-scope generation
# (984a935+c9ec040): zhipu drops glm-5.3 and gains glm-5.4; CN WorkBuddy
# serves glm-5.4 as the -wb fork alias; gemini-4-hydrogen-xhigh re-opens.
# Sequential loopback single-shots through admission (127.0.0.1:8318),
# per-lane idle gates before each probe, no retries. Keys are never echoed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -u
set -o pipefail
umask 077
ROOT=$(mktemp -d /root/alias-accept-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
API_KEY=$(python3 -c 'import yaml,pathlib;print(yaml.safe_load(pathlib.Path("/opt/cliproxyapi/config.yaml").read_text())["access"]["api-keys"][0])')
lane_idle() {
  python3 - "$1" <<'PY'
import json, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    d = json.load(response)
st = (d.get("lanes", {}).get(sys.argv[1]) or {}).get("state", {})
if st.get("hold_reason") is not None or st.get("cooldown_active"):
    print("LANE_BLOCKED %s hold=%r" % (sys.argv[1], st.get("hold_reason")))
    raise SystemExit(4)
if st.get("inflight", 0) or st.get("pending", 0):
    print("LANE_BUSY %s inflight=%s pending=%s" % (sys.argv[1], st.get("inflight"), st.get("pending")))
    raise SystemExit(5)
print("LANE_IDLE %s" % sys.argv[1])
PY
}
gate() {
  if ! lane_idle "$2"; then
    rc=$?
    if [ "$rc" != 5 ]; then echo "ABORT=$1"; exit 4; fi
    waited=0
    while [ "$waited" -lt 90 ]; do
      sleep 10; waited=$((waited + 10))
      lane_idle "$2" && return 0
      rc=$?
      [ "$rc" = 5 ] || { echo "ABORT=$1"; exit 4; }
    done
    echo "ABORT=$1 lane_still_busy"
    exit 4
  fi
}
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
parts, terminal, served = [], False, set()
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
        parts.append(str((choice.get("delta") or {}).get("content") or ""))
print("%s_TERMINAL=%s TEXT=%r" % (tag, str(terminal).lower(), "".join(parts).strip()[:60]))
print("%s_SERVED_MODEL=%s" % (tag, ", ".join(sorted(served)) or "unknown"))
for marker in ("PERMISSION_DENIED", "operator_review_required", "TOS_VIOLATION", "server_is_overloaded", "repeat_rejected", "11128", "11140", "VALIDATION_REQUIRED"):
    if marker in text:
        print("%s_SIGNAL=%s" % (tag, marker))
PY
}
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
print("%s_STATUS=%s MODEL=%r TEXT=%r IN=%s OUT=%s" % (
    tag, d.get("status"), d.get("model"), out_text.strip()[:60], usage.get("input_tokens"), usage.get("output_tokens")))
for marker in ("PERMISSION_DENIED", "operator_review_required", "TOS_VIOLATION", "server_is_overloaded", "repeat_rejected", "11128", "VALIDATION_REQUIRED"):
    if marker in text:
        print("%s_SIGNAL=%s" % (tag, marker))
raise SystemExit(0 if d.get("status") == "completed" and out_text.strip() else 7)
PY
}
step() { echo "== $1"; }
step "runtime"
docker inspect cli-proxy-api --format 'IMAGE={{.Config.Image}} RESTARTS={{.RestartCount}}'

gate alias_zhipu_before zhipu-coding-plan
step "probe 1/5: glm-5.4 (zhipu lane, responses)"
responses_single "glm-5.4" '[{"type":"message","role":"user","content":[{"type":"input_text","text":"Reply with exactly: GLM54_ZHIPU_OK"}]}]' ''
gate alias_zhipu_after_glm54 zhipu-coding-plan

step "probe 2/5: glm-5.3-flash (zhipu lane regression after glm-5.3 removal)"
responses_single "glm-5.3-flash" '[{"type":"message","role":"user","content":[{"type":"input_text","text":"Reply with exactly: OK"}]}]' ''
gate alias_zhipu_after_glm53flash zhipu-coding-plan

gate alias_wb_before workbuddy-oauth
step "probe 3/5: glm-5.4-wb (CN workbuddy fork alias, chat)"
chat_single "glm-5.4-wb" '{"model":"glm-5.4-wb","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}'
gate alias_wb_after_glm54wb workbuddy-oauth

step "probe 4/5: deepseek-v4.1-flash (CN workbuddy regression, plugin 0.21.31)"
chat_single "deepseek-v4.1-flash" '{"model":"deepseek-v4.1-flash","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}'
gate alias_wb_after_deepseek workbuddy-oauth

gate alias_agy_before antigravity-oauth
step "probe 5/5: gemini-4-hydrogen-xhigh (antigravity lane re-opened, chat)"
chat_single "gemini-4-hydrogen-xhigh" '{"model":"gemini-4-hydrogen-xhigh","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}'
gate alias_agy_after_hydrogen antigravity-oauth

step "final lane states"
for lane in zhipu-coding-plan workbuddy-oauth antigravity-oauth; do lane_idle "$lane" || true; done
step "journal: probe window upstream results"
journalctl -u cpa-admission --since "6 minutes ago" --no-pager -o cat 2>/dev/null | grep -E "upstream_result" | sed -E 's/request_id=[0-9a-f]+ //' | grep -oE "lane=\S+ model=\S+ status=[0-9]+ capacity=\S+|usage_in=[0-9]+ usage_cached=[0-9]+ usage_out=[0-9]+" | paste - - 2>/dev/null | tail -8
echo ALIAS_ACCEPTANCE_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1500
