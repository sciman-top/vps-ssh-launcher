#requires -Version 7
# 2026-11-17 post-swap acceptance singles for the antigravity lane: the
# challenge-flagged credential was removed and two new pro credentials were
# added by the user (IDE-verified for hydrogen + sonnet-5-5). Three sequential
# loopback single-shots (sonnet -> hydrogen -> flash), each gated on a
# pre-probe lane health check (hold must be None, inflight 0) and a
# post-probe re-check so a surprise upstream 403 aborts before the next
# model. Keys/tokens never echoed. Read-only except the probe requests.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -u
set -o pipefail
ROOT=$(mktemp -d /root/ag-singles-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
API_KEY=$(python3 -c 'import yaml,pathlib;print(yaml.safe_load(pathlib.Path("/opt/cliproxyapi/config.yaml").read_text())["access"]["api-keys"][0])')
lane_state() {
  curl -s --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
d = json.load(sys.stdin)
st = d["lanes"]["antigravity-oauth"]["state"]
print("AG hold=%r gen=%r inflight=%s pending=%s" % (st["hold_reason"], st["failure_generation"], st["inflight"], st["pending"]))
sys.exit(0 if st["hold_reason"] is None and st["inflight"] == 0 and st["pending"] == 0 else 4)
'
}
single() {
  model="$1"; out="$ROOT/$model.body"
  st=$(curl --silent --show-error --max-time 120 --output "$out" --write-out "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
    --data "{\"model\":\"$model\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Return the word PONG only.\"}]}" \
    http://127.0.0.1:8318/v1/chat/completions)
  STATUS=$(echo "$model" | tr '.' '_'); STATUS="${STATUS//-/_}_STATUS=$st"
  echo "$STATUS"
  python3 - "$out" "$model" <<'PY'
import json, sys
path, model = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8", errors="replace").read()
parts = []; terminal = False; events = 0; served = set()
for line in text.splitlines():
    if not line.startswith("data: "):
        continue
    payload = line[6:].strip()
    if payload == "[DONE]":
        terminal = True; continue
    try:
        ev = json.loads(payload)
    except ValueError:
        continue
    events += 1
    ch = (ev.get("choices") or [{}])[0]
    parts.append(str((ch.get("delta") or {}).get("content") or ""))
    if ev.get("model"):
        served.add(str(ev["model"]))
tag = model.upper().replace(".", "_").replace("-", "_")
print("%s_SSE_EVENTS=%d TERMINAL=%s TEXT=%r" % (tag, events, str(terminal).lower(), "".join(parts).strip()))
served_ids = ", ".join(sorted(served)) if served else "unknown"
print("%s_SERVED_MODEL=%s" % (tag, served_ids))
if served and model not in served:
    print("%s_SUBSTITUTED=YES upstream_replaced_requested_model" % tag)
for marker in ("VALIDATION_REQUIRED", "PERMISSION_DENIED", "operator_review_required", "TOS_VIOLATION"):
    if marker in text:
        print("%s_SIGNAL=%s" % (tag, marker))
if not terminal:
    print("%s_RAW_HEAD=%r" % (tag, text[:300]))
PY
}
echo "== probe 1/3: claude-sonnet-5-5-high"
lane_state || { echo "ABORT=lane_not_healthy"; exit 4; }
single "claude-sonnet-5-5-high"
lane_state || { echo "STOP=lane_unhealthy_after_sonnet"; exit 5; }
echo "== probe 2/3: gemini-4-hydrogen-xhigh"
single "gemini-4-hydrogen-xhigh"
lane_state || { echo "STOP=lane_unhealthy_after_hydrogen"; exit 5; }
echo "== probe 3/3: gemini-3.8-flash-high"
single "gemini-3.8-flash-high"
lane_state || { echo "STOP=lane_unhealthy_after_flash"; exit 5; }
echo "== journal: probe window upstream results"
journalctl -u cpa-admission --since '3 minutes ago' --no-pager | grep 'antigravity' | tail -8
echo SWAP_SINGLES_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 420
