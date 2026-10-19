"""State probe after the failed apply (2026-10-16). Read-only.
"""

import hashlib
import json
import subprocess

import yaml

CONFIG = "/opt/cliproxyapi/config.yaml"

manifest_hash = hashlib.sha256(
    open("/opt/cliproxyapi/cpa_provider_routes.json", "rb").read()
).hexdigest()
print("MANIFEST_SHA %s" % manifest_hash[:12])

config = yaml.safe_load(open(CONFIG, encoding="utf-8"))
payload = config["requests"]["payload"]
print("PAYLOAD override=%s filter=%s" % (
    [e["models"][0]["name"] for e in payload["override"]],
    [e["models"][0]["name"] for e in payload["filter"]],
))
compat = config["api-keys"]["openai-compatibility"]
for entry in compat:
    host = str(entry.get("base-url", ""))
    if "opencode" in host:
        print("SLOT2 models=%s" % [m["name"] for m in entry.get("models", [])])
codex = config["api-keys"].get("codex", [])
for group in codex:
    print("CODEXGROUP %s models=%s" % (
        group.get("name"), [m["name"] for m in group.get("models", [])]
    ))

status = subprocess.run(
    ["docker", "inspect", "--format", "{{.State.Status}}", "cli-proxy-api"],
    capture_output=True, text=True,
)
print("CONTAINER %s" % status.stdout.strip())
adm = subprocess.run(
    ["systemctl", "is-active", "cpa-admission.service"], capture_output=True, text=True
)
print("ADMISSION %s" % adm.stdout.strip())
ls = subprocess.run(
    ["ls", "-d", "/root/cpa-guardrails-backup-20261016T*" ],
    capture_output=True, text=True,
)
print("BACKUPS")
print(ls.stdout.strip())
