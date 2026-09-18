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


def _parse_quality_payload(content):
    """Accept raw JSON or one Markdown JSON fence without accepting prose."""
    if not isinstance(content, str):
        return None
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in ("```", "```json"):
            return None
        if lines[-1].strip() != "```":
            return None
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


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
            with _local_opener().open(req, timeout=120 if body else 5) as response:
                return json.load(response)

    # Bare-catalog contract (revised 2026-09-18 evening): the ai.input.im r1
    # relay and its prefixed view are gone; Sol and Terra are explicitly
    # registered from the relay-8003 entry (its other 37 catalog models stay
    # hidden), Luna is the only open model on the re-enrolled ChatGPT Plus
    # OAuth slot, GLM comes from zhipu-plan, and DeepSeek-flash is the only
    # open model on the official DeepSeek API entry (deepseek-v4-pro disabled
    # by user decision the same day). gpt-6-astra died with the r1 relay.
    # OAuth is deliberately not a runtime dependency.
    allowed = {
        "glm-5.3-flash",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "deepseek-flash",
    }
    # Soft relay leg (2026-09-18): pure observability for the relay-8003
    # routes. Never a decision input — the updater logs the RELAY_DEGRADED
    # marker and moves on without deferring or rolling back. Every failure
    # mode converges to 11; local contract problems stay readiness/generation's
    # job. Single-shot per model, transient responses are never retried.
    if mode == "relay-soft":
        try:
            catalog = request("models")
            ids = (
                {m["id"] for m in catalog["data"]}
                if isinstance(catalog.get("data"), list)
                else set()
            )
            if not {"gpt-5.6-sol", "gpt-5.6-terra"} <= ids:
                return 11
            for model in ("gpt-5.6-sol", "gpt-5.6-terra"):
                data = request(
                    "chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {"role": "user", "content": "Reply with exactly: OK"}
                        ],
                        "max_tokens": 1024,
                    },
                )
                if data.get("error"):
                    return 11
                choice = data["choices"][0]
                if (
                    choice["message"]["content"].strip() != "OK"
                    or choice.get("finish_reason") != "stop"
                    or data.get("model") != model
                ):
                    return 11
            return 0
        except Exception:
            return 11
    catalog_seen = False
    catalog_transient_seen = False
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
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_CODES:
                return 20
            catalog_transient_seen = True
        except (urllib.error.URLError, TimeoutError):
            # A catalog endpoint that is unreachable is an upstream/readiness
            # problem, not proof that the local model contract is malformed.
            catalog_transient_seen = True
        except Exception:
            pass
        if attempt == 14:
            # Cooling credentials can disappear from /models. A valid, non-
            # exposing catalog proves local readiness, not provider availability.
            if catalog_seen:
                return 0 if mode == "readiness" else 10
            # Never turn a catalog-wide 408/429/5xx/network outage into a
            # local-contract failure. The updater can then perform exactly one
            # readiness recheck without sending another generation request.
            return 10 if catalog_transient_seen else 20
        sleep(2)
    if mode == "readiness":
        return 0
    # Keep scheduled maintenance low-frequency. The default gate target is
    # Luna on the owner's ChatGPT Plus OAuth slot (user decision 2026-09-18):
    # low latency, and it exercises the OAuth pipeline that most needs
    # monitoring. It also degrades gracefully: before re-enrollment Luna is
    # absent, so the catalog never completes and generation defers with 10
    # instead of misreading a provider absence as a local failure. Transient
    # 408/429/5xx are never retried (runbook contract). relay-8003 small-prompt
    # latency swings 5s -> 120s+, so Sol stays out of the auto gate and in the
    # explicit matrix below.
    generation_targets = ("gpt-5.6-luna",)
    if (
        mode in ("generation-all", "quality-canary")
        or os.environ.get("CPA_HEALTH_ALL_ROUTES") == "1"
    ):
        generation_targets = (
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "glm-5.3-flash",
            "deepseek-flash",
        )
    expected_models = {
        "gpt-5.6-luna": {"gpt-5.6-luna"},
        "gpt-5.6-sol": {"gpt-5.6-sol"},
        "gpt-5.6-terra": {"gpt-5.6-terra"},
        "glm-5.3-flash": {"glm-5.3-flash"},
        "deepseek-flash": {"deepseek-flash"},
    }
    try:
        for model in generation_targets:
            # Flat 1024 budget: gpt-5.6/deepseek family models can spend the
            # whole allowance on hidden reasoning before emitting content
            # (2026-09-18 deepseek verdict); a too-tight cap reads as an empty
            # finish=length reply and misclassifies as a contract failure.
            budget = 1024
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": budget,
            }
            if mode == "quality-canary":
                body["messages"] = [
                    {
                        "role": "user",
                        "content": (
                            'Return only JSON: {"sum":42,"word":"canary"}. '
                            "Compute 19 + 23 before responding."
                        ),
                    }
                ]
            data = request(
                "chat/completions",
                body,
            )
            if data.get("error"):
                return 10
            choice = data["choices"][0]
            content = choice["message"]["content"].strip()
            content_matches = content == "OK"
            if mode == "quality-canary":
                semantic_result = _parse_quality_payload(content)
                content_matches = semantic_result == {"sum": 42, "word": "canary"}
            if (
                not content_matches
                or choice.get("finish_reason") != "stop"
                or data.get("model") not in expected_models[model]
            ):
                return 20
        return 0
    except urllib.error.HTTPError as error:
        # The catalog already accepted the local client key. A relay 403 during
        # an explicit per-route canary is an unavailable upstream route, not a
        # local configuration failure.
        if mode == "quality-canary" and error.code == 403:
            return 10
        return 10 if error.code in TRANSIENT_HTTP_CODES else 20
    except (urllib.error.URLError, TimeoutError):
        return 10
    except Exception:
        return 20


_EXIT_LABELS = {
    0: "HEALTH_OK",
    10: "UPSTREAM_UNAVAILABLE",
    11: "RELAY_DEGRADED",
    20: "LOCAL_CONTRACT_FAILED",
}


if __name__ == "__main__":
    import yaml

    try:
        with open("/opt/cliproxyapi/config.yaml") as handle:
            config = yaml.safe_load(handle)
        result = check(config, sys.argv[1])
    except Exception:
        result = 20
    print(_EXIT_LABELS.get(result, "LOCAL_CONTRACT_FAILED"))
    raise SystemExit(result)
