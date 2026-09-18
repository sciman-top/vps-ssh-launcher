"""Bounded CPA readiness/generation check; never print response bodies or keys."""

import json
import sys
import time
import urllib.error
import urllib.request
import os
import uuid


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

_CACHE_CANARY_PREFIX = "\n".join(
    "CPA cache canary invariant: preserve this exact public, non-sensitive sentence."
    for _ in range(256)
)

_QUALITY_EVAL_CASES = (
    {
        "id": "reasoning-json-v1",
        "messages": [
            {
                "role": "user",
                "content": (
                    'Return only JSON: {"result":444,"label":"reasoning"}. '
                    "Compute (19 * 23) + 7 before responding."
                ),
            }
        ],
        "expected": {"result": 444, "label": "reasoning"},
    },
    {
        "id": "instruction-json-v1",
        "messages": [
            {
                "role": "user",
                "content": (
                    'Return only JSON: {"first":"alpha","count":3}. Do not add '
                    "fields, prose, Markdown, or explanations."
                ),
            }
        ],
        "expected": {"first": "alpha", "count": 3},
    },
    {
        "id": "tool-structure-v1",
        "messages": [
            {
                "role": "user",
                "content": "Use the lookup tool exactly once for id canary-42.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Return a test record by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "expected_tool": "lookup",
        "expected_arguments": {"id": "canary-42"},
    },
    {
        "id": "long-context-json-v1",
        "messages": [
            {
                "role": "system",
                "content": "\n".join(
                    f"record-{index:03d}=value-{index:03d}" for index in range(1, 161)
                ),
            },
            {
                "role": "user",
                "content": (
                    'Return only JSON: {"record":"record-127","value":"value-127"}. '
                    "Read the supplied records before responding."
                ),
            },
        ],
        "expected": {"record": "record-127", "value": "value-127"},
    },
)


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


def _loopback_request(config):
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

    return request


def _nonnegative_int(value):
    return value if type(value) is int and value >= 0 else None


def _first_metric(mappings, names):
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        for name in names:
            value = _nonnegative_int(mapping.get(name))
            if value is not None:
                return value
    return None


def _cache_metrics(data):
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    input_details = usage.get("input_tokens_details")
    mappings = (usage, prompt_details, input_details)
    metrics = {
        "input_tokens": _first_metric(mappings, ("prompt_tokens", "input_tokens")),
        "cache_read_tokens": _first_metric(
            mappings,
            (
                "prompt_cache_hit_tokens",
                "cache_read_tokens",
                "cached_tokens",
            ),
        ),
        "cache_write_tokens": _first_metric(
            mappings,
            ("cache_creation_tokens", "cache_write_tokens"),
        ),
        "cache_miss_tokens": _first_metric(
            mappings,
            ("prompt_cache_miss_tokens",),
        ),
    }
    return metrics if any(value is not None for value in metrics.values()) else None


def _format_cache_metrics(sample, metrics):
    fields = ["CACHE_CANARY", f"sample={sample}"]
    for name in (
        "input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_miss_tokens",
    ):
        value = metrics.get(name)
        fields.append(f"{name}={value if value is not None else 'unknown'}")
    input_tokens = metrics.get("input_tokens")
    cache_read_tokens = metrics.get("cache_read_tokens")
    if input_tokens and cache_read_tokens is not None:
        fields.append(f"hit_ratio={cache_read_tokens / input_tokens:.4f}")
    else:
        fields.append("hit_ratio=unknown")
    return " ".join(fields)


def check(config, mode, request=None, sleep=time.sleep):
    if request is None:
        request = _loopback_request(config)

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
    catalog_complete = False
    # A 200 catalog can lag while credentials finish registering, so allow a
    # short bounded convergence window. Provider-side transient responses are
    # deliberately single-shot: retrying them here turns a rate-limit signal
    # into a health-check amplification loop.
    for attempt in range(3):
        try:
            catalog = request("models")
            if not isinstance(catalog, dict) or not isinstance(
                catalog.get("data"), list
            ):
                return 20
            if not all(
                isinstance(model, dict) and isinstance(model.get("id"), str)
                for model in catalog["data"]
            ):
                return 20
            ids = {model["id"] for model in catalog["data"]}
            # The current BWG contract exposes exactly the five approved bare
            # IDs. Unknown prefixes are not an alternate namespace; they are
            # unexpected routes and must fail closed.
            if ids - allowed:
                return 20
            if ids == allowed:
                catalog_complete = True
                break
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_CODES:
                return 20
            return 10
        except (urllib.error.URLError, TimeoutError):
            # A catalog endpoint that is unreachable is an upstream/readiness
            # problem, not proof that the local model contract is malformed.
            return 10
        except (KeyError, TypeError, ValueError):
            return 20
        if attempt < 2:
            sleep(2)
    if not catalog_complete:
        # Cooling credentials can disappear from /models. A valid, non-
        # exposing catalog proves local readiness, not provider availability.
        return 0 if mode == "readiness" else 10
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
        mode in ("generation-all", "quality-canary", "quality-eval")
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
    if mode == "quality-eval":
        return _quality_eval(request, generation_targets, expected_models)
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


def _quality_eval(request, generation_targets, expected_models):
    try:
        for model in generation_targets:
            for case in _QUALITY_EVAL_CASES:
                body = {
                    "model": model,
                    "messages": case["messages"],
                    "max_tokens": 1024,
                }
                if "tools" in case:
                    body["tools"] = case["tools"]
                    body["tool_choice"] = case["tool_choice"]
                data = request("chat/completions", body)
                if data.get("error") or data.get("model") not in expected_models[model]:
                    return 10 if data.get("error") else 20
                choice = data["choices"][0]
                if "expected" in case:
                    if (
                        choice.get("finish_reason") != "stop"
                        or _parse_quality_payload(choice["message"]["content"])
                        != case["expected"]
                    ):
                        return 20
                    continue
                tool_calls = choice["message"].get("tool_calls")
                if (
                    choice.get("finish_reason") != "tool_calls"
                    or not isinstance(tool_calls, list)
                    or len(tool_calls) != 1
                ):
                    return 20
                function = tool_calls[0].get("function")
                if not isinstance(function, dict) or function.get("name") != case[
                    "expected_tool"
                ]:
                    return 20
                if json.loads(function.get("arguments", "")) != case["expected_arguments"]:
                    return 20
        return 0
    except urllib.error.HTTPError as error:
        return 10 if error.code in TRANSIENT_HTTP_CODES else 20
    except (urllib.error.URLError, TimeoutError):
        return 10
    except Exception:
        return 20


def cache_canary(config, request=None, sleep=time.sleep):
    """Measure one provider's cache metadata without exposing request content."""

    if request is None:
        request = _loopback_request(config)
    readiness = check(config, "readiness", request, sleep)
    if readiness != 0:
        return readiness, []
    body = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": _CACHE_CANARY_PREFIX},
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        # Same ephemeral session for both calls makes routing affinity explicit
        # without deriving or logging a reusable cross-user cache key.
        "session_id": f"cpa-cache-canary-{uuid.uuid4().hex}",
        # Keep the same ceiling as the other semantic checks: some models can
        # spend a small cap entirely on hidden reasoning and return a false
        # finish=length failure before emitting the fixed "OK" response.
        "max_tokens": 1024,
    }
    metrics = []
    try:
        for _ in range(2):
            data = request("chat/completions", body)
            if data.get("error"):
                return 10, metrics
            choice = data["choices"][0]
            if (
                choice["message"]["content"].strip() != "OK"
                or choice.get("finish_reason") != "stop"
                or data.get("model") != "deepseek-flash"
            ):
                return 20, metrics
            sample_metrics = _cache_metrics(data)
            if sample_metrics is None:
                return 12, metrics
            metrics.append(sample_metrics)
        return 0, metrics
    except urllib.error.HTTPError as error:
        return (10 if error.code in TRANSIENT_HTTP_CODES else 20), metrics
    except (urllib.error.URLError, TimeoutError):
        return 10, metrics
    except Exception:
        return 20, metrics


_EXIT_LABELS = {
    0: "HEALTH_OK",
    10: "UPSTREAM_UNAVAILABLE",
    11: "RELAY_DEGRADED",
    12: "CACHE_TELEMETRY_UNAVAILABLE",
    20: "LOCAL_CONTRACT_FAILED",
}


if __name__ == "__main__":
    import yaml

    try:
        with open("/opt/cliproxyapi/config.yaml") as handle:
            config = yaml.safe_load(handle)
        mode = sys.argv[1]
        if mode == "cache-canary":
            result, metrics = cache_canary(config)
            for sample, metric in enumerate(metrics, start=1):
                print(_format_cache_metrics(sample, metric))
        else:
            result = check(config, mode)
    except Exception:
        result = 20
    print(_EXIT_LABELS.get(result, "LOCAL_CONTRACT_FAILED"))
    raise SystemExit(result)
