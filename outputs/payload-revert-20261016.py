"""Revert requests.payload to muse-only (claude-haiku-5-5 retirement).

Removes the two haiku entries added earlier today so the semantic policy
(which derives muse-only expectations from the retired manifest) passes.
"""

import subprocess

import yaml

CONFIG = "/opt/cliproxyapi/config.yaml"

HAIKU_OVERRIDE = """    - models:
      - name: claude-haiku-5-5
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
"""
HAIKU_FILTER = """    - models:
      - name: claude-haiku-5-5
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
"""

text = open(CONFIG, encoding="utf-8").read()
assert text.count(HAIKU_OVERRIDE) == 1, "haiku override entry not found"
assert text.count(HAIKU_FILTER) == 1, "haiku filter entry not found"
new_text = text.replace(HAIKU_OVERRIDE, "", 1).replace(HAIKU_FILTER, "", 1)

old_doc = yaml.safe_load(text)
new_doc = yaml.safe_load(new_text)
old_payload = old_doc["requests"].pop("payload")
new_payload = new_doc["requests"].pop("payload")
assert old_doc == new_doc, "non-payload config changed"
override_models = [e["models"][0]["name"] for e in new_payload["override"]]
filter_models = [e["models"][0]["name"] for e in new_payload["filter"]]
assert override_models == ["muse-spark-1.3-contributor"], override_models
assert filter_models == ["muse-spark-1.3-contributor"], filter_models
assert new_payload["override"][0]["params"] == old_payload["override"][1]["params"]
assert new_payload["filter"][0]["params"] == old_payload["filter"][1]["params"]
print("YAML_REVERT_ASSERT_OK")

snapshot = CONFIG + ".bak-20261016-payload-retire"
subprocess.run(["cp", CONFIG, snapshot], check=True)
tmp = CONFIG + ".tmp-payload-retire"
with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(new_text)
subprocess.run(["mv", tmp, CONFIG], check=True)
print("SNAPSHOT+WRITE_OK " + snapshot)
