"""Reorder requests.payload entries to sorted model-name order (2026-10-16).

cpa_policy.py derives EXPECTED_PAYLOAD_TOOL_RULES from sorted codex route
model names and compares with ==, so the config entries must be
[claude-haiku-5-5, muse-spark-1.3-contributor]. Failed applies leave disk
untouched (write-then-verify), so this edit starts from the surviving
[muse, haiku] state.
"""

import subprocess
import sys

import yaml

CONFIG = "/opt/cliproxyapi/config.yaml"

OLD_OVERRIDE = """    override:
    - models:
      - name: muse-spark-1.3-contributor
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
    - models:
      - name: claude-haiku-5-5
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
"""
NEW_OVERRIDE = """    override:
    - models:
      - name: claude-haiku-5-5
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
    - models:
      - name: muse-spark-1.3-contributor
      params:
        tools.#(type=="tool_search").parameters.required:
        - query
        - limit
"""
OLD_FILTER = """    filter:
    - models:
      - name: muse-spark-1.3-contributor
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
    - models:
      - name: claude-haiku-5-5
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
"""
NEW_FILTER = """    filter:
    - models:
      - name: claude-haiku-5-5
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
    - models:
      - name: muse-spark-1.3-contributor
      params:
      - tools.#(type=="web_search").search_content_types
      - tools.#(type=="custom")
"""

text = open(CONFIG, encoding="utf-8").read()
assert text.count(OLD_OVERRIDE) == 1, "override block not in [muse, haiku] order"
assert text.count(OLD_FILTER) == 1, "filter block not in [muse, haiku] order"
new_text = text.replace(OLD_OVERRIDE, NEW_OVERRIDE, 1)
new_text = new_text.replace(OLD_FILTER, NEW_FILTER, 1)

old_doc = yaml.safe_load(text)
new_doc = yaml.safe_load(new_text)
old_payload = old_doc["requests"].pop("payload")
new_payload = new_doc["requests"].pop("payload")
assert old_doc == new_doc, "non-payload config changed"
override_models = [e["models"][0]["name"] for e in new_payload["override"]]
filter_models = [e["models"][0]["name"] for e in new_payload["filter"]]
assert override_models == ["claude-haiku-5-5", "muse-spark-1.3-contributor"], override_models
assert filter_models == ["claude-haiku-5-5", "muse-spark-1.3-contributor"], filter_models
assert new_payload == {
    "override": [
        {"models": [{"name": "claude-haiku-5-5"}],
         "params": {"tools.#(type==\"tool_search\").parameters.required": ["query", "limit"]}},
        {"models": [{"name": "muse-spark-1.3-contributor"}],
         "params": {"tools.#(type==\"tool_search\").parameters.required": ["query", "limit"]}},
    ],
    "filter": [
        {"models": [{"name": "claude-haiku-5-5"}],
         "params": ["tools.#(type==\"web_search\").search_content_types",
                    "tools.#(type==\"custom\")"]},
        {"models": [{"name": "muse-spark-1.3-contributor"}],
         "params": ["tools.#(type==\"web_search\").search_content_types",
                    "tools.#(type==\"custom\")"]},
    ],
}, "payload does not equal policy expectation"
print("YAML_REORDER_ASSERT_OK")

snapshot = CONFIG + ".bak-20261016-payload-order"
subprocess.run(["cp", CONFIG, snapshot], check=True)
tmp = CONFIG + ".tmp-payload-order"
with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(new_text)
subprocess.run(["mv", tmp, CONFIG], check=True)
print("SNAPSHOT+WRITE_OK " + snapshot)
