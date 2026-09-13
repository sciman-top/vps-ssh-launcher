"""Run unchanged production updater in the disposable CPA fixture namespace.

Only release metadata and Docker lifecycle are replaced. Health requests and
CPA processes are real; fixture image tags never leave this namespace.
"""

import json
import os
from pathlib import Path
import subprocess

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
import sys,os,signal,time,subprocess,json,yaml
from pathlib import Path
r=Path('/opt/cliproxyapi');args=sys.argv[1:];mode=(r/'scenario').read_text().strip()
with (r/'docker-calls').open('a') as h:h.write(' '.join(args)+'\\n')
if args[:2]==['image','inspect'] or args[:1]==['pull']:sys.exit(0)
if args[:2]==['compose','config']:sys.exit(0)
if args[:3]!=['compose','up','-d']:sys.exit(9)
try:os.kill(int((r/'pid').read_text()),signal.SIGTERM)
except (ProcessLookupError,FileNotFoundError):pass
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
        results.append(result)
    return results
