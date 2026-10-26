set -Eeuo pipefail
python3 - <<'PY'
import json,subprocess,re,collections,yaml,urllib.request
from pathlib import Path
c=yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())
w=c['plugins']['configs']['workbuddy']
print('workbuddy_safe_config='+json.dumps({k:w.get(k) for k in ('scheduler_mode','account_max_concurrent','identity_switch_on_429','checkin_auto','tasks_auto','lifecycle_auto','token_keepalive')}))
print('api_key_routes='+str(len(c['api-keys']['openai-compatibility'])))
print('oauth_files='+str(len(list(Path('/opt/cliproxyapi/auth').glob('*.json')))))
h=json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz',timeout=5))
for k,v in h['lanes'].items():
 s=v.get('state',{}); print('lane='+k+' inflight='+str(s.get('inflight'))+' hold='+str(s.get('hold_reason')))
log=subprocess.run(['journalctl','-u','cpa-admission','--since','2026-10-26 16:48:44 UTC','-o','cat'],capture_output=True,text=True,check=True).stdout
counts=collections.Counter()
for line in log.splitlines():
 if 'upstream_result ' not in line: continue
 fields=dict(re.findall(r'([a-z_]+)=([^\s]+)',line))
 counts[tuple((k,fields.get(k,'unknown')) for k in ('lane','model','status','successful','protocol_result'))]+=1
for key,n in counts.items(): print('post_upgrade_natural='+json.dumps(dict(key)|{'count':n}))
nginx=subprocess.run(['nginx','-T'],capture_output=True,text=True,check=True).stdout
print('nginx_proxy_buffering_off_count='+str(len(re.findall(r'proxy_buffering\s+off;',nginx))))
PY
docker inspect --format 'image={{.Config.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' cli-proxy-api
sha256sum /opt/cliproxyapi/plugins/workbuddy.so
