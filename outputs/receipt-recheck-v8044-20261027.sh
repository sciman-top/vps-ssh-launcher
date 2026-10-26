set -Eeuo pipefail
python3 - <<'PY'
from pathlib import Path
import hashlib,json,re,collections
root=Path('/root/cpa-v8044-isolated-20261027')
expected={'cpa-health.py':'0a38749a1a20c0aa34490819f1238ec4e3f12fa9f16463adff486e79fba4c396','cpa-update-acceptance.py':'738cb7faf0b25772fa3e4ef7f9ff7d18b6267efbf3e2768a057f5ce78ffbc3e4','cpa-acceptance.py':'9edf6288bcf53d8dddfd4bf9d7ff5ef7b117b3db5610057590535d4e5e68ffc7','cpa_provider_routes.json':'5c95d4b9ab282ceea77c623e4b15e6aefab223438e0aba17a94643f4361069a8'}
for name,value in expected.items():
 ok=hashlib.sha256((root/name).read_bytes()).hexdigest()==value
 print('fixture_source='+name+' '+('MATCH' if ok else 'DRIFT'))
 assert ok
log=(root/'acceptance.log').read_text()
assert 'ACCEPTANCE_RESULT=PASS' in log
for line in log.splitlines():
 try: d=json.loads(line)
 except ValueError: continue
 if 'scenario' in d or d.get('stage') in ('overload','cooldown','recovered','actual_health'): print('receipt='+json.dumps(d))
print('ACCEPTANCE_RECEIPT_CURRENT=PASS')
assert hashlib.sha256((root/'CLIProxyAPI').read_bytes()).hexdigest()=='6e8834cd053c1980afe20fe4a67e242874bcaee1a5682b8e735f16f55ae6496d'
assert hashlib.sha256((root/'plugins/workbuddy.so').read_bytes()).hexdigest()=='3920cbf4dd96f0a38e20c1c73f6e77a0a88d4b4d3b37f358d923e56bf6228c8f'
log=(root/'plugin-load.log').read_text()
assert re.search(r'plugin registered plugin_id=workbuddy .*version=0\.9\.77',log)
assert 'account_max_concurrent=1 applied' in log
assert 'identity_switch_on_429 applied' not in log
print('JOINT_PLUGIN_RECEIPT_CURRENT=PASS')
# Inspect response-side error codes only; never emit log bodies or request data.
counts=collections.Counter()
for p in sorted(Path('/opt/cliproxyapi/auth/logs').glob('error-*.log'),key=lambda p:p.stat().st_mtime,reverse=True)[:5]:
 s=p.read_text(errors='replace')
 i=s.find('=== API RESPONSE ===')
 if i<0: i=s.find('=== RESPONSE ===')
 if i<0: continue
 s=s[i:]
 for code in re.findall(r'"code"\s*:\s*"?(1302|1308|11140|6004)\b',s): counts[code]+=1
print('retained_response_error_codes='+json.dumps(dict(counts)))
PY
docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI
