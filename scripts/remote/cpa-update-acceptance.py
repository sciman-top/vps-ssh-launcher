"""Run unchanged production updater in the disposable CPA fixture namespace.

Only release metadata and Docker lifecycle are replaced. Health requests and
CPA processes are real; fixture image tags never leave this namespace.
"""

import json
import os
import time
from pathlib import Path
import subprocess

import yaml

ROOT = Path("/opt/cliproxyapi")


def run_cases():
    assert (ROOT / "FIXTURE_ONLY").exists()
    assert Path("/proc/self/ns/net").readlink() != Path("/proc/1/ns/net").readlink()
    bins = ROOT / "bin"
    bins.mkdir(exist_ok=True)
    stub = ROOT / "python-stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text("""
import urllib.request,json,io
real=urllib.request.urlopen
def urlopen(req,*args,**kwargs):
 url=req.full_url if hasattr(req,'full_url') else req
 if url.startswith('https://api.github.com/repos/router-for-me/CLIProxyAPI/releases'):
  return io.BytesIO(json.dumps([{'tag_name':'v0.0.2','draft':False,'prerelease':False,'published_at':'2020-01-01T00:00:00Z'}]).encode())
 if url.startswith('https://hub.docker.com/v2/repositories/eceasy/cli-proxy-api/tags'):
  return io.BytesIO(json.dumps({'results':[{'name':'v0.0.2','last_updated':'2020-01-01T00:00:00Z','digest':'sha256:'+'a'*64}]}).encode())
 return real(req,*args,**kwargs)
urllib.request.urlopen=urlopen
""")
    (bins / "docker").write_text("""#!/usr/bin/python3
import sys,os,signal,socket,time,subprocess,json,urllib.request,yaml
from pathlib import Path
r=Path('/opt/cliproxyapi');args=sys.argv[1:];mode=(r/'scenario').read_text().strip()
with (r/'docker-calls').open('a') as h:h.write(' '.join(args)+'\\n')
if args[:2]==['image','inspect'] or args[:1]==['pull']:sys.exit(0)
if args[:2]==['compose','config']:sys.exit(0)
if args[:3]!=['compose','up','-d']:sys.exit(9)
try:os.kill(int((r/'pid').read_text()),signal.SIGTERM)
except (ProcessLookupError,FileNotFoundError):pass
# Wait for the old CPA to fully release the port before starting the new one,
# otherwise the new process fails to bind (or traffic hits the dying process
# with stale cooldown state) and pre-update health races scene startup.
dl=time.time()+20
while time.time()<dl:
 try:
  s=socket.create_connection(('127.0.0.1',8317),timeout=1);s.close();time.sleep(.5)
 except Exception:break
time.sleep(.4)
new='v0.0.2' in (r/'compose.yml').read_text()
if mode=='start_fail' and new:sys.exit(1)
config=yaml.safe_load((r/'config.yaml').read_text())
if mode=='model_exposure' and new:
 config['codex-api-key'][0]['models'].append({'name':'gpt-unexpected','alias':'gpt-unexpected'})
(r/'runtime.yaml').write_text(yaml.safe_dump(config))
(r/'upstream-mode').write_text('http503' if new and mode=='transient' else 'ok')
with (r/'binary.log').open('ab') as log:
 p=subprocess.Popen([str(r/'CLIProxyAPI'),'-config',str(r/'runtime.yaml')],cwd=r,stdout=log,stderr=log,start_new_session=True)
(r/'pid').write_text(str(p.pid))
# Deterministic readiness: poll the catalog endpoint until the NEW process
# answers, so scenario health checks never hit a starting or dying instance.
if not (mode=='start_fail' and new):
 dl=time.time()+45
 while time.time()<dl:
  try:
   rq=urllib.request.Request('http://127.0.0.1:8317/v1/models',headers={'Authorization':'Bearer fixture-client'})
   with urllib.request.urlopen(rq,timeout=2) as rp:
    if rp.status==200:break
  except Exception:pass
  time.sleep(1)
""")
    (bins / "docker").chmod(0o700)
    env = dict(
        os.environ, PATH=str(bins) + ":" + os.environ["PATH"], PYTHONPATH=str(stub)
    )
    old = "services:\n  cli-proxy-api:\n    image: eceasy/cli-proxy-api:v0.0.1\n"
    results = []
    for mode, expected in [
        ("start_fail", 1),
        ("model_exposure", 1),
        ("transient", 10),
        ("success", 0),
    ]:
        if os.environ.get("CPA_ACCEPTANCE_REMAINING") == "1" and mode not in (
            "transient",
            "success",
        ):
            continue
        (ROOT / "scenario").write_text(mode)
        (ROOT / "compose.yml").write_text(old)
        (ROOT / "upstream-mode").write_text("ok")
        subprocess.run(
            [str(bins / "docker"), "compose", "up", "-d"], check=True, env=env
        )
        ready = subprocess.run(
            ["python3", str(ROOT / "cpa-health.py"), "readiness"],
            capture_output=True,
            text=True,
        )
        assert ready.returncode == 0
        # Deterministic full-catalog wait: a freshly started fixture CPA may
        # register its last declared model up to ~60s after the port answers
        # (observed: gpt-5.6-sol), and cpa-health generation requires the bare
        # catalog to EXACTLY equal the allowed set. Without this wait the
        # updater's pre-update health races registration and DEFERs.
        expected = set()
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
        for entry in cfg.get("codex-api-key", []) + cfg.get(
            "openai-compatibility", []
        ):
            for m in entry.get("models", []):
                if isinstance(m, dict) and m.get("alias"):
                    expected.add(m["alias"])
        deadline = time.time() + 180
        bare: set = set()
        warmed: set = set()
        while time.time() < deadline:
            q = subprocess.run(
                [
                    "python3",
                    "-c",
                    "import json,urllib.request"
                    ";d=json.load(urllib.request.urlopen(urllib.request.Request("
                    "'http://127.0.0.1:8317/v1/models',"
                    "headers={'Authorization':'Bearer fixture-client'})))"
                    ";print(sorted(m['id'] for m in d['data'] if '/' not in m['id']))",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            try:
                bare = set(json.loads(q.stdout.strip() or "[]"))
            except ValueError:
                bare = set()
            if bare == expected:
                break
            # Declared models can register lazily on first use (observed with
            # gpt-5.6-sol): warm each missing model with a tiny generation so
            # passive waiting is not required.
            for missing in sorted(expected - bare - warmed):
                warm = subprocess.run(
                    [
                        "python3",
                        "-c",
                        "import json,urllib.request"
                        ";b=json.dumps({'model':'"
                        + missing
                        + "','messages':[{'role':'user','content':'Reply OK'}],"
                        "'max_tokens':8}).encode()"
                        ";r=urllib.request.urlopen(urllib.request.Request("
                        "'http://127.0.0.1:8317/v1/chat/completions',data=b,"
                        "headers={'Authorization':'Bearer fixture-client',"
                        "'Content-Type':'application/json'}),timeout=60)"
                        ";d=json.load(r);print(d.get('model'))",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                warmed.add(missing)
            time.sleep(2)
        print(
            json.dumps(
                {
                    "catalog_ready": bare == expected,
                    "waited_s": round(90 - (deadline - time.time()), 1),
                    "bare": sorted(bare),
                }
            ),
            flush=True,
        )
        if bare != expected:
            print(
                json.dumps({"fixture_diagnostic": f"catalog never reached {sorted(expected)}"}),
                flush=True,
            )
            raise AssertionError("fixture catalog incomplete before scenario")
        (ROOT / "docker-calls").write_text("")
        print(json.dumps({"update_scenario_start": mode}), flush=True)
        p = subprocess.run(
            ["bash", str(ROOT / "auto-update.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        restored = (ROOT / "compose.yml").read_text() == old
        local = subprocess.run(
            ["python3", str(ROOT / "cpa-health.py"), "readiness"],
            capture_output=True,
            text=True,
        )
        result = {
            "scenario": mode,
            "exit": p.returncode,
            "old_compose_restored": restored,
            "real_cpa_ready": local.returncode == 0,
            "rollback_logged": "ROLLBACK restored=" in p.stdout,
            "unverified_logged": "UNVERIFIED:" in p.stdout,
            "transient_generation_retried": "one recheck after 65s" in p.stdout,
        }
        print(json.dumps(result), flush=True)
        if p.returncode != expected:
            print(
                json.dumps(
                    {"fixture_diagnostic": p.stdout[-1500:], "stderr": p.stderr[-1000:]}
                ),
                flush=True,
            )
        assert p.returncode == expected and local.returncode == 0
        assert restored == (mode in ("start_fail", "model_exposure"))
        if mode == "transient":
            assert not result["transient_generation_retried"]
        results.append(result)
    return results
