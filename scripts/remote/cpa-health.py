"""Bounded CPA readiness/generation check; never print response bodies or keys."""

import json
import sys
import time
import urllib.error
import urllib.request
import os


TRANSIENT_HTTP_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
    520,
    521,
    522,
    523,
    524,
    525,
    526,
}


def _local_opener() -> urllib.request.OpenerDirector:
    """Keep loopback health traffic off ambient HTTP(S)_PROXY settings."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
            with _local_opener().open(req, timeout=65 if body else 5) as response:
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
    # Keep the scheduled updater low-frequency: one representative OAuth route
    # proves CPA generation without spending every provider route's quota.
    # Operators can explicitly request the bounded route matrix for acceptance.
    generation_targets = ("gpt-5.6-luna",)
    if mode == "generation-all" or os.environ.get("CPA_HEALTH_ALL_ROUTES") == "1":
        generation_targets = (
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-6-astra",
            "glm-5.3-flash",
        )
    expected_models = {
        "gpt-5.6-luna": {"gpt-5.6-luna"},
        "gpt-5.6-sol": {"gpt-5.6-sol"},
        "gpt-5.6-terra": {"gpt-5.6-terra"},
        "gpt-6-astra": {"gpt-6-astra"},
        "glm-5.3-flash": {"glm-5.3-flash"},
    }
    try:
        for model in generation_targets:
            budget = 1024 if model == "glm-5.3-flash" else 256
            data = request(
                "chat/completions",
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "max_tokens": budget,
                },
            )
            if data.get("error"):
                return 10
            choice = data["choices"][0]
            if (
                choice["message"]["content"].strip() != "OK"
                or choice.get("finish_reason") != "stop"
                or data.get("model") not in expected_models[model]
            ):
                return 20
        return 0
    except urllib.error.HTTPError as error:
        return 10 if error.code in TRANSIENT_HTTP_CODES else 20
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
