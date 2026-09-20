import ast
import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, cast


class ScriptValidationTests(unittest.TestCase):
    def test_cpa_health_classifies_overload_and_model_exposure(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    "glm-5.3-flash",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                ]
            ]
        }
        for code, expected in [
            (401, 20),
            # The catalog already accepted CPA's local client key; a per-route
            # 403 is therefore an upstream account/route decision.
            (403, 10),
            (429, 10),
            (503, 10),
            (520, 10),
            (526, 10),
            (400, 20),
        ]:
            with self.subTest(code=code):
                req = mock.Mock(
                    side_effect=[
                        catalog,
                        urllib.error.HTTPError("", code, "", Message(), None),
                    ]
                )
                self.assertEqual(check({}, "generation", req, mock.Mock()), expected)
        for code in [408, 429, 500, 502, 503, 504, 520, 526]:
            with self.subTest(catalog_code=code):
                req = mock.Mock(
                    side_effect=[urllib.error.HTTPError("", code, "", Message(), None)]
                )
                self.assertEqual(check({}, "generation", req, mock.Mock()), 10)
                self.assertEqual(req.call_count, 1)
                req = mock.Mock(
                    side_effect=[urllib.error.HTTPError("", code, "", Message(), None)]
                )
                self.assertEqual(check({}, "readiness", req, mock.Mock()), 10)
                self.assertEqual(req.call_count, 1)
        req = mock.Mock(
            return_value={"data": catalog["data"] + [{"id": "gpt-unexpected"}]}
        )
        self.assertEqual(check({}, "readiness", req, mock.Mock()), 20)
        self.assertEqual(req.call_count, 1)
        req = mock.Mock(
            return_value={"data": catalog["data"] + [{"id": "r1/unknown-model"}]}
        )
        self.assertEqual(check({}, "readiness", req, mock.Mock()), 20)
        self.assertEqual(req.call_count, 1)
        for mode, expected in [("readiness", 0), ("generation", 10)]:
            request = mock.Mock(return_value={"data": []})
            self.assertEqual(check({}, mode, request, mock.Mock()), expected)
        request = mock.Mock(return_value={"error": "invalid response"})
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 20)

    def test_cpa_health_loopback_request_bypasses_ambient_proxy(self) -> None:
        import io
        import json
        import runpy
        import urllib.request

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": model}
                for model in [
                    "glm-5.3-flash",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                ]
            ]
        }
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(catalog).encode())
        response.__exit__.return_value = False
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener) as build:
            self.assertEqual(
                check({"api-keys": ["SECRET"]}, "readiness"),
                0,
            )
        build.assert_called_once()
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})
        opener.open.assert_called_once()

    def test_cpa_health_classifies_malformed_2xx_as_upstream_unavailable(self) -> None:
        import io
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        protocol_error = script["UpstreamProtocolError"]

        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b"not-json")
        response.__exit__.return_value = False
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(
                check({"api-keys": ["SECRET"]}, "readiness"),
                10,
            )

        catalog = {
            "data": [
                {"id": model}
                for model in (
                    "glm-5.3-flash",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                )
            ]
        }
        malformed = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "generation-all", malformed, mock.Mock()), 10)
        self.assertEqual(malformed.call_count, 2)

        relay = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "relay-soft", relay, mock.Mock()), 11)
        self.assertEqual(relay.call_count, 2)

    def test_cpa_policy_rejects_nested_retry_and_quota_fallback_overrides(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa_policy.py")
        )
        config = cast(
            dict[str, Any],
            {
                "host": "0.0.0.0",
                "port": 8317,
                "force-model-prefix": True,
                "request-retry": 0,
                "max-retry-credentials": 1,
                "disable-cooling": False,
                "save-cooldown-status": False,
                "transient-error-cooldown-seconds": 60,
                "routing": {
                    "strategy": "fill-first",
                    "session-affinity": True,
                    "session-affinity-ttl": "1h",
                    "session-affinity-subagents": False,
                },
                "quota-exceeded": {
                    "switch-project": False,
                    "switch-preview-model": False,
                    "antigravity-credits": False,
                },
                "codex": {
                    "stream-bootstrap-buffering": True,
                    "stream-bootstrap-timeout": "20s",
                },
                "openai-compatibility": [
                    {
                        "name": "fixture-glm",
                        "base-url": "https://open.bigmodel.cn/api/coding/paas/v4",
                        "api-key-entries": [{"api-key": "GLM_TEST_KEY"}],
                        "models": [{"name": "glm-5.3-flash", "alias": "glm-5.3-flash"}],
                        "request-retry": 0,
                        "disable-cooling": False,
                        "support-prompt-cache-key": False,
                    },
                    {
                        "name": "ai.input.im",
                        "base-url": "https://ai.input.im/v1",
                        "api-key-entries": [{"api-key": "AI_TEST_KEY"}],
                        "models": [
                            {"name": "gpt-5.6-sol", "alias": "gpt-5.6-sol"},
                            {"name": "gpt-5.6-terra", "alias": "gpt-5.6-terra"},
                        ],
                    },
                    {
                        "name": "deepseek",
                        "base-url": "https://api.deepseek.com",
                        "api-key-entries": [{"api-key": "DEEPSEEK_TEST_KEY"}],
                        "models": [
                            {"name": "deepseek-flash", "alias": "deepseek-flash"}
                        ],
                    },
                ],
            },
        )
        self.assertEqual(policy["validate_config"](config), [])
        config["openai-compatibility"][0]["request-retry"] = 1
        issues = policy["validate_config"](config)
        self.assertTrue(any("request-retry" in issue for issue in issues))
        config["openai-compatibility"][0]["request-retry"] = 0
        config["quota-exceeded"]["switch-project"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("switch-project" in issue for issue in issues))
        config["quota-exceeded"]["switch-project"] = False
        config["routing"]["session-affinity-subagents"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("session-affinity-subagents" in issue for issue in issues))
        config["routing"]["session-affinity-subagents"] = False
        config["codex"]["stream-bootstrap-timeout"] = "30s"
        issues = policy["validate_config"](config)
        self.assertTrue(any("stream-bootstrap-timeout" in issue for issue in issues))
        config["codex"]["stream-bootstrap-timeout"] = "20s"
        config["openai-compatibility"][1]["disabled"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("ai.input.im.disabled" in issue for issue in issues))
        config["openai-compatibility"][1].pop("disabled")
        config["openai-compatibility"][1]["base-url"] = "https://ai.input.im"
        issues = policy["validate_config"](config)
        self.assertTrue(any("ai.input.im.base-url" in issue for issue in issues))
        config["openai-compatibility"][1]["base-url"] = "https://ai.input.im/v1"
        config["openai-compatibility"][1]["base-url"] = "http://35.213.82.91:8003/v1"
        issues = policy["validate_config"](config)
        self.assertTrue(any("35.213.82.91:8003" in issue for issue in issues))

        config["openai-compatibility"][1]["base-url"] = "https://ai.input.im/v1"
        for invalid_url in (
            "http://ai.input.im/v1",
            "https://user:pass@ai.input.im/v1",
            "https://ai.input.im:8443/v1",
            "https://ai.input.im/v1?x=1",
            "https://ai.input.im/v2",
        ):
            config["openai-compatibility"][1]["base-url"] = invalid_url
            with self.subTest(invalid_url=invalid_url):
                self.assertTrue(
                    any(
                        "base-url must be exact" in issue
                        for issue in policy["validate_config"](config)
                    )
                )
        config["openai-compatibility"][1]["base-url"] = "https://ai.input.im/v1"
        config["openai-compatibility"][1]["api-key-entries"] = [
            {"api-key": "AI_TEST_KEY"},
            {"api-key": "AI_TEST_KEY_2"},
        ]
        self.assertTrue(
            any(
                "exactly one entry" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["openai-compatibility"][1]["api-key-entries"] = [
            {"api-key": "AI_TEST_KEY"}
        ]
        config["openai-compatibility"][1]["headers"] = {"X-Test": "blocked"}
        self.assertTrue(
            any(
                "headers transport override" in issue
                for issue in policy["validate_config"](config)
            )
        )

    def test_cpa_updater_waits_for_auth_registration_without_generation_retry(
        self,
    ) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    "glm-5.3-flash",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                ]
            ]
        }
        smoke = {
            "model": "gpt-5.6-luna",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        for final, expected in [
            (smoke, 0),
            ({"error": {"code": "rate_limit"}}, 10),
            ({}, 20),
        ]:
            with self.subTest(expected=expected):
                responses = [{"data": []}, catalog]
                responses.extend([final] * (1 if expected == 0 else 1))
                request = mock.Mock(side_effect=responses)
                sleep = mock.Mock()
                self.assertEqual(check({}, "generation", request, sleep), expected)
                self.assertEqual(request.call_count, 3)
                sleep.assert_called_once_with(2)
                self.assertEqual(
                    sum(len(c.args) > 1 for c in request.call_args_list),
                    1,
                )
                body = next(
                    call.args[1]
                    for call in request.call_args_list
                    if len(call.args) > 1
                )
                self.assertEqual(body["model"], "gpt-5.6-luna")

    def test_cpa_health_relay_soft_is_observability_only(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        script = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        self.assertEqual(script["_EXIT_LABELS"][11], "RELAY_DEGRADED")
        self.assertEqual(script["_EXIT_LABELS"][13], "RELAY_DISABLED")
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    "glm-5.3-flash",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                ]
            ]
        }
        ok_sol = {
            "model": "gpt-5.6-sol",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        ok_terra = {
            "model": "gpt-5.6-terra",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        request = mock.Mock(side_effect=[catalog, ok_sol, ok_terra])
        self.assertEqual(check({}, "relay-soft", request, mock.Mock()), 0)
        disabled_request = mock.Mock()
        self.assertEqual(
            check(
                {"openai-compatibility": [{"name": "ai.input.im", "disabled": True}]},
                "relay-soft",
                disabled_request,
                mock.Mock(),
            ),
            13,
        )
        disabled_request.assert_not_called()
        for case_number, responses in enumerate(
            [
                [catalog, {"error": {"code": "upstream"}}],
                [catalog, urllib.error.HTTPError("", 503, "", Message(), None)],
                [catalog, TimeoutError()],
                [
                    catalog,
                    {
                        "model": "gpt-5.6-sol",
                        "choices": [
                            {"message": {"content": "nope"}, "finish_reason": "stop"}
                        ],
                    },
                ],
                [{"data": [{"id": "glm-5.3-flash"}]}],
                [TimeoutError()],
            ],
            start=1,
        ):
            with self.subTest(case=case_number):
                request = mock.Mock(side_effect=responses)
                self.assertEqual(check({}, "relay-soft", request, mock.Mock()), 11)

    def test_cpa_health_all_routes_is_explicit_and_budgeted(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        models = [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "glm-5.3-flash",
            "deepseek-flash",
        ]
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    *models,
                ]
            ]
        }
        responses: list[object] = [catalog]
        responses.extend(
            {
                "model": model,
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            {item["id"] for item in catalog["data"]},
        )
        broken_last_route = mock.Mock(
            side_effect=[*responses[:-1], RuntimeError("route unavailable")]
        )
        self.assertEqual(
            check({}, "generation-all", broken_last_route, mock.Mock()), 20
        )

        budgets = {
            call.args[1]["model"]: call.args[1]["max_tokens"]
            for call in request.call_args_list[1:]
        }
        self.assertTrue(budgets)
        self.assertEqual(set(budgets.values()), {1024})

    def test_cpa_health_generation_all_reports_every_route_after_failure(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        models = [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "glm-5.3-flash",
            "deepseek-flash",
        ]
        catalog = {"data": [{"id": model} for model in models]}
        responses: list[object] = [catalog]
        responses.extend(
            urllib.error.HTTPError("", 502, "", Message(), None)
            if model == "gpt-5.6-sol"
            else {
                "model": model,
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        lines: list[str] = []

        self.assertEqual(
            check({}, "generation-all", request, mock.Mock(), lines.append),
            10,
        )
        self.assertEqual(request.call_count, 1 + len(models))
        self.assertEqual(len(lines), len(models))
        self.assertTrue(
            any(
                line.startswith("GENERATION model=gpt-5.6-sol status=502 latency_ms=")
                and line.endswith("error_class=transient_upstream")
                for line in lines
            )
        )
        for model in models:
            self.assertTrue(any(f"model={model} " in line for line in lines))
        self.assertTrue(
            any(
                "model=deepseek-flash status=200" in line and "finish=stop" in line
                for line in lines
            )
        )

    def test_cpa_health_quality_canary_requires_semantic_response_per_route(
        self,
    ) -> None:
        import json
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        models = [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "glm-5.3-flash",
            "deepseek-flash",
        ]
        catalog = {
            "data": [
                {"id": model}
                for model in [
                    *models,
                ]
            ]
        }
        responses: list[object] = [catalog]
        responses.extend(
            {
                "model": model,
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                + json.dumps({"sum": 42, "word": "canary"})
                                + "\n```"
                                if model == models[0]
                                else json.dumps({"sum": 42, "word": "canary"})
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "quality-canary", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            {item["id"] for item in catalog["data"]},
        )
        broken_last_route = mock.Mock(
            side_effect=[*responses[:-1], RuntimeError("route unavailable")]
        )
        self.assertEqual(
            check({}, "quality-canary", broken_last_route, mock.Mock()), 20
        )

        self.assertIn(
            "19 + 23", request.call_args_list[1].args[1]["messages"][0]["content"]
        )

        invalid = mock.Mock(
            side_effect=[
                catalog,
                {
                    "model": models[0],
                    "choices": [
                        {
                            "message": {"content": '{"sum":41,"word":"canary"}'},
                            "finish_reason": "stop",
                        }
                    ],
                },
            ]
        )
        self.assertEqual(check({}, "quality-canary", invalid, mock.Mock()), 20)

        relay_denied = mock.Mock(
            side_effect=[
                catalog,
                urllib.error.HTTPError("", 403, "", Message(), None),
            ]
        )
        self.assertEqual(check({}, "quality-canary", relay_denied, mock.Mock()), 10)

    def test_cpa_cache_canary_measures_usage_without_exposing_session_or_prompt(
        self,
    ) -> None:
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )
        cache_canary = script["cache_canary"]
        format_metrics = script["_format_cache_metrics"]
        catalog = {
            "data": [
                {"id": model}
                for model in (
                    "glm-5.3-flash",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                )
            ]
        }
        success = {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1200,
                "prompt_cache_hit_tokens": 900,
                "prompt_cache_miss_tokens": 300,
                "cache_creation_tokens": 300,
            },
        }
        request = mock.Mock(side_effect=[catalog, success, success])
        result, metrics = cache_canary({}, request, mock.Mock())
        self.assertEqual(result, 0)
        self.assertEqual(len(metrics), 2)
        self.assertEqual(metrics[1]["cache_read_tokens"], 900)
        self.assertEqual(metrics[1]["cache_write_tokens"], 300)
        rendered = format_metrics(2, metrics[1])
        self.assertIn("hit_ratio=0.7500", rendered)
        self.assertNotIn("cpa-cache-canary-", rendered)
        self.assertNotIn("Reply with exactly", rendered)

        bodies = [call.args[1] for call in request.call_args_list[1:]]
        self.assertEqual(bodies[0]["session_id"], bodies[1]["session_id"])
        self.assertTrue(bodies[0]["session_id"].startswith("cpa-cache-canary-"))
        self.assertEqual(bodies[0]["messages"], bodies[1]["messages"])
        self.assertEqual(bodies[0]["max_tokens"], 1024)

        no_telemetry = mock.Mock(side_effect=[catalog, {**success, "usage": {}}])
        result, metrics = cache_canary({}, no_telemetry, mock.Mock())
        self.assertEqual(result, 12)
        self.assertEqual(metrics, [])

    def test_cpa_quality_eval_requires_reasoning_instruction_tool_and_context_cases(
        self,
    ) -> None:
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        cases = script["_QUALITY_EVAL_CASES"]
        models = (
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "glm-5.3-flash",
            "deepseek-flash",
        )
        catalog = {"data": [{"id": model} for model in models]}
        responses: list[object] = [catalog]
        for model in models:
            for case in cases:
                if "expected" in case:
                    responses.append(
                        {
                            "model": model,
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(case["expected"])
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                    )
                else:
                    responses.append(
                        {
                            "model": model,
                            "choices": [
                                {
                                    "message": {
                                        "tool_calls": [
                                            {
                                                "function": {
                                                    "name": case["expected_tool"],
                                                    "arguments": json.dumps(
                                                        case["expected_arguments"]
                                                    ),
                                                }
                                            }
                                        ]
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ],
                        }
                    )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "quality-eval", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1 + len(models) * len(cases))

        tool_case = next(case for case in cases if "tools" in case)
        unsupported = script["_FORCED_TOOL_CHOICE_UNSUPPORTED"]
        tool_bodies = [
            call.args[1]
            for call in request.call_args_list[1:]
            if "tools" in call.args[1]
        ]
        self.assertEqual(len(tool_bodies), len(models))
        for body in tool_bodies:
            if body["model"] in unsupported:
                self.assertNotIn("tool_choice", body)
            else:
                self.assertEqual(body["tool_choice"], tool_case["tool_choice"])

        malformed = list(responses)
        malformed[3] = {
            "model": models[0],
            "choices": [
                {
                    "message": {"tool_calls": []},
                    "finish_reason": "tool_calls",
                }
            ],
        }
        self.assertEqual(
            check({}, "quality-eval", mock.Mock(side_effect=malformed), mock.Mock()),
            20,
        )

    def test_cpa_updater_selects_mature_release_without_starvation(self) -> None:
        import contextlib
        import datetime as dt
        import io
        import json
        import tempfile

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        selection = source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        now = dt.datetime.now(dt.timezone.utc)
        old = (now - dt.timedelta(days=4)).isoformat()
        fresh = (now - dt.timedelta(hours=1)).isoformat()
        releases = [
            {"tag_name": tag, "published_at": age, "draft": False, "prerelease": False}
            for tag, age in [
                ("v7.2.159", fresh),
                ("v7.2.156", old),
                ("v7.3.0", old),
                ("v8.0.0", old),
            ]
        ]
        tags = {
            "results": [
                {"name": tag, "last_updated": age, "digest": "sha256:" + "a" * 64}
                for tag, age in [
                    ("v7.2.159", fresh),
                    ("v7.2.156", old),
                    ("v7.3.0", old),
                    ("v8.0.0", old),
                ]
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            for current, expected in [
                ("v7.2.154", "v7.2.156"),
                ("v7.2.156", "v7.2.156"),
                ("v7.2.160", "v7.2.160"),
                ("v7.3.0", "v7.3.0"),
                ("v8.0.0", "v8.0.0"),
            ]:
                with self.subTest(current=current):
                    compose.write_text(f"image: eceasy/cli-proxy-api:{current}\n")
                    responses = [
                        io.BytesIO(json.dumps(v).encode()) for v in (releases, tags)
                    ]
                    output = io.StringIO()
                    with (
                        mock.patch("urllib.request.urlopen", side_effect=responses),
                        mock.patch.object(sys, "argv", ["select", str(compose)]),
                        contextlib.redirect_stdout(output),
                    ):
                        exec(compile(selection, "updater-selection", "exec"), {})
                    self.assertEqual(output.getvalue().split()[:2], [current, expected])
                    minor_expected = ["v7.3.0"] if current.startswith("v7.2.") else []
                    self.assertEqual(
                        [
                            line.split("available=")[-1]
                            for line in output.getvalue().splitlines()
                            if line.startswith("MINOR_CANDIDATE available=")
                        ],
                        minor_expected,
                    )
                    major_expected = [] if current.startswith("v8.") else ["v8.0.0"]
                    self.assertEqual(
                        [
                            line.split("available=")[-1]
                            for line in output.getvalue().splitlines()
                            if line.startswith("MAJOR_CANDIDATE available=")
                        ],
                        major_expected,
                    )

    def test_cpa_updater_prunes_with_bounded_retention_after_success(self) -> None:
        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        self.assertIn('mkdir -m 700 "$BK"', source)
        self.assertNotIn('mkdir -m 700 -p "$BK"', source)
        self.assertIn('cp -a "$DIR/compose.yml" "$BK/"', source)
        self.assertNotIn('"$BK/auth"', source)
        self.assertNotIn("-delete || rollback_failed=1", source)
        self.assertNotIn('cp -a "$BK/config.yaml"', source)
        self.assertNotIn('cp -a "$BK/cpa-health.py"', source)

        # Deletion is bounded: exactly one rm -rf restricted to backup-dir
        # entries collected by find, and one docker rmi restricted to the
        # pinned repo. Anything broader is an unbounded-delete hazard.
        self.assertEqual(source.count("rm -rf"), 1)
        self.assertEqual(source.count("docker rmi"), 1)
        # Pruning runs only on the verified success path; the UNVERIFIED exit-10
        # and rollback paths keep every backup and image.
        ok_log = source.index('log "OK: updated')
        unverified = source.index("UNVERIFIED: upstream unavailable")
        between = source[unverified:ok_log]
        self.assertNotIn("prune_backups", between)
        self.assertNotIn("prune_images", between)
        self.assertIn("\nprune_backups\n", source[ok_log:])
        self.assertIn("\nprune_images\n", source[ok_log:])
        # Error-request dumps are age-bounded hygiene: swept on the daily
        # no-update path too (exit-10 days included), restricted to
        # auth/logs/error-*.log older than 7 days.
        self.assertIn("prune_error_dumps", source[:unverified])
        self.assertIn("\nprune_error_dumps\n", source[ok_log:])
        self.assertIn('find "$DIR/auth/logs"', source)
        self.assertIn("-mmin +10080", source)
        self.assertIn("secure_error_dumps", source)
        self.assertIn('chmod 700 -- "$DIR/auth/logs"', source)
        self.assertIn('chmod 600 -- "$entry"', source)
        # The third-party relay channel must never be auto-probed by the daily
        # timer; relay-soft stays a manual, explicit mode of cpa-health.py.
        self.assertNotIn("relay-soft", source)
        self.assertNotIn("RELAY_SOFT", source)

    def test_cpa_updater_no_update_closes_transient_health_as_unverified(self) -> None:
        import tempfile

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index('if [[ "$CUR" == "$TARGET" ]]')
        end = source.index("\nif ! health generation", start)
        branch = source[start:end]
        with tempfile.TemporaryDirectory():
            harness = "\n".join(
                [
                    "set -u",
                    "CUR=v7.3.7",
                    "TARGET=v7.3.7",
                    'health() { printf "HEALTH_CALL %s\\n" "$1"; if [[ "$1" == generation ]]; then return 10; fi; return 0; }',
                    'log() { printf "%s\\n" "$*"; }',
                    "prune_error_dumps() { :; }",
                    branch,
                ]
            )
            completed = subprocess.run(
                [bash],
                input=harness.encode(),
                capture_output=True,
                timeout=30,
            )
            output = completed.stdout.decode()
            self.assertEqual(completed.returncode, 10, completed.stderr.decode())
            self.assertIn("UNVERIFIED: upstream unavailable; image unchanged", output)
            self.assertIn("readiness=HEALTH_OK", output)
            self.assertEqual(output.count("UNVERIFIED:"), 1)
            self.assertEqual(
                [
                    line
                    for line in output.splitlines()
                    if line.startswith("HEALTH_CALL ")
                ],
                ["HEALTH_CALL generation", "HEALTH_CALL readiness"],
            )

    def test_cpa_updater_post_update_readiness_distinguishes_upstream_and_local_failure(
        self,
    ) -> None:
        import tempfile

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index("RESULT=0\nhealth generation")
        end = source.index('\n[[ "$RESULT" == 0 ]]', start)
        branch = source[start:end]
        for readiness, expected_code, marker in (
            (10, 10, "readiness=UPSTREAM_UNAVAILABLE"),
            (20, 20, "ROLLBACK_CALLED result=20"),
        ):
            with self.subTest(readiness=readiness), tempfile.TemporaryDirectory():
                harness = "\n".join(
                    [
                        "set -u",
                        "TARGET=v7.3.8; BK=/tmp/cpa-test-backup; LOG=/tmp/cpa-test.log",
                        'health() { if [[ "$1" == generation ]]; then return 10; fi; return '
                        f"{readiness}; }}",
                        'log() { printf "%s\\n" "$*"; }',
                        'rollback() { printf "ROLLBACK_CALLED result=%s\\n" "$1"; exit "$1"; }',
                        branch,
                    ]
                )
                completed = subprocess.run(
                    [bash],
                    input=harness.encode(),
                    capture_output=True,
                    timeout=30,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)

    def test_cpa_updater_dump_permissions_gate_blocks_all_provider_traffic(
        self,
    ) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index("if ! secure_error_dumps; then")
        end = source.index('if [[ "$CUR" == "$TARGET" ]]', start)
        gate = source[start:end]
        for dumps_result, expected_code, marker in (
            (1, 1, "DEFER: error-dump permissions unavailable"),
            (0, 0, "OK: no newer mature release"),
        ):
            with self.subTest(dumps_result=dumps_result):
                harness = "\n".join(
                    [
                        "set -u",
                        "CUR=v7.3.7",
                        "TARGET=v7.3.7",
                        f"secure_error_dumps() {{ return {dumps_result}; }}",
                        "prune_error_dumps() { :; }",
                        'health() { printf "HEALTH_CALL %s\\n" "$1"; return 0; }',
                        'log() { printf "%s\\n" "$*"; }',
                        gate,
                        'if [[ "$CUR" == "$TARGET" ]]; then',
                        "  RESULT=0",
                        "  health generation || RESULT=$?",
                        '  [[ "$RESULT" == 0 ]]',
                        '  log "OK: no newer mature release; current=$CUR"',
                        "  exit 0",
                        "fi",
                    ]
                )
                completed = subprocess.run(
                    [bash],
                    input=harness.encode(),
                    capture_output=True,
                    timeout=30,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)
                if dumps_result == 1:
                    self.assertNotIn("HEALTH_CALL", output)

    def test_cpa_doctor_oauth_monitor_block_fails_on_expired_and_refresh_signals(
        self,
    ) -> None:
        import datetime as dt
        import json
        import tempfile

        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        block = (
            source.split('echo "==oauth-monitor=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )

        def oauth_record(expired: str) -> dict[str, Any]:
            # Placeholder token values: the monitor only reads metadata fields.
            return {
                "type": "codex",
                "access_token": "TEST_ACCESS_TOKEN",
                "refresh_token": "TEST_REFRESH_TOKEN",
                "expired": expired,
                "last_refresh": "2026-09-19T20:38:59+08:00",
            }

        now = dt.datetime.now(dt.timezone.utc)
        renewal_window_expiry = (now + dt.timedelta(days=3)).isoformat()
        grace_window_expiry = (now + dt.timedelta(hours=30)).isoformat()
        not_rolled_expiry = (now + dt.timedelta(hours=20)).isoformat()
        refresh_point_expiry = (now + dt.timedelta(hours=12)).isoformat()
        cases: tuple[
            tuple[str, dict[str, dict[str, Any]], dict[str, str], int, str], ...
        ] = (
            (
                "healthy",
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {},
                0,
                "oauth_monitor=OK",
            ),
            (
                "renewal_window_is_not_blocking",
                # 72h out the auto-refresh point is far away: WARN only.
                {"codex-window.json": oauth_record(renewal_window_expiry)},
                {},
                0,
                "oauth_monitor=WARN_RENEWAL_WINDOW",
            ),
            (
                "refresh_grace_not_exhausted_is_not_blocking",
                # Before the 24h auto-refresh point: WARN, not a failure.
                {"codex-grace.json": oauth_record(grace_window_expiry)},
                {},
                0,
                "oauth_monitor=WARN_RENEWAL_WINDOW",
            ),
            (
                "refresh_window_grace_exhausted_requires_action",
                # Under 22h left (24h refresh lead minus 2h grace) without an
                # expiry roll: the auto-refresh failed to fire: blocking.
                {"codex-stale.json": oauth_record(not_rolled_expiry)},
                {},
                1,
                "oauth_monitor=ACTION_REQUIRED_REENROLL_OR_VERIFY_REFRESH",
            ),
            (
                "refresh_point_requires_action",
                # Well past the refresh point without an expiry roll: blocking.
                {"codex-due.json": oauth_record(refresh_point_expiry)},
                {},
                1,
                "oauth_monitor=ACTION_REQUIRED_REENROLL_OR_VERIFY_REFRESH",
            ),
            (
                "expired",
                {"codex-old.json": oauth_record("2020-01-01T00:00:00+00:00")},
                {},
                1,
                "oauth_monitor=FAIL_EXPIRED",
            ),
            (
                "refresh_signal",
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {"error-20260920.log": "invalid_grant: refresh rejected"},
                1,
                "oauth_monitor=FAIL_REFRESH_SIGNAL",
            ),
            (
                "absent",
                {},
                {},
                0,
                "oauth_monitor=ABSENT_OPTIONAL",
            ),
        )
        for name, auth_files, log_files, expected_code, marker in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                auth_dir = Path(directory) / "auth"
                logs_dir = auth_dir / "logs"
                logs_dir.mkdir(parents=True)
                for file_name, record in auth_files.items():
                    (auth_dir / file_name).write_text(
                        json.dumps(record), encoding="utf-8"
                    )
                for file_name, content in log_files.items():
                    (logs_dir / file_name).write_text(content, encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, "-", str(auth_dir), str(logs_dir)],
                    input=block.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)

    def test_cpa_updater_bash_syntax_parses(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        script_path = self._bash_path(
            bash, Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        )
        completed = subprocess.run(
            [
                bash,
                "-n",
                script_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cpa_prune_backups_keeps_newest_backup_dirs(self) -> None:
        import tempfile

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_backups() {") : source.index("prune_images() {")
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "backups"
            root.mkdir()
            for day in range(1, 11):
                (root / f"202609{day:02d}T000000Z-from-v0.0.{day}").mkdir()
            (root / "foreign.txt").write_text("keep me", encoding="utf-8")
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BACKUP_ROOT='{self._bash_path(bash, root)}'",
                    "RETENTION_KEEP_BACKUPS=8",
                    'log() { printf "LOG %s\\n" "$*"; }',
                    function,
                    "prune_backups",
                ]
            )
            completed = subprocess.run(
                [bash],
                input=harness.encode(),
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            remaining = sorted(path.name for path in root.iterdir())
            self.assertEqual(
                remaining,
                [
                    *(
                        f"202609{day:02d}T000000Z-from-v0.0.{day}"
                        for day in range(3, 11)
                    ),
                    "foreign.txt",
                ],
            )
            self.assertIn(
                "LOG PRUNE scope=backups kept=8 removed=2",
                completed.stdout.decode(),
            )

    def test_cpa_prune_images_normalizes_docker_image_ids(self) -> None:
        import tempfile

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_images() {") : source.index(
                '\nif [[ "$CUR" == "$TARGET" ]]'
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            log_path = self._bash_path(bash, Path(directory) / "prune.log")
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    "BK=/backup",
                    "CPA_IMAGE_REPO=eceasy/cli-proxy-api",
                    f"LOG='{log_path}'",
                    "log() { printf 'LOG %s\\n' \"$*\"; }",
                    "secure_error_dumps() { :; }",
                    "prune_error_dumps() { :; }",
                    "compose_image_ref() { printf 'rollback-image\\n'; }",
                    "docker() {",
                    '  if [[ "$1" == inspect && "$2" == --format ]]; then',
                    "    printf 'sha256:%064d\\n' 0 | tr '0' 'a'",
                    '  elif [[ "$1" == image && "$2" == inspect && "$4" == \'{{.ID}}\' ]]; then',
                    "    printf 'sha256:%064d\\n' 0 | tr '0' 'b'",
                    '  elif [[ "$1" == image && "$2" == inspect && "$4" == \'{{.Size}}\' ]]; then',
                    "    printf '123\\n'",
                    '  elif [[ "$1" == images ]]; then',
                    "    printf '%s\\n' 'aaaaaaaaaaaa eceasy/cli-proxy-api:<none>' 'bbbbbbbbbbbb eceasy/cli-proxy-api:<none>' 'cccccccccccc eceasy/cli-proxy-api:<none>'",
                    '  elif [[ "$1" == rmi ]]; then',
                    "    printf 'removed=%s\\n' \"$2\"",
                    "  else",
                    "    return 1",
                    "  fi",
                    "}",
                    function,
                    "prune_images",
                    'cat "$LOG"',
                ]
            )
            completed = subprocess.run(
                [bash],
                input=harness.encode(),
                capture_output=True,
                timeout=30,
            )
            output = completed.stdout.decode()
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertIn("removed=cccccccccccc", output)
            self.assertNotIn("removed=aaaaaaaaaaaa", output)
            self.assertNotIn("removed=bbbbbbbbbbbb", output)
            self.assertIn(
                "PRUNE scope=images kept=2 removed=1 freed_bytes=123",
                output,
            )

    @staticmethod
    def _bash_path(bash: str, path: Path) -> str:
        """Use a path understood by the selected Bash implementation."""
        if os.name != "nt" or not path.drive:
            return str(path)

        # Windows ships a WSL bash launcher that cannot consume Win32 paths.
        # Probe the selected executable instead of assuming every bash.exe is
        # WSL; Git Bash accepts the original path form.
        try:
            probe = subprocess.run(
                [bash, "-c", "test -d /mnt/c"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return str(path)
        if probe.returncode != 0:
            return str(path)

        posix = path.as_posix()
        return f"/mnt/{path.drive[0].lower()}{posix[2:]}"

    @staticmethod
    def _render_embedded_wrapper(source: str, function_name: str) -> str:
        function_start = source.index(f"{function_name}()")
        body_start = source.index("#!/usr/bin/env bash", function_start)
        body_end = source.index("\nEOF", body_start)
        rendered = source[body_start:body_end].replace("`", "")
        return rendered.replace("\r\n", "\n").replace("\r", "\n")

    def test_ssh_tool_direct_execution_invokes_cli(self) -> None:
        repo_root = Path(__file__).resolve().parent
        completed = subprocess.run(
            [sys.executable, str(repo_root / "ssh_tool.py"), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())

    def test_powershell_scripts_parse(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                self._assert_powershell_script_parses(powershell, script_path)

    def test_cpa_guardrails_freezes_public_data_plane_contract(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )

        # Authorization gates: writes only behind -Apply/-RotatePath, and the
        # doctor is a blocking contract, not an observation.
        self.assertIn("[switch]$Observe", text)
        self.assertIn("[switch]$RotatePath", text)
        self.assertIn("STRICT=1", text)
        self.assertIn("DOCTOR_CONTRACT_FAILED", text)
        # Data plane shape: loopback-only container port and no SSH tunnel
        # data plane; the public entry stays nginx 8443 with random path.
        self.assertIn(
            'expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}',
            text,
        )
        self.assertNotIn("ssh -L", text)
        self.assertNotIn("ssh -R", text)
        self.assertNotIn("ssh -D", text)
        # Projection integrity: each embedded payload placeholder must have a
        # matching remote write, or the remote side silently keeps stale code.
        for placeholder in ("__CPA_HEALTH_B64__", "__CPA_UPDATER_B64__"):
            with self.subTest(placeholder=placeholder):
                self.assertIn(placeholder, text)
                self.assertIn(f'write_base64_file "{placeholder}"', text)
        self.assertIn("__CPA_PROVIDER_ENV_B64__", text)
        self.assertIn('"ai.input.im"', text)
        self.assertIn('"open.bigmodel.cn"', text)
        self.assertIn('"api.deepseek.com"', text)
        self.assertIn('legacy_hosts = {"35.213.82.91"}', text)
        self.assertIn("error-dump-permissions=OK", text)
        self.assertIn("oauth_days_left=", text)
        self.assertIn("oauth_hours_left=", text)
        self.assertIn("oauth_refresh_policy=lead24h_grace2h", text)
        self.assertIn("refresh_token_reused", text)
        self.assertIn("oauth_refresh_failures_7d=", text)
        self.assertIn("oauth_monitor=FAIL_REFRESH_SIGNAL", text)
        # Error dump inventory must retain each path alongside its metadata;
        # otherwise the loop repeatedly reads the last path from discovery.
        self.assertIn("candidates.append((path, st.st_mtime, st.st_size))", text)
        self.assertIn("for path, mtime, size in candidates:", text)
        # Gateway status telemetry: 499 client aborts carry request_time stats
        # so a fixed client-side total timeout signature is provable from
        # doctor output alone.
        self.assertIn("client_abort_request_time", text)
        self.assertIn("abort_request_times", text)
        self.assertIn("'p50_s':", text)
        # Silent model substitution telemetry (upstream >= v7.3.8): counted,
        # redaction-safe (no log line text echoed), capability-aware, and
        # observation-grade.
        self.assertIn("==model-substitution==", text)
        self.assertIn("model_substitution_warnings_7d=", text)
        self.assertIn("grep -c 'upstream served model'", text)
        self.assertIn("WARN_SUBSTITUTION_OBSERVED", text)
        self.assertIn("model_substitution=UNAVAILABLE_VERSION", text)
        self.assertIn("sort -V", text)
        self.assertIn("not a strict gate", text)
        self.assertIn("assert_public_route_contract()", text)
        self.assertIn("assert_path_route_contract()", text)
        self.assertIn(
            "PROBE_ALREADY_RUNNING",
            (repo_root / "scripts" / "remote" / "cpa-health.py").read_text(),
        )
        self.assertIn('chmod 700 "$DIR/auth/logs"', text)
        self.assertIn("-name 'error-*.log' -exec chmod 600 -- {} +", text)
        # Random-path rotation must prove old path dead and new path live.
        self.assertIn("OLD_PATH_REVOKED=yes", text)
        self.assertIn("NEW_PATH_ACTIVE=yes", text)
        # Chunked payload execution must fail on EITHER pipeline stage: a
        # corrupted payload fails the base64 decoder, and a failing remote
        # script must keep its own exit code (pipefail $? captures both).
        self.assertIn("set -o pipefail; base64 -d -- '$remoteTemp' | bash; ", text)
        self.assertIn("rc=`$?; rm -f -- '$remoteTemp'; exit `$rc", text)
        # Apply/RotatePath/DeactivateOAuthLuna share the updater flock so the
        # daily timer cannot interleave with a guardrail transaction.
        self.assertEqual(text.count("exec 9>/opt/cliproxyapi/auto-update.lock"), 3)
        self.assertEqual(text.count("flock -n 9"), 3)

    def test_cpa_guardrails_normalizes_crlf_in_remote_payloads(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        function = text.split("function Invoke-BwgRemoteScript", 1)[1].split(
            "$doctorScript = @'", 1
        )[0]
        # gitattributes checks *.ps1 out as CRLF; real Linux bash rejects CR
        # in the projected payload (e.g. "func() {<CR>" is a syntax error), so
        # the payload must be normalized before it is base64-projected.
        self.assertIn(
            '$Script = $Script.Replace("`r`n", "`n").Replace("`r", "`n")',
            function,
        )
        self.assertLess(
            function.index("$Script = $Script.Replace"),
            function.index("UTF8.GetBytes($Script)"),
        )

    def test_cpa_guardrails_payloads_are_valid_bash(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payloads = {
            "doctor": source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0],
            "rotate": source.split("$rotateScript = @'\n", 1)[1].split("\n'@", 1)[0],
            "deactivate_oauth_luna": source.split(
                "$deactivateOAuthLunaScript = @'\n", 1
            )[1].split("\n'@", 1)[0],
            "apply": source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0],
        }
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                completed = subprocess.run(
                    ["bash", "-n"],
                    input=payload.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8", errors="replace"
                )
                self.assertEqual(completed.returncode, 0, output)

    def test_cpa_oauth_luna_deactivation_is_explicit_and_credential_destructive(
        self,
    ) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        # Destructive OAuth removal must exist only behind the explicit
        # switch; the payload itself must never archive the auth directory
        # (credentials would survive in backups).
        self.assertIn("-DeactivateOAuthLuna", source)
        self.assertIn("-not $DeactivateOAuthLuna", source)
        self.assertNotIn('cp -a "$AUTH_DIR"', payload)

    def test_cpa_apply_embedded_python_and_rollback_contract(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        apply_script = text.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        embedded_python = apply_script.split("if ! python3 - <<'PY'\n", 1)[1].split(
            "\nPY\nthen", 1
        )[0]
        tree = ast.parse(embedded_python)
        assigned: set[str] = set()
        loaded: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                (assigned if isinstance(node.ctx, ast.Store) else loaded).add(node.id)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                assigned.add(node.name)
            elif isinstance(node, ast.arg):
                assigned.add(node.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assigned.add(alias.asname or alias.name.split(".")[0])

        self.assertEqual(
            loaded - assigned - set(dir(builtins)),
            set(),
        )
        self.assertNotIn('cp -a "$DIR/auth" "$BK/auth"', apply_script)
        self.assertIn('cp -a "$DIR/cpa_policy.py" "$BK/cpa_policy.py"', apply_script)
        self.assertIn('python3 "$DIR/cpa_policy.py" "$DIR/config.yaml"', apply_script)
        self.assertIn("stream-bootstrap-timeout", apply_script)
        self.assertIn('python3 "$DIR/cpa-health.py" readiness', apply_script)
        self.assertIn("ROLLBACK_VERIFIED", apply_script)
        self.assertIn("ROLLBACK_FAILED", apply_script)
        self.assertIn("limit_req=$limit_req_status", apply_script)
        self.assertIn("limit_conn=$limit_conn_status", apply_script)

    def test_cpa_apply_exit_and_signal_failures_invoke_rollback_once(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        apply_script = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        handler = apply_script.split("rollback_on_exit() {\n", 1)[1].split(
            "\n}\n\nif ! grep", 1
        )[0]
        handler = "rollback_on_exit() {\n" + handler + "\n}\n"

        for trigger, expected_code in (("false", 1), ("kill -TERM $$", 143)):
            harness = (
                "set -Eeuo pipefail\n"
                "ROLLBACK_CALLS=0\n"
                "restore_all() { ROLLBACK_CALLS=$((ROLLBACK_CALLS + 1)); "
                "echo ROLLBACK_CALLS=$ROLLBACK_CALLS; }\n"
                + handler
                + "trap rollback_on_exit EXIT\n"
                + "trap 'exit 130' INT\n"
                + "trap 'exit 143' TERM\n"
                + trigger
                + "\n"
            )
            with self.subTest(trigger=trigger):
                completed = subprocess.run(
                    [bash],
                    input=harness.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                stdout = completed.stdout.decode("utf-8", errors="replace")
                stderr = completed.stderr.decode("utf-8", errors="replace")
                self.assertEqual(completed.returncode, expected_code, stderr)
                self.assertEqual(stdout.count("ROLLBACK_CALLS=1"), 1)
                self.assertIn("ROLLBACK transaction_failed", stdout)

    def test_cpa_fail2ban_policy_has_versioned_source_and_is_projected(self) -> None:
        repo_root = Path(__file__).resolve().parent
        guardrails = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )

        # Projection integrity only: the versioned filter/jail sources must be
        # carried to the host via their placeholders, or the remote jail keeps
        # running stale policy while the repo looks correct.
        self.assertIn("__CPA_FAIL2BAN_FILTER_B64__", guardrails)
        self.assertIn("__CPA_FAIL2BAN_JAIL_B64__", guardrails)

    def _assert_powershell_script_parses(
        self, powershell: str, script_path: Path
    ) -> None:
        command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path -LiteralPath $env:VPS_SSH_LAUNCHER_SCRIPT_UNDER_TEST),
  [ref]$tokens,
  [ref]$errors
) | Out-Null

if ($errors.Count -gt 0) {
  $errors | ForEach-Object { Write-Error $_.Message }
  exit 1
}
"""
        args = [powershell, "-NoProfile"]
        if Path(powershell).name.lower() == "powershell.exe":
            args += ["-ExecutionPolicy", "Bypass"]
        args += ["-Command", command]
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_SCRIPT_UNDER_TEST"] = str(script_path)

        completed = subprocess.run(
            args,
            cwd=script_path.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_google_ipv4_routing_script_is_opt_in_for_apply(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "google_ipv4_routing.ps1"
        ).read_text(encoding="utf-8")

        # Default mode is read-only diagnosis; every remote write must go
        # through the -Apply gate and the shared apply-safety guard.
        self.assertIn("[switch]$Apply", text)
        self.assertIn("Assert-SafeRemoteApplyScript", text)

        check_command = text.split("$checkCommand = @'", 1)[1].split("'@", 1)[0]
        # Regression guard: single-quoted here-strings pass backticks to bash
        # verbatim, which once broke the read-only check silently.
        self.assertNotIn(
            "`",
            check_command,
            "single-quoted here-strings pass backticks to bash verbatim; "
            "escape $ only inside double-quoted here-strings",
        )

    def test_vasma_kernel_cron_uses_vasma_menu_not_direct_downloads(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")

        # Kernel upgrades go through the vasma menu; direct GitHub downloads
        # are forbidden so version pinning and menu handling stay in one place.
        self.assertNotIn("releases?per_page", text)
        self.assertNotIn("releases/download", text)

        # Every apply path must be wrapped in the rollback trap with verified
        # backup/restore state before and after the remote write.
        self.assertIn("backup_apply_state", text)
        self.assertIn("restore_apply_state", text)
        self.assertIn("trap rollback_apply ERR INT TERM", text)
        self.assertIn("ROLLBACK_VERIFIED", text)

    def test_rendered_vasma_wrappers_are_valid_bash(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        for function_name in ("write_xray_wrapper", "write_singbox_wrapper"):
            with self.subTest(wrapper=function_name):
                wrapper = self._render_embedded_wrapper(source, function_name)
                completed = subprocess.run(
                    [bash, "-n"],
                    input=wrapper.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8",
                    errors="replace",
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    output,
                )

    def test_vasma_query_failure_verifies_current_installation_and_skips(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        cases = (
            (
                "write_xray_wrapper",
                "vasma_visible_stable_xray_version",
                "verify_current_xray",
            ),
            (
                "write_singbox_wrapper",
                "vasma_visible_stable_singbox_version",
                "verify_current_singbox",
            ),
        )
        for wrapper_name, query_function, verify_function in cases:
            with self.subTest(wrapper=wrapper_name):
                wrapper = self._render_embedded_wrapper(source, wrapper_name)
                branch_start = wrapper.index(
                    f'if ! latest_version="$({query_function})"; then'
                )
                branch_end = wrapper.index(
                    'if [ "$current_version" = "$latest_version" ]; then',
                    branch_start,
                )
                branch = wrapper[branch_start:branch_end]
                probe = f"""
set -Eeuo pipefail
log() {{ printf '%s\\n' "$*"; }}
{query_function}() {{ return 22; }}
{verify_function}() {{ echo VERIFIED_CURRENT; }}
{branch}
echo UNREACHABLE
"""
                completed = subprocess.run(
                    [bash, "-s"],
                    input=probe.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8",
                    errors="replace",
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    output,
                )
                self.assertIn("VERIFIED_CURRENT", output)
                self.assertIn("unable to query latest stable", output)
                self.assertNotIn("UNREACHABLE", output)

    def test_connect_ps1_template_uses_password_env(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"password_env": "VPS_EXAMPLE_PASSWORD"', text)
        self.assertNotIn('"password": "YOUR_PASSWORD"', text)

    def test_shared_launcher_normalizes_remote_command_line_endings(self) -> None:
        helper = (
            Path(__file__).resolve().parent
            / "scripts"
            / "lib"
            / "project_environment.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("$normalizedLauncherArgs", helper)
        self.assertIn('$_ -replace "`r`n", "`n"', helper)
        self.assertIn("| Out-Host", helper)
        self.assertIn("$exitCode = $LASTEXITCODE", helper)

    def test_connect_cmd_requires_powershell_7(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.cmd").read_text(
            encoding="utf-8"
        )

        self.assertIn("VPS_SSH_LAUNCHER_POWERSHELL", text)
        self.assertIn("pwsh.exe", text)
        self.assertNotIn('set "POWERSHELL_EXE=powershell.exe"', text)
        self.assertIn("PowerShell 7", text)

    def test_connect_ps1_requires_explicit_allow_global_bootstrap(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[switch]$AllowGlobalBootstrap", text)
        self.assertIn("AllowGlobalBootstrap", text)
        self.assertIn(
            "Refusing to install or upgrade dependencies in non-isolated Python",
            text,
        )
        self.assertIn('importlib.metadata.version("paramiko")', text)
        self.assertIn("major == 5", text)

    def test_run_gates_covers_package_without_duplicate_tools(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")

        self.assertIn("project_environment.ps1", text)
        self.assertIn('"vps_ssh_launcher"', text)
        self.assertIn('"pytest"', text)
        self.assertIn("[switch]$RunDependencyAudit", text)
        self.assertNotIn('"unittest"', text)
        self.assertNotIn('"pyright"', text)
        self.assertNotIn('"vulture"', text)

    def test_powershell_entrypoints_reuse_shared_environment_helper(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            repo_root / "scripts" / "run_gates.ps1",
            repo_root / "scripts" / "google_ipv4_routing.ps1",
            repo_root / "scripts" / "vasma_kernel_update_cron.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn("project_environment.ps1", text)
                self.assertNotIn("function Resolve-ProjectPython", text)

    def test_explicit_python_environment_is_probed_for_isolation(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        repo_root = Path(__file__).resolve().parent
        helper = repo_root / "scripts" / "lib" / "project_environment.ps1"
        command = r"""
. $env:VPS_SSH_LAUNCHER_HELPER_UNDER_TEST
$resolved = Resolve-ProjectPython -ProjectRoot $env:VPS_SSH_LAUNCHER_ROOT
$resolved.IsIsolated.ToString().ToLowerInvariant()
"""
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_HELPER_UNDER_TEST"] = str(helper)
        env["VPS_SSH_LAUNCHER_ROOT"] = str(repo_root)
        env["VPS_SSH_LAUNCHER_PYTHON"] = sys.executable
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        expected = str(sys.prefix != sys.base_prefix).lower()
        self.assertEqual(completed.stdout.strip().splitlines()[-1], expected)

    def test_explicit_config_path_is_anchored_before_launcher_changes_directory(
        self,
    ) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")

        repo_root = Path(__file__).resolve().parent
        helper = repo_root / "scripts" / "lib" / "project_environment.ps1"
        command = r"""
. $env:VPS_SSH_LAUNCHER_HELPER_UNDER_TEST
$config = Resolve-LauncherConfigPath `
  -ProjectRoot $env:VPS_SSH_LAUNCHER_ROOT `
  -Config '.\fixture-target.json'
$key = Resolve-LauncherExplicitPath -Path '.\fixture-key'
Push-Location $env:VPS_SSH_LAUNCHER_ROOT
try {
  [System.IO.Path]::GetFullPath($config)
  [System.IO.Path]::GetFullPath($key)
} finally {
  Pop-Location
}
"""
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "fixture-target.json"
            config_path.write_text("{}", encoding="utf-8")
            env = os.environ.copy()
            env["VPS_SSH_LAUNCHER_HELPER_UNDER_TEST"] = str(helper)
            env["VPS_SSH_LAUNCHER_ROOT"] = str(repo_root)
            completed = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        output_paths = [Path(line) for line in completed.stdout.strip().splitlines()]
        self.assertEqual(
            output_paths, [config_path, config_path.with_name("fixture-key")]
        )

    def test_integration_workflow_is_fixed_strict_and_environment_protected(
        self,
    ) -> None:
        workflow = (
            Path(__file__).resolve().parent
            / ".github"
            / "workflows"
            / "integration-real-ssh.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("integration_command:", workflow)
        self.assertNotIn("integration_expected:", workflow)
        self.assertNotIn("-IntegrationCommand", workflow)
        self.assertNotIn("-IntegrationExpected", workflow)
        self.assertIn("environment: vps-production", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("group: vps-real-ssh-integration", workflow)
        self.assertIn("VPS_SSH_LAUNCHER_INTEGRATION_KNOWN_HOSTS", workflow)
        self.assertIn(
            'VPS_SSH_LAUNCHER_INTEGRATION_STRICT_HOST_KEY_CHECKING: "1"',
            workflow,
        )
        self.assertNotIn('"${{ inputs.integration_profile }}"', workflow)

    def test_repository_markdown_uses_lf_without_embedded_carriage_returns(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        markdown_files = [
            *repo_root.glob("*.md"),
            *(repo_root / "docs").rglob("*.md"),
        ]
        for path in markdown_files:
            with self.subTest(path=str(path.relative_to(repo_root))):
                self.assertNotIn(b"\r", path.read_bytes())

    def test_shared_environment_helper_is_the_only_inline_environment_definition(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        all_scripts = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]

        for script_path in all_scripts:
            text = script_path.read_text(encoding="utf-8")
            is_helper = script_path.name == "project_environment.ps1"
            with self.subTest(script=str(script_path.relative_to(repo_root))):
                if is_helper:
                    self.assertIn("function Initialize-WindowsProcessEnvironment", text)
                    self.assertIn("function Resolve-ProjectPython", text)
                else:
                    self.assertNotIn(
                        "function Initialize-WindowsProcessEnvironment",
                        text,
                    )
                    self.assertNotIn("function Resolve-ProjectPython", text)


if __name__ == "__main__":
    unittest.main()
