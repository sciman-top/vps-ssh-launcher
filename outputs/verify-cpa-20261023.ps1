#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3-flash
python3 - <<'PY'
import json,time,urllib.request,yaml
c=yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
k=c['access']['api-keys'][0]
if isinstance(k,dict): k=k['key']
body={'model':'glm-5.3-flash','messages':[{'role':'user','content':'Reply with exactly: cache stream probe ok'}],'stream':True,'max_tokens':1024}
req=urllib.request.Request('http://127.0.0.1:8318/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+k,'Content-Type':'application/json'})
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
t=time.monotonic(); first=None; text=[]; finish=False; done=False; count=0
with op.open(req,timeout=90) as r:
 status=r.status
 for line in r:
  if time.monotonic()-t>120: raise SystemExit('STREAM_TIMEOUT')
  if not line.startswith(b'data:'): continue
  d=line[5:].strip()
  if d==b'[DONE]': done=True; break
  p=json.loads(d); assert not p.get('error'), 'STREAM_ERROR'
  count+=1
  for ch in p.get('choices',[]):
   v=ch.get('delta',{}).get('content','')
   if v:
    if first is None: first=time.monotonic()-t
    text.append(v)
   finish=finish or ch.get('finish_reason')=='stop'
assert status==200 and finish and done and ''.join(text).strip()=='cache stream probe ok', 'STREAM_CONTRACT_FAILED'
print(json.dumps({'STREAM_ACCEPTANCE':'PASS','status':status,'first_text_s':round(first,3),'total_s':round(time.monotonic()-t,3),'events':count}))
PY
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 180 -HardTimeoutSeconds 400
