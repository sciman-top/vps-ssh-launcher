"""Bounded CPA readiness/generation check; never print response bodies or keys."""

import json
import sys
import time
import urllib.error
import urllib.request


def check(config, mode, request=None, sleep=time.sleep):
    if request is None:

        def request(path, body=None):
            req = urllib.request.Request(
                "http://127.0.0.1:8317/v1/" + path,
                data=json.dumps(body).encode() if body else None,
                headers={
                    "Authorization": "Bearer " + config["api-keys"][0],
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=65 if body else 5) as response:
                return json.load(response)

    # Bare-catalog contract (2026-09-15 gateway split + GLM alias): luna from
    # the OAuth credential (luna-only allowlist), sol/terra/astra bare from
    # the prefix-less ai.input.im relay entry, glm-5.3-flash and its gpt-5.5
    # alias from zhipu-plan. luna is also the generation smoke target (the
    # relay may be down while the OAuth channel is alive).
    allowed = {
        "glm-5.3-flash",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
    }
    catalog_seen = False
    for attempt in range(15):
        try:
            catalog = request("models")
            if not isinstance(catalog.get("data"), list):
                return 20
            ids = {m["id"] for m in catalog["data"]}
            catalog_seen = True
            bare = {m for m in ids if "/" not in m}
            if bare - allowed:
                return 20
            if bare == allowed:
                break
        except Exception:
            pass
        if attempt == 14:
            # Cooling credentials can disappear from /models. A valid, non-
            # exposing catalog proves local readiness, not provider availability.
            return (0 if mode == "readiness" else 10) if catalog_seen else 20
        sleep(2)
    if mode == "readiness":
        return 0
    # Exercise every distinct locally exposed route. The alias gpt-5.5 shares
    # the GLM route and is therefore covered by its canonical model smoke.
    generation_targets = (
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "glm-5.3-flash",
    )
    try:
        for model in generation_targets:
            data = request(
                "chat/completions",
                {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "Reply with exactly: OK"}
                    ],
                    "max_tokens": 256,
                },
            )
            if data.get("error"):
                return 10
            choice = data["choices"][0]
            if (
                choice["message"]["content"].strip() != "OK"
                or choice.get("finish_reason") != "stop"
            ):
                return 20
        return 0
    except urllib.error.HTTPError as error:
        return 10 if error.code in (401, 403, 408, 429, 500, 502, 503, 504) else 20
    except (urllib.error.URLError, TimeoutError):
        return 10
    except Exception:
        return 20


if __name__ == "__main__":
    import yaml

    try:
        with open("/opt/cliproxyapi/config.yaml") as handle:
            config = yaml.safe_load(handle)
        result = check(config, sys.argv[1])
    except Exception:
        result = 20
    print(
        {0: "HEALTH_OK", 10: "UPSTREAM_UNAVAILABLE", 20: "LOCAL_CONTRACT_FAILED"}[
            result
        ]
    )
    raise SystemExit(result)
