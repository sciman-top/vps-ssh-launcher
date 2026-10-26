set -Eeuo pipefail
python3 - <<'PY'
import json,urllib.request
h=json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz',timeout=5))
for k,v in h['lanes'].items():
 s=v.get('state',{})
 print(k, 'inflight='+str(s.get('inflight')), 'hold='+str(s.get('hold_reason')))
PY
docker ps --format '{{.Image}} {{.Status}}'
