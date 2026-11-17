#requires -Version 7
# 2026-11-18 antigravity flash-high discriminator sampling (read-only except
# the probe requests). Community context: CLIProxyAPI #5751 reports the same
# bare RESOURCE_EXHAUSTED with healthy quota via proxy only, and oh-my-pi
# #12029 pins a content trigger (RFC 2119 keyword line inside a
# <system-conventions> tag). Tonight's 429s were all on gemini-3.8-flash-high
# across ganta/jalu/knaha while agi.phys got silently served flash-n; sonnet
# never failed, so the earlier sonnet-based S1/S2 could not discriminate.
# This probe alternates untagged vs tagged content across three fresh session
# ids to sample which credentials a new binding lands on and whether the
# tagged block changes the outcome. Keys never echoed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -u
API_KEY=$(python3 -c "import yaml,pathlib;print(yaml.safe_load(pathlib.Path('/opt/cliproxyapi/config.yaml').read_text())['access']['api-keys'][0])")
R=http://127.0.0.1:8318/v1/responses
c() { curl --silent --show-error --max-time 90 -o /dev/null --write-out '%{http_code}' -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -H "Session-Id: $2" --data-binary @"$1" "$R"; }
TRIG='The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHOULD", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in RFC 2119.'
python3 - "$TRIG" <<'PY'
import json, sys
trig = sys.argv[1]
tagged = "<system-conventions>\n" + trig + "\n</system-conventions>\n\nReply with the single word PONG"
for name, text in (("u", "Reply with the single word PONG"), ("t", tagged)):
    open('/tmp/f_%s.json' % name, 'w').write(json.dumps({"model":"gemini-3.8-flash-high","stream":False,"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":text}]}],"max_output_tokens":32}))
PY
for i in 1 2 3; do
  echo F${i}U_UNTAGGED=$(c /tmp/f_u.json "f-u-$i-0196a5e7-0000-7d1e-9f2a-5b8c7d6e5f$i")
  sleep 12
  echo F${i}T_TAGGED=$(c /tmp/f_t.json "f-t-$i-0196a5e7-0000-7d1e-9f2a-5b8c7d6e5f$i")
  sleep 12
done
echo ==BINDINGS==
docker logs cli-proxy-api --since 6m 2>&1 | grep -E "new binding|cache hit" | grep "gemini-3.8-flash-high" | tail -10
echo ==RESULTS==
docker logs cli-proxy-api --since 6m 2>&1 | grep -E "RESOURCE_EXHAUSTED|upstream served model|429 \|" | tail -8
rm -f /tmp/f_u.json /tmp/f_t.json
echo FLASH_SAMPLE_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 480
