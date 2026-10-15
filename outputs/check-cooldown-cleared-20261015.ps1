# One-off read-only verification: both cooldown layers cleared after RestartAndRecover.
# Layer 1 (CPA in-memory): container freshly started by the recovery restart.
# Layer 2 (admission): live healthz per-lane cooldown state, all three lanes.
$ErrorActionPreference = 'Stop'
$repo = 'D:\CODE\vps-ssh-launcher'
$remote = @'
echo "==docker=="
docker inspect -f 'status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}} image={{.Config.Image}}' cli-proxy-api
echo "==healthz=="
python3 - <<'PY'
import json, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    data = json.load(response)
print("status=" + str(data.get("status")))
for name in sorted(data["lanes"]):
    s = data["lanes"][name]["state"]
    cooldown_active = s.get("cooldown_active", bool(s.get("cooldown_remaining")))
    verdict = "CLEAR" if (not cooldown_active and not s.get("half_open_probe")) else "ACTIVE"
    print(
        "lane=%s inflight=%s pending=%s half_open=%s cooldown_active=%s cooldown_remaining=%s cooldown_scope=%s failure_streak=%s model_cooldowns=%s verdict=%s"
        % (
            name, s.get("inflight"), s.get("pending"), s.get("half_open_probe"),
            cooldown_active, s.get("cooldown_remaining"), s.get("cooldown_scope"),
            s.get("failure_streak", 0), json.dumps(s.get("model_cooldowns", {}), sort_keys=True),
            verdict,
        )
    )
PY
'@
& (Join-Path $repo 'connect.ps1') -Profile bwg -StrictHostKeyChecking `
  -Command $remote -CommandTimeout 60 -CommandHardTimeout 90
exit $LASTEXITCODE
