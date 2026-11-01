"""One-off generator: derive the v8.0.49 joint-canary scripts from the
reviewed v8.0.48 templates.

The acceptance/load fixtures are self-contained, version-agnostic payloads
(verified: they embed neither the fixture V path, the plugin version, nor the
CPA tag), so they are copied byte-for-byte and only the plaintext anchors are
rewritten. Every substitution asserts its expected hit count so a silent drift
in the template fails loudly instead of producing a wrong pin.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent

# v8.0.49 pins (resolved read-only from the registry index digest).
CUR = "v8.0.48"
CUR_DIGEST = "c198886a86b57f49cb2508a68b6170e23b2d509c648934af5d5dd827cf6faa16"
TARGET = "v8.0.49"
DIGEST = "20925aef8323b3f2539605aa048bb59ff17cc9761cba311e2eb1e1723b1ff42e"
PLUGIN_SHA = "81f0ccf3082e878fbe3cb66bf167c377c8d1029ece86dac5b322352a9c806be6"
PLUGIN_VERSION = "0.9.82"

OLD_V = "cpa-v8048-plugin-0981-20261030"
NEW_V = "cpa-v8049-plugin-0982-20261101"
OLD_WB = "cpa-workbuddy-0981-20261028"
NEW_WB = "cpa-workbuddy-0982-20261031"


def rewrite(name: str, src: str, repls: list[tuple[str, str, int]]) -> None:
    text = src
    for old, new, expected in repls:
        count = text.count(old)
        if count != expected:
            raise SystemExit(
                f"{name}: expected {expected} hit(s) of {old!r}, found {count}"
            )
        text = text.replace(old, new)
    (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"WROTE {name}")


stage = (OUT / "stage-v8048-20261030.sh").read_text(encoding="utf-8")
rewrite("stage-v8049-20261101.sh", stage, [
    (f"eceasy/cli-proxy-api:v8.0.48@sha256:{CUR_DIGEST}",
     f"eceasy/cli-proxy-api:v8.0.49@sha256:{DIGEST}", 1),
    (OLD_V, NEW_V, 1),
    (OLD_WB, NEW_WB, 1),
])

accept = (OUT / "accept-v8048-20261030.sh").read_text(encoding="utf-8")
rewrite("accept-v8049-20261101.sh", accept, [
    (OLD_V, NEW_V, 2),
])

load = (OUT / "plugin-load-v8048-20261030.sh").read_text(encoding="utf-8")
rewrite("plugin-load-v8049-20261101.sh", load, [
    (OLD_V, NEW_V, 2),
    ("version=0.9.81", f"version={PLUGIN_VERSION}", 1),
])

deploy = (OUT / "deploy-cpa-v8048-canary-20261030.sh").read_text(encoding="utf-8")
rewrite("deploy-cpa-v8049-canary-20261101.sh", deploy, [
    ("# Authorized bwg v8.0.48 host-only canary, 2026-10-30.",
     "# Authorized bwg v8.0.49 host-only canary, 2026-11-01.", 1),
    ("# Plugin stays at the in-production WorkBuddy 0.9.81 build from upstream v0.8.89;",
     "# Plugin stays at the in-production WorkBuddy 0.9.82 build from upstream v0.8.89;", 1),
    # Anchored to the physical line so the bare DIGEST= rewrite below cannot
    # also hit the CUR_DIGEST= line once CUR_DIGEST has been rewritten.
    ("\nCUR=v8.0.47\n", f"\nCUR={CUR}\n", 1),
    ("\nCUR_DIGEST=sha256:d1874eba7f9819f3a19a0fea0333b457d6fdd4cf290d4bfe4283be102dc489d9\n",
     f"\nCUR_DIGEST=sha256:{CUR_DIGEST}\n", 1),
    ("\nTARGET=v8.0.48\n", f"\nTARGET={TARGET}\n", 1),
    # The v8.0.48 deploy script already pins DIGEST to the current digest
    # (c198886a...), so anchor the replacement to that exact prior value.
    ("\nDIGEST=sha256:c198886a86b57f49cb2508a68b6170e23b2d509c648934af5d5dd827cf6faa16\n",
     f"\nDIGEST=sha256:{DIGEST}\n", 1),
    ("PLUGIN_SHA=36d8b72097845f472ae51e2d579185e579878a0ec564e676be3008520bedf1dc",
     f"PLUGIN_SHA={PLUGIN_SHA}", 1),
    ("PLUGIN_VERSION=0.9.81", f"PLUGIN_VERSION={PLUGIN_VERSION}", 1),
])
