"""Phase 2 (revised): extend requests.payload for claude-haiku-5-5 (2026-10-16).

No manual restart: the following -Apply reads the file from disk, validates
semantic policy against it, and restarts the container itself (its
transaction backup is taken after this edit, so a rollback keeps the edit).
Preflight is informational only; any lane busyness is left to -Apply's own
readiness gates.
"""

import json
import subprocess
import sys
import urllib.request

import yaml

CONFIG = "/opt/cliproxyapi/config.yaml"
MUSE = "muse-spark-1.3-contributor"
HAIKU = "claude-haiku-5-5"

MUSE_OVERRIDE = """    override:
    - models:
      - name: muse-spark-1.3-contributor
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
"""
HAIKU_OVERRIDE = """    - models:
      - name: claude-haiku-5-5
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
"""
MUSE_FILTER = """    filter:
    - models:
      - name: muse-spark-1.3-contributor
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
"""
HAIKU_FILTER = """    - models:
      - name: claude-haiku-5-5
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
"""

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    health = json.load(response)
lanes = health.get("lanes", {})
for name, lane in lanes.items():
    state = lane.get("state", {})
    print(
        "LANE %s inflight=%s pending=%s half_open=%s cooldown=%s hold=%s"
        % (
            name,
            state.get("inflight"),
            state.get("pending"),
            state.get("half_open_probe"),
            state.get("cooldown_remaining"),
            state.get("hold_reason"),
        )
    )
busy = any(
    lane.get("state", {}).get(key)
    for lane in lanes.values()
    for key in ("inflight", "pending", "half_open_probe")
)
print("HEALTHZ status=%s retired_readers=%s busy=%s" % (
    health.get("status"), health.get("retired_readers", 0), busy
))

text = open(CONFIG, encoding="utf-8").read()
assert text.count(MUSE_OVERRIDE) == 1, "muse override anchor not unique"
assert text.count(MUSE_FILTER) == 1, "muse filter anchor not unique"
new_text = text.replace(MUSE_OVERRIDE, MUSE_OVERRIDE + HAIKU_OVERRIDE, 1)
new_text = new_text.replace(MUSE_FILTER, MUSE_FILTER + HAIKU_FILTER, 1)

yaml.safe_load(text)  # old doc parses
new_doc = yaml.safe_load(new_text)
old_doc = yaml.safe_load(text)
old_payload = old_doc["requests"].pop("payload")
new_payload = new_doc["requests"].pop("payload")
assert old_doc == new_doc, "non-payload config changed"
override_models = [entry["models"][0]["name"] for entry in new_payload["override"]]
filter_models = [entry["models"][0]["name"] for entry in new_payload["filter"]]
assert override_models == [MUSE, HAIKU], override_models
assert filter_models == [MUSE, HAIKU], filter_models
assert new_payload["override"][1]["params"] == old_payload["override"][0]["params"]
assert new_payload["filter"][1]["params"] == old_payload["filter"][0]["params"]
print("YAML_ASSERT_OK")

snapshot = CONFIG + ".bak-20261016-payload-haiku"
subprocess.run(["cp", CONFIG, snapshot], check=True)
tmp = CONFIG + ".tmp-payload-haiku"
with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(new_text)
subprocess.run(["mv", tmp, CONFIG], check=True)
print("SNAPSHOT+WRITE_OK " + snapshot)
print("PAYLOAD_RULES_EXTENDED")
