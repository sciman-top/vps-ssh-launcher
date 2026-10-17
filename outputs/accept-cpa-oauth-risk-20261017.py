import http.client,json,runpy,threading,time,tempfile,pathlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ns=runpy.run_path('/opt/cliproxyapi/cpa-admission.py')
config_path=pathlib.Path('/opt/cliproxyapi/cpa-admission.json')
cases=[
 (403,b'{}',{},'operator_review_required'),
 (429,b'{"error":{"type":"rate_limit_error"}}',{},'operator_review_required'),
 (429,b'{"error":{"type":"rate_limit_error"}}',{'Retry-After':'100000'},'cooldown'),
 (529,b'{"error":{"type":"overloaded_error"}}',{},'cooldown'),
 (200,b'data: {"error":{"message":"account has been suspended"}}\n\n',{},'operator_review_required'),
]
for index,(status,body,headers,expected) in enumerate(cases):
    received=[]
    class Stub(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(status)
            self.send_header('Content-Type','text/event-stream' if body.startswith(b'data:') else 'application/json')
            self.send_header('Content-Length',str(len(body)))
            for k,v in headers.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Stub)
    with tempfile.TemporaryDirectory(prefix='cpa-risk-fixture-') as tmp:
        paths=[pathlib.Path(tmp)/name for name in ['antigravity.json','claude.json']]
        for path in paths:path.write_text(json.dumps({'version':1,'not_before':0,'hold_reason':None}))
        cfg=ns['load_config'](config_path);cfg['upstream_port']=upstream.server_port
        lane=next(l for l in cfg['lanes'] if l['name']=='claude-oauth');lane['queue_timeout_seconds']=0.01
        proxy=ns['AdmissionProxy'](cfg,state_path=paths[0],claude_state_path=paths[1])
        server=ns['Server'](('127.0.0.1',0),proxy)
        threads=[threading.Thread(target=s.serve_forever) for s in [upstream,server]]
        for t in threads:t.start()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
        request=b'{"model":"claude-sonnet-5-5","messages":[]}'
        try:
            client.request('POST','/v1/messages',request)
            response=client.getresponse();assert response.status==status;assert response.read()==body
            deadline=time.monotonic()+3
            while proxy.lanes['claude-oauth'].snapshot()['inflight']:
                assert time.monotonic()<deadline;time.sleep(0.01)
            client.request('POST','/v1/messages',request)
            response=client.getresponse();assert response.status==(403 if expected=='operator_review_required' else 429)
            assert response.getheader('X-CPA-Admission-Reason')==expected
            response.read();assert received==[request]
            state=ns['LaneState'](lane,paths[1]);assert not state.acquire().admitted
            assert json.loads(paths[0].read_text())['hold_reason'] is None
            print('FIXTURE_CASE='+str(index+1)+' PASS',flush=True)
        finally:
            client.close()
            for srv in [server,upstream]:srv.shutdown();srv.server_close()
            for t in threads:t.join(timeout=2)
print('CONTROLLED_FIXTURE_RESULT=PASS cases=5 real_provider_calls=0',flush=True)
