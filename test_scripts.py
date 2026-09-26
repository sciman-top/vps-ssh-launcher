import ast
import base64
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

CPA_TEST_PROVIDER_ALIASES = {
    "gpt-6-astra-cii": "gpt-6-astra",
    "gpt-6-sol-cii": "gpt-5.6-sol",
    "gpt-6-sol-91": "gpt-5.6-sol",
    "gpt-5.6-terra": "gpt-5.6-terra",
}


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
                    "glm-5.3",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-6-astra-cii",
                    "gpt-6-sol-cii",
                    "gpt-6-sol-91",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                    "deepseek-v4-pro",
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
                    "glm-5.3",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-6-astra-cii",
                    "gpt-6-sol-cii",
                    "gpt-6-sol-91",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                    "deepseek-v4-pro",
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
        import urllib.error
        from email.message import Message

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
                    "glm-5.3",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-6-astra-cii",
                    "gpt-6-sol-cii",
                    "gpt-6-sol-91",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                    "deepseek-v4-pro",
                )
            ]
        }
        malformed = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "generation-all", malformed, mock.Mock()), 10)
        self.assertEqual(malformed.call_count, 2)

        relay = mock.Mock(side_effect=[catalog, protocol_error("bad body")])
        self.assertEqual(check({}, "relay-soft", relay, mock.Mock()), 11)
        self.assertEqual(relay.call_count, 2)

        upstream_512 = mock.Mock(
            side_effect=[
                catalog,
                urllib.error.HTTPError("fixture", 512, "upstream", Message(), None),
            ]
        )
        self.assertEqual(check({}, "generation", upstream_512, mock.Mock()), 10)

    def test_cpa_policy_rejects_nested_retry_and_quota_fallback_overrides(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa_policy.py")
        )
        route_manifest = policy["ROUTE_MANIFEST"]
        self.assertEqual(policy["_route_manifest_issues"](route_manifest), [])
        self.assertEqual(
            policy["EXPECTED_OAUTH_ROUTE_ALIASES"], {"gpt-6-luna", "gpt-5.6-luna"}
        )
        duplicate_route_manifest = json.loads(json.dumps(route_manifest))
        duplicate_route_manifest["providers"][1]["models"][0]["alias"] = "gpt-6-sol"
        self.assertTrue(
            any(
                "assigned more than once" in issue
                for issue in policy["_route_manifest_issues"](duplicate_route_manifest)
            )
        )
        duplicate_route_manifest = json.loads(json.dumps(route_manifest))
        duplicate_route_manifest["oauth_routes"][0]["models"][0]["alias"] = (
            "gpt-6-astra"
        )
        self.assertTrue(
            any(
                "assigned to multiple routes" in issue
                for issue in policy["_route_manifest_issues"](duplicate_route_manifest)
            )
        )
        compatibility = [
            {
                "name": provider["name"],
                "base-url": (
                    f"{provider.get('scheme', 'https')}://{provider['host']}"
                    + (
                        f":{provider['port']}"
                        if provider.get("port") is not None
                        else ""
                    )
                    + provider["path"]
                ),
                "api-key-entries": [{"api-key": f"{provider['name']}_TEST_KEY"}],
                "models": json.loads(json.dumps(provider["models"])),
            }
            for provider in route_manifest["providers"]
        ]
        ai_input_index = next(
            index
            for index, provider in enumerate(route_manifest["providers"])
            if provider["host"] == "ai.input.im"
        )
        http_relay_index = next(
            index
            for index, provider in enumerate(route_manifest["providers"])
            if provider["host"] == "35.213.82.91"
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
                "error-logs-max-files": 5,
                "logs-max-total-size-mb": 32,
                "usage-statistics-enabled": True,
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
                "oauth-excluded-models": {
                    "codex": ["codex-*", "gpt-5.7*"]
                    + list(route_manifest["oauth_exclusions"])
                },
                "openai-compatibility": compatibility,
            },
        )
        self.assertEqual(policy["validate_config"](config), [])
        # usage-statistics-enabled gates the doctor's cache/lane telemetry; a
        # silent disable must be a policy violation, not a quiet downgrade.
        config["usage-statistics-enabled"] = False
        self.assertTrue(
            any(
                "usage-statistics-enabled" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["usage-statistics-enabled"] = True
        self.assertEqual(policy["validate_config"](config), [])
        config["openai-compatibility"][http_relay_index]["base-url"] = (
            "https://35.213.82.91:8003/v1"
        )
        issues = policy["validate_config"](config)
        self.assertTrue(any("35.213.82.91.base-url" in issue for issue in issues))
        config["openai-compatibility"][http_relay_index]["base-url"] = (
            "http://35.213.82.91:8003/v1"
        )
        self.assertEqual(policy["validate_config"](config), [])
        config["codex-api-key"] = [
            {
                "api-key": "FIXTURE_CODEX_KEY",
                "excluded-models": list(route_manifest["codex_api_key_exclusions"]),
            }
        ]
        self.assertEqual(policy["validate_config"](config), [])
        config["codex-api-key"][0]["excluded-models"].remove("gpt-6-luna")
        issues = policy["validate_config"](config)
        self.assertTrue(any("bare-route overlap" in issue for issue in issues))
        config.pop("codex-api-key")
        config["oauth-excluded-models"]["codex"] = [
            "codex-*",
            "gpt-5.7*",
            "gpt-6*",
        ]
        issues = policy["validate_config"](config)
        self.assertTrue(any("configured OAuth routes" in issue for issue in issues))
        config["oauth-excluded-models"]["codex"] = [
            "codex-*",
            "gpt-5.7*",
            "gpt-6-sol",
            "gpt-6-astra",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
        ]
        config["openai-compatibility"][ai_input_index]["models"].remove(
            {"name": "gpt-6-sol", "alias": "gpt-6-sol"}
        )
        issues = policy["validate_config"](config)
        self.assertTrue(any("models=" in issue for issue in issues))
        config["openai-compatibility"][ai_input_index]["models"].append(
            {"name": "gpt-6-sol", "alias": "gpt-6-sol"}
        )
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
        config["openai-compatibility"][ai_input_index]["disabled"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("ai.input.im.disabled" in issue for issue in issues))
        config["openai-compatibility"][ai_input_index].pop("disabled")
        config["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://ai.input.im"
        )
        issues = policy["validate_config"](config)
        self.assertTrue(any("ai.input.im.base-url" in issue for issue in issues))
        config["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://ai.input.im/v1"
        )
        config["openai-compatibility"][ai_input_index]["base-url"] = (
            "http://35.213.82.91:8003/v1"
        )
        issues = policy["validate_config"](config)
        self.assertTrue(
            any("exactly one ai.input.im provider" in issue for issue in issues)
        )

        config["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://ai.input.im/v1"
        )
        for invalid_url in (
            "http://ai.input.im/v1",
            "https://user:pass@ai.input.im/v1",
            "https://ai.input.im:8443/v1",
            "https://ai.input.im/v1?x=1",
            "https://ai.input.im/v2",
        ):
            config["openai-compatibility"][ai_input_index]["base-url"] = invalid_url
            with self.subTest(invalid_url=invalid_url):
                self.assertTrue(
                    any(
                        "base-url must be exact" in issue
                        for issue in policy["validate_config"](config)
                    )
                )
        config["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://ai.input.im/v1"
        )
        config["openai-compatibility"][ai_input_index]["api-key-entries"] = [
            {"api-key": "AI_TEST_KEY"},
            {"api-key": "AI_TEST_KEY_2"},
        ]
        self.assertTrue(
            any(
                "exactly one entry" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["openai-compatibility"][ai_input_index]["api-key-entries"] = [
            {"api-key": "AI_TEST_KEY"}
        ]
        config["openai-compatibility"][ai_input_index]["headers"] = {
            "X-Test": "blocked"
        }
        self.assertTrue(
            any(
                "headers transport override" in issue
                for issue in policy["validate_config"](config)
            )
        )

        config["openai-compatibility"][ai_input_index].pop("headers")
        config["openai-compatibility"][ai_input_index]["models"][0]["name"] = (
            "different-upstream-model"
        )
        self.assertTrue(
            any("models=" in issue for issue in policy["validate_config"](config))
        )
        config["openai-compatibility"][ai_input_index]["models"][0]["name"] = (
            "gpt-6-sol"
        )
        config["openai-compatibility"].append(
            {
                "name": "unexpected",
                "base-url": "https://example.invalid/v1",
                "api-key-entries": [{"api-key": "EXTRA_TEST_KEY"}],
                "models": [{"name": "other", "alias": "other"}],
            }
        )
        self.assertTrue(
            any(
                "unexpected openai-compatibility" in issue
                for issue in policy["validate_config"](config)
            )
        )

    def _valid_cpa_policy_config(self, policy: dict[str, Any]) -> dict[str, Any]:
        route_manifest = policy["ROUTE_MANIFEST"]
        compatibility = [
            {
                "name": provider["name"],
                "base-url": (
                    f"{provider.get('scheme', 'https')}://{provider['host']}"
                    + (
                        f":{provider['port']}"
                        if provider.get("port") is not None
                        else ""
                    )
                    + provider["path"]
                ),
                "api-key-entries": [{"api-key": f"{provider['name']}_TEST_KEY"}],
                "models": json.loads(json.dumps(provider["models"])),
            }
            for provider in route_manifest["providers"]
        ]
        return cast(
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
                "error-logs-max-files": 5,
                "logs-max-total-size-mb": 32,
                "usage-statistics-enabled": True,
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
                "oauth-excluded-models": {
                    "codex": ["codex-*", "gpt-5.7*"]
                    + list(route_manifest["oauth_exclusions"])
                },
                "openai-compatibility": compatibility,
            },
        )

    def test_cpa_policy_oauth_quarantine_requires_a_matching_marker(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa_policy.py")
        )
        oauth_aliases = sorted(policy["EXPECTED_OAUTH_ROUTE_ALIASES"])
        self.assertEqual(oauth_aliases, ["gpt-5.6-luna", "gpt-6-luna"])

        def write_marker(payload: object) -> None:
            marker_path.write_text(json.dumps(payload), encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            marker_path = Path(directory) / "oauth-quarantine.json"

            def validate(candidate: dict[str, Any]) -> list[str]:
                return cast(
                    list[str],
                    policy["validate_config"](candidate, marker_path=marker_path),
                )

            config = self._valid_cpa_policy_config(policy)
            self.assertEqual(validate(config), [])

            # Blocking a live OAuth route without the operator marker is an
            # unreviewed lane change and must fail closed.
            config["oauth-excluded-models"]["codex"] = [
                *config["oauth-excluded-models"]["codex"],
                *oauth_aliases,
            ]
            issues = validate(config)
            self.assertTrue(
                any("configured OAuth routes" in issue for issue in issues), issues
            )

            # The marker authorizes exactly those aliases.
            write_marker(
                {
                    "version": 1,
                    "state": "quarantined",
                    "aliases": oauth_aliases,
                    "previous_codex_exclusions": [
                        pattern
                        for pattern in config["oauth-excluded-models"]["codex"]
                        if pattern not in oauth_aliases
                    ],
                    "applied_codex_exclusions": list(
                        config["oauth-excluded-models"]["codex"]
                    ),
                    "since": "2026-09-26T00:00:00Z",
                    "reason": "operator_requested_risk_control",
                }
            )
            self.assertEqual(validate(config), [])

            # A whole-account quarantine marker must name every configured
            # OAuth alias; a partial marker cannot authorize a broader block.
            write_marker(
                {"version": 1, "state": "quarantined", "aliases": ["gpt-6-luna"]}
            )
            issues = validate(config)
            self.assertTrue(
                any(
                    "must name every configured OAuth alias" in issue
                    for issue in issues
                ),
                issues,
            )

            # A marker naming an alias the config still serves means the
            # quarantine never took effect.
            write_marker(
                {
                    "version": 1,
                    "state": "quarantined",
                    "aliases": oauth_aliases,
                    "previous_codex_exclusions": [
                        pattern
                        for pattern in config["oauth-excluded-models"]["codex"]
                        if pattern not in oauth_aliases
                    ],
                    "applied_codex_exclusions": list(
                        config["oauth-excluded-models"]["codex"]
                    ),
                }
            )
            partial = json.loads(json.dumps(config))
            partial["oauth-excluded-models"]["codex"] = [
                pattern
                for pattern in partial["oauth-excluded-models"]["codex"]
                if pattern != "gpt-5.6-luna"
            ]
            issues = validate(partial)
            self.assertTrue(any("still served" in issue for issue in issues), issues)

            # Malformed or inconsistent markers fail closed instead of
            # silently re-exposing the subscription lane.
            marker_path.write_text("{not json", encoding="utf-8")
            issues = validate(config)
            self.assertTrue(
                any("unreadable OAuth quarantine marker" in issue for issue in issues),
                issues,
            )
            write_marker({"version": 1, "state": "released", "aliases": oauth_aliases})
            issues = validate(config)
            self.assertTrue(
                any("state must be 'quarantined'" in issue for issue in issues), issues
            )
            write_marker(
                {"version": 1, "state": "quarantined", "aliases": ["gpt-6-sol"]}
            )
            issues = validate(config)
            self.assertTrue(
                any("non-OAuth aliases" in issue for issue in issues), issues
            )
            write_marker({"version": 1, "state": "quarantined", "aliases": []})
            issues = validate(config)
            self.assertTrue(
                any("at least one alias" in issue for issue in issues), issues
            )

            # Releasing the marker restores the normal fail-closed contract.
            marker_path.unlink()
            issues = validate(config)
            self.assertTrue(
                any("configured OAuth routes" in issue for issue in issues), issues
            )

    def test_cpa_health_prepared_gpt6_models_are_optional_until_cataloged(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        script = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        required_models = [
            "glm-5.3-flash",
            "glm-5.3",
            "gpt-6-astra",
            "gpt-5.6-sol",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "deepseek-flash",
            "deepseek-v4-pro",
        ]
        missing_optional_catalog = {
            "data": [{"id": model} for model in required_models]
        }
        request = mock.Mock(return_value=missing_optional_catalog)
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1)

        generation = {
            "model": "glm-5.3-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        request = mock.Mock(side_effect=[missing_optional_catalog, generation])
        self.assertEqual(check({}, "generation", request, mock.Mock()), 0)
        self.assertEqual(request.call_args_list[1].args[1]["model"], "glm-5.3-flash")

        matrix_targets = [
            "gpt-6-luna",
            "gpt-6-sol",
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
        ]
        full_catalog = {
            "data": [
                {"id": model} for model in required_models + ["gpt-6-luna", "gpt-6-sol"]
            ]
        }
        responses: list[object] = [full_catalog]
        responses.extend(
            {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in matrix_targets
        )
        request = mock.Mock(side_effect=responses)
        # The matrix targets include the subscription lane aliases, so this
        # deliberately reviewed run opts the OAuth lane back in.
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1 + len(matrix_targets))
        self.assertEqual(
            {call.args[1]["model"] for call in request.call_args_list[1:]},
            set(matrix_targets),
        )

        closed_responses: list[object] = [full_catalog]
        closed_responses.extend(
            urllib.error.HTTPError("", 404, "", Message(), None)
            if model == "gpt-6-sol"
            else {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in matrix_targets
        )
        closed_request = mock.Mock(side_effect=closed_responses)
        closed_lines: list[str] = []
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(
                check(
                    {},
                    "generation-all",
                    closed_request,
                    mock.Mock(),
                    closed_lines.append,
                ),
                10,
            )
        self.assertEqual(closed_request.call_count, 1 + len(matrix_targets))
        self.assertTrue(
            any(
                line.startswith("GENERATION model=gpt-6-sol status=404 ")
                and "error_class=optional_route_unavailable" in line
                for line in closed_lines
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
                    "glm-5.3",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-6-astra-cii",
                    "gpt-6-sol-cii",
                    "gpt-6-sol-91",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                    "deepseek-v4-pro",
                ]
            ]
        }
        smoke = {
            "model": "glm-5.3-flash",
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
                self.assertEqual(body["model"], "glm-5.3-flash")

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
                    "glm-5.3",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-6-astra-cii",
                    "gpt-6-sol-cii",
                    "gpt-6-sol-91",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                    "deepseek-v4-pro",
                ]
            ]
        }
        ok_astra = {
            "model": "gpt-6-astra",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        ok_sol56 = {
            "model": "gpt-5.6-sol",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        request = mock.Mock(side_effect=[catalog, ok_astra, ok_sol56])
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
                        "model": "gpt-6-sol",
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
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
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
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 1 + len(models))
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

    def test_cpa_health_no_oauth_suppresses_oauth_routes(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        # The same ordered provider matrix as the all-routes test, plus the two
        # Luna aliases present in the catalog: the guard must drop them even
        # though they are listed and would otherwise be probed.
        provider_models = [
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
        ]
        oauth_models = ["gpt-6-luna", "gpt-5.6-luna"]
        catalog = {
            "data": [{"id": model} for model in [*oauth_models, *provider_models]]
        }

        def build_request(models: list[str]) -> mock.Mock:
            responses: list[object] = [catalog]
            responses.extend(
                {
                    "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                    "choices": [
                        {"message": {"content": "OK"}, "finish_reason": "stop"}
                    ],
                }
                for model in models
            )
            return mock.Mock(side_effect=responses)

        suppressed_request = build_request(provider_models)
        lines: list[str] = []
        with mock.patch.dict(os.environ, {"CPA_HEALTH_NO_OAUTH": "1"}):
            self.assertEqual(
                check(
                    {}, "generation-all", suppressed_request, mock.Mock(), lines.append
                ),
                0,
            )
        probed = {
            call.args[1]["model"] for call in suppressed_request.call_args_list[1:]
        }
        self.assertEqual(probed, set(provider_models))
        for alias in oauth_models:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    for line in lines
                )
            )

        # Control: the hard suppression wins even when the lane is opted in, so
        # the absence of OAuth traffic is attributable to the flag alone.
        control_request = build_request(provider_models)
        control_lines: list[str] = []
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "1", "CPA_HEALTH_INCLUDE_OAUTH": "1"}
        ):
            self.assertEqual(
                check(
                    {},
                    "generation-all",
                    control_request,
                    mock.Mock(),
                    control_lines.append,
                ),
                0,
            )
        control_probed = {
            call.args[1]["model"] for call in control_request.call_args_list[1:]
        }
        self.assertEqual(control_probed, set(provider_models))
        for alias in oauth_models:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "reason=CPA_HEALTH_NO_OAUTH" in line
                    for line in control_lines
                )
            )

    def test_cpa_health_oauth_matrix_default_is_opt_out(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        provider_models = [
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
        ]
        oauth_models = ["gpt-6-luna", "gpt-5.6-luna"]
        catalog = {
            "data": [{"id": model} for model in [*oauth_models, *provider_models]]
        }

        def build_request(models: list[str]) -> mock.Mock:
            responses: list[object] = [catalog]
            responses.extend(
                {
                    "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
                    "choices": [
                        {"message": {"content": "OK"}, "finish_reason": "stop"}
                    ],
                }
                for model in models
            )
            return mock.Mock(side_effect=responses)

        # Default matrix admission excludes the subscription lane even though
        # both aliases are listed, so a quality run cannot touch it by accident.
        default_request = build_request(provider_models)
        lines: list[str] = []
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "0", "CPA_HEALTH_INCLUDE_OAUTH": "0"}
        ):
            self.assertEqual(
                check({}, "generation-all", default_request, mock.Mock(), lines.append),
                0,
            )
        self.assertEqual(
            {call.args[1]["model"] for call in default_request.call_args_list[1:]},
            set(provider_models),
        )
        for alias in oauth_models:
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    and "reason=default_opt_out" in line
                    for line in lines
                )
            )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET ")
                and f"models={len(provider_models)}" in line
                and "cases_per_model=1" in line
                and f"planned_generation_requests={len(provider_models)}" in line
                and "oauth_lane=excluded:default_opt_out" in line
                for line in lines
            )
        )

        # Explicit opt-in re-admits the lane for a deliberately reviewed run.
        opted_in_request = build_request([*oauth_models, *provider_models])
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "0", "CPA_HEALTH_INCLUDE_OAUTH": "1"}
        ):
            self.assertEqual(
                check({}, "generation-all", opted_in_request, mock.Mock()), 0
            )
        self.assertEqual(
            {call.args[1]["model"] for call in opted_in_request.call_args_list[1:]},
            {*oauth_models, *provider_models},
        )

        # quality-eval multiplies the budget per model instead of hiding it.
        quality_lines: list[str] = []
        with mock.patch.dict(
            os.environ, {"CPA_HEALTH_NO_OAUTH": "0", "CPA_HEALTH_INCLUDE_OAUTH": "0"}
        ):
            self.assertEqual(
                check(
                    {},
                    "quality-eval",
                    mock.Mock(side_effect=[catalog]),
                    mock.Mock(),
                    quality_lines.append,
                ),
                20,
            )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET mode=quality-eval ")
                and "cases_per_model=4" in line
                for line in quality_lines
            )
        )

    def test_cpa_health_generation_all_reports_every_route_after_failure(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        models = [
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
        ]
        catalog = {"data": [{"id": model} for model in models]}
        responses: list[object] = [catalog]
        responses.extend(
            urllib.error.HTTPError("", 502, "", Message(), None)
            if model == "gpt-6-sol-91"
            else {
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
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
        generation_lines = [line for line in lines if line.startswith("GENERATION ")]
        self.assertEqual(len(generation_lines), len(models))
        # Every generation route is reported, plus the two subscription-lane
        # routes the default matrix suppresses, the unlisted route notice, and
        # the probe-budget line.
        self.assertEqual(len(lines), len(models) + 4)
        self.assertEqual(
            sorted(
                line.split(" ", 1)[0]
                for line in lines
                if not line.startswith("GENERATION ")
            ),
            ["PROBE_BUDGET", "ROUTE_PREPARED", "ROUTE_PREPARED", "ROUTE_PREPARED"],
        )
        self.assertTrue(
            any(
                line.startswith("PROBE_BUDGET mode=generation-all ")
                and "oauth_lane=excluded:default_opt_out" in line
                for line in lines
            )
        )
        for alias in ("gpt-6-luna", "gpt-5.6-luna"):
            self.assertTrue(
                any(
                    line.startswith(f"ROUTE_PREPARED model={alias} ")
                    and "kind=oauth_lane_suppressed" in line
                    for line in lines
                )
            )
        self.assertTrue(
            any(
                line.startswith("ROUTE_PREPARED model=gpt-6-sol ")
                and "status=not_listed" in line
                for line in lines
            )
        )
        for model in ("gpt-6-astra-cii", "gpt-6-sol-cii"):
            self.assertTrue(
                any(
                    line.startswith(f"GENERATION model={model} status=200 ")
                    for line in lines
                )
            )
        self.assertTrue(
            any(
                line.startswith("GENERATION model=gpt-6-sol-91 status=502 latency_ms=")
                and line.endswith("error_class=transient_upstream")
                for line in lines
            )
        )
        for model in models:
            self.assertTrue(any(f"model={model} " in line for line in generation_lines))
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
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
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
                "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
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
        self.assertEqual(request.call_count, 1 + len(models))
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
                    "glm-5.3",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-6-astra-cii",
                    "gpt-6-sol-cii",
                    "gpt-6-sol-91",
                    "gpt-5.6-terra",
                    "deepseek-flash",
                    "deepseek-v4-pro",
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
            "gpt-6-astra",
            "gpt-5.6-sol",
            "glm-5.3-flash",
            "deepseek-flash",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "glm-5.3",
            "deepseek-v4-pro",
        )
        catalog = {"data": [{"id": model} for model in models]}
        responses: list[object] = [catalog]
        for model in models:
            for case in cases:
                if "expected" in case:
                    responses.append(
                        {
                            "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
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
                            "model": CPA_TEST_PROVIDER_ALIASES.get(model, model),
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
        # timer path too, restricted to auth/logs/error-*.log older than 48
        # hours (2026-09-21 review: plaintext bodies stay at rest too long).
        self.assertIn("prune_error_dumps", source[:unverified])
        self.assertIn("\nprune_error_dumps\n", source[ok_log:])
        self.assertIn('find "$DIR/auth/logs"', source)
        self.assertIn("-mmin +1440", source)
        self.assertIn("secure_error_dumps", source)
        self.assertIn('chmod 700 -- "$DIR/auth/logs"', source)
        self.assertIn('chmod 600 -- "$entry"', source)
        # The third-party relay channel must never be auto-probed by the daily
        # timer; relay-soft stays a manual, explicit mode of cpa-health.py.
        self.assertNotIn("relay-soft", source)
        self.assertNotIn("RELAY_SOFT", source)

    def test_cpa_updater_no_update_path_is_non_consuming(self) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index('if [[ "$CUR" == "$TARGET" ]]')
        end = source.index("\nif ! health generation", start)
        branch = source[start:end]
        for readiness_code, expected_code, marker in (
            (0, 0, "OK: no newer mature release"),
            (1, 1, "DEFER: no-update readiness failed"),
        ):
            with self.subTest(readiness=readiness_code), tempfile.TemporaryDirectory():
                harness = "\n".join(
                    [
                        "set -u",
                        "CUR=v7.3.7",
                        "TARGET=v7.3.7",
                        'health() { printf "HEALTH_CALL %s\\n" "$1"; return '
                        f"{readiness_code}; }}",
                        'docker() { printf "credential refresh failed for codex\\n"; }',
                        'log() { printf "%s\\n" "$*"; }',
                        "prune_error_dumps() { :; }",
                        branch,
                    ]
                )
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode(),
                    capture_output=True,
                    timeout=30,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)
                # The no-candidate daily path must not spend the OAuth
                # account: local readiness only, never a generation request
                # (2026-09-21 review removed the fixed-window machine smoke).
                self.assertEqual(
                    [
                        line
                        for line in output.splitlines()
                        if line.startswith("HEALTH_CALL ")
                    ],
                    ["HEALTH_CALL readiness"],
                )
                if readiness_code == 0:
                    self.assertIn("REFRESH_SIGNALS_24H=1", output)
                    self.assertNotIn("UNVERIFIED", output)

    def test_cpa_updater_post_update_readiness_distinguishes_upstream_and_local_failure(
        self,
    ) -> None:
        import tempfile

        bash = self._resolve_bash()
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
                    self._bash_command(bash),
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
        bash = self._resolve_bash()
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
                    self._bash_command(bash),
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

        def oauth_record(
            expired: str, last_refresh: str = "2026-09-19T20:38:59+08:00"
        ) -> dict[str, Any]:
            # Placeholder token values: the monitor only reads metadata fields.
            return {
                "type": "codex",
                "access_token": "TEST_ACCESS_TOKEN",
                "refresh_token": "TEST_REFRESH_TOKEN",
                "expired": expired,
                "last_refresh": last_refresh,
            }

        def error_dump(
            body: str = "", response: str = "", age_hours: float = 0.0
        ) -> str:
            # Mirror the CLIProxyAPI dump layout: REQUEST BODY is plaintext
            # untrusted client text; only response-side sections are signal
            # evidence (2026-09-21 prompt-contamination regression).
            stamp = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=age_hours)
            ).isoformat()
            return (
                "=== REQUEST INFO ===\n"
                "Version: v7.3.7\n"
                "URL: /v1/responses\n"
                "Method: POST\n"
                f"Timestamp: {stamp}\n"
                "\n"
                "\n=== HEADERS ===\n"
                "Authorization: Bearer abcd...ef01\n"
                "\n=== REQUEST BODY ===\n"
                f"{body}\n"
                "=== RESPONSE ===\n"
                f"{response}\n"
            )

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
                "refresh_signal_in_response_section",
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {
                    "error-20260921a.log": error_dump(
                        body="routine prompt text",
                        response='{"error":{"message":"invalid_grant"}}',
                    )
                },
                1,
                "oauth_monitor=FAIL_REFRESH_SIGNAL",
            ),
            (
                "prompt_keywords_in_request_body_are_not_signals",
                # A failed request whose prompt merely discusses OAuth failure
                # keywords must never count as a credential failure.
                {"codex-ok.json": oauth_record("2030-01-01T00:00:00+00:00")},
                {
                    "error-20260921b.log": error_dump(
                        body="review text: invalid_grant refresh_token_reused "
                        "refresh token expired oauth returned 401 upstream",
                        response='{"error":{"message":"server_is_overloaded"}}',
                    )
                },
                0,
                "oauth_monitor=OK",
            ),
            (
                "signals_resolved_by_later_refresh",
                # A refresh-failure signal older than the last successful
                # refresh is reported but no longer blocking.
                {
                    "codex-ok.json": oauth_record(
                        "2030-01-01T00:00:00+00:00",
                        last_refresh=dt.datetime.now(dt.timezone.utc).isoformat(),
                    )
                },
                {
                    "error-20260921c.log": error_dump(
                        response='{"error":{"message":"invalid_grant"}}',
                        age_hours=2.0,
                    )
                },
                0,
                "oauth_refresh_signals_resolved=true",
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
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        script_path = self._bash_path(
            bash, Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        )
        completed = subprocess.run(
            [
                *self._bash_command(bash, "-n"),
                script_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cpa_prune_backups_keeps_newest_backup_dirs(self) -> None:
        import tempfile

        bash = self._resolve_bash()
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
                self._bash_command(bash),
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

        bash = self._resolve_bash()
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
            backup_root = Path(directory) / "backups"
            retained = backup_root / "20260901T000000Z-from-v0.0.1"
            retained.mkdir(parents=True)
            (retained / "compose.yml").write_text(
                "services:\n  cli-proxy-api:\n    image: rollback-image\n"
            )
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BK='{self._bash_path(bash, retained)}'",
                    f"BACKUP_ROOT='{self._bash_path(bash, backup_root)}'",
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
                self._bash_command(bash),
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
    def _resolve_bash() -> str | None:
        """Prefer Git Bash on Windows before retaining the PATH fallback."""
        if os.name == "nt":
            program_files_roots = dict.fromkeys(
                filter(
                    None,
                    (
                        os.environ.get("ProgramW6432"),
                        os.environ.get("ProgramFiles"),
                        os.environ.get("ProgramFiles(x86)"),
                    ),
                )
            )
            for root in program_files_roots:
                for relative_path in (
                    Path("Git") / "bin" / "bash.exe",
                    Path("Git") / "usr" / "bin" / "bash.exe",
                ):
                    candidate = Path(root) / relative_path
                    if candidate.is_file():
                        return str(candidate)

        return shutil.which("bash")

    @staticmethod
    def _bash_command(bash: str, *args: str) -> list[str]:
        """Give Git Bash the login environment its Unix utilities require."""
        if os.name == "nt" and "git" in {
            part.lower() for part in Path(bash).resolve().parts
        }:
            return [bash, "-l", *args]
        return [bash, *args]

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
                ScriptValidationTests._bash_command(bash, "-c", "test -d /mnt/c"),
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

    def test_operational_scripts_require_powershell_7(self) -> None:
        # PS 5.1 reads UTF-8 (no BOM) scripts as ANSI and would silently
        # corrupt the vasma menu anchors before they are deployed; the family
        # also relies on pwsh-only behavior (utf8NoBOM). The #requires line is
        # ASCII, so 5.1 refuses cleanly instead of running corrupted.
        repo_root = Path(__file__).resolve().parent
        operational = [
            "cpa_bwg_guardrails.ps1",
            "google_ipv4_routing.ps1",
            "install_vps_maintenance_task.ps1",
            "run_gates.ps1",
            "system_maintenance_cron.ps1",
            "vasma_kernel_update_cron.ps1",
            "vps_maintenance.ps1",
        ]
        for name in operational:
            with self.subTest(script=name):
                text = (repo_root / "scripts" / name).read_text(encoding="utf-8")
                self.assertTrue(
                    text.startswith("#requires -Version 7"),
                    f"{name} must refuse pre-7 PowerShell hosts",
                )

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
        # Observe mode keeps exit 0 but must not masquerade a failing run as a
        # clean contract.
        self.assertIn("DOCTOR_CONTRACT_OBSERVE_FAILED", text)
        self.assertIn("DOCTOR_CONTRACT_OBSERVE_OK", text)
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
        self.assertIn("__CPA_PROVIDER_ROUTES_B64__", text)
        self.assertIn(
            'write_base64_file "__CPA_PROVIDER_ROUTES_B64__" '
            '"$DIR/cpa_provider_routes.json" 644',
            text,
        )
        route_manifest = json.loads(
            (repo_root / "scripts" / "remote" / "cpa_provider_routes.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [provider["slot"] for provider in route_manifest["providers"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(route_manifest["retired_hosts"], [])
        retired_aliases = {
            "codex-auto-review",
            "gpt-5.5",
            "gpt-5.6",
            "gpt-reserve",
        }
        self.assertTrue(retired_aliases <= set(route_manifest["oauth_exclusions"]))
        self.assertTrue(
            retired_aliases <= set(route_manifest["codex_api_key_exclusions"])
        )
        self.assertFalse(
            retired_aliases
            & {
                model["alias"]
                for provider in route_manifest["providers"]
                for model in provider["models"]
            }
        )
        slot1_route = next(
            route for route in route_manifest["providers"] if route["slot"] == 1
        )
        self.assertEqual(slot1_route["host"], "ai.input.im")
        self.assertEqual(
            [model["alias"] for model in slot1_route["models"]],
            ["gpt-6-sol", "gpt-6-astra", "gpt-5.6-sol", "gpt-image-2.5"],
        )
        self.assertEqual(
            set(slot1_route["optional_models"]), {"gpt-6-sol", "gpt-image-2.5"}
        )
        self.assertEqual(slot1_route["image_models"], ["gpt-image-2.5"])
        ciii_route = next(
            route for route in route_manifest["providers"] if route["slot"] == 2
        )
        self.assertEqual(ciii_route["host"], "codex.ciii.club")
        self.assertEqual(
            ciii_route["models"],
            [
                {"name": "gpt-6-astra", "alias": "gpt-6-astra-cii"},
                {"name": "gpt-5.6-sol", "alias": "gpt-6-sol-cii"},
            ],
        )
        for slot, expected in {
            4: {
                "glm-5.3",
                "glm-5.3-flash",
            },
            5: {"deepseek-flash", "deepseek-v4-pro"},
        }.items():
            provider = next(
                route for route in route_manifest["providers"] if route["slot"] == slot
            )
            self.assertEqual({model["name"] for model in provider["models"]}, expected)
            self.assertEqual({model["alias"] for model in provider["models"]}, expected)
        http_route = next(
            provider
            for provider in route_manifest["providers"]
            if provider["slot"] == 3
        )
        self.assertEqual(
            (http_route["scheme"], http_route["host"], http_route["port"]),
            ("http", "35.213.82.91", 8003),
        )
        self.assertEqual(
            http_route["models"],
            [
                {"name": "gpt-5.6-sol", "alias": "gpt-6-sol-91"},
                {"name": "gpt-5.6-terra", "alias": "gpt-5.6-terra"},
            ],
        )
        self.assertIs(http_route["allow_insecure_http"], True)
        self.assertIn(
            'legacy_hosts = set(route_manifest.get("retired_hosts", []))',
            text,
        )
        self.assertIn("error-dump-permissions=OK", text)
        self.assertIn("compose-umask=OK", text)
        self.assertIn("logs-max-total-size-mb=32", text)
        self.assertIn("limit_req zone=cpa_rl burst=10;", text)
        self.assertIn(
            'legacy_limit_req = "limit_req zone=cpa_rl burst=20 nodelay;"', text
        )
        # Local throttling must answer 429, not nginx's 503 default, and the
        # limiter directive plus its statuses must be asserted by the read-only
        # doctor - not only repaired by -Apply. Without this the 2026-09-09
        # hardening silently regressed out of the versioned contract.
        self.assertIn("limit_req_status 429;", text)
        self.assertIn("limit_conn_status 429;", text)
        self.assertIn("gateway-per-ip-rate-limit", text)
        self.assertIn("gateway-throttle-status=429", text)
        self.assertIn("ensure_nginx_directive", text)
        self.assertIn("ROLLBACK gateway_throttle_contract", text)
        # The catalog segment is manifest-derived and fails closed on any
        # unregistered ID instead of only printing MODEL_IDS for a human.
        self.assertIn("MODEL_IDS_UNKNOWN=", text)
        self.assertIn('"$DIR/cpa_provider_routes.json"; then', text)
        # config.yaml carries cleartext provider keys; its permissions are gated.
        self.assertIn("config-permissions=owner-only", text)
        self.assertIn("oauth_days_left=", text)
        self.assertIn("oauth_hours_left=", text)
        self.assertIn("oauth_refresh_policy=lead24h_grace2h", text)
        self.assertIn("refresh_token_reused", text)
        self.assertIn("oauth_refresh_failures_7d=", text)
        self.assertIn("oauth_monitor=FAIL_REFRESH_SIGNAL", text)
        # OAuth refresh signals come only from response-side dump sections
        # and the container log: request-body keywords must never trip the
        # gate (2026-09-21 false positive), one file is one event, and a
        # later successful refresh resolves retained signals.
        self.assertIn("=== api error response ===", text)
        self.assertIn("oauth_refresh_signals_resolved", text)
        self.assertIn("incomplete_bounded_sample", text)
        # Error dump inventory must retain each path alongside its metadata;
        # otherwise the loop repeatedly reads the last path from discovery.
        self.assertIn("candidates.append((path, st.st_mtime, st.st_size))", text)
        self.assertIn("for path, mtime, size in candidates:", text)
        # Retained-dump observations are bounded samples (CPA keeps only the
        # newest error-logs-max-files dumps), never full-window counts.
        self.assertIn("auth_unavailable_retained_sample_count", text)
        self.assertIn("incomplete_bounded_error_dumps", text)
        # Management plane: either fully disabled or keyed-behind-loopback.
        # allow-remote=true is only acceptable with a >=32 char secret-key,
        # because docker-proxy forwards non-loopback source IPs and the panel
        # is reached through an SSH tunnel; nginx must never carry a
        # management route.
        self.assertIn("management-remote=DISABLED", text)
        self.assertIn("management-remote=LOOPBACK_KEYED", text)
        self.assertIn("-ge 32", text)
        self.assertIn("nginx-no-management-route", text)
        self.assertIn("REFUSE remote management enabled without a strong", text)
        # Gateway status telemetry: 499 client aborts carry request_time stats
        # so a fixed client-side total timeout signature is provable from
        # doctor output alone.
        self.assertIn("client_abort_request_time", text)
        self.assertIn("abort_request_times", text)
        self.assertIn("'p50_s':", text)
        # 5xx attribution: request_time buckets separate CPA cooldown
        # fast-fails (<0.5s) from upstream passthrough (>=3s) so a client 503
        # storm is attributable from doctor output alone; client IPs stay
        # masked to /16 and retry cadence is reported as aggregate gaps only.
        self.assertIn("five_xx_local_vs_upstream", text)
        self.assertIn("fast_local_lt_0_5s", text)
        self.assertIn("fast_upstream_lt_0_5s", text)
        self.assertIn("slow_upstream_ge_3s", text)
        self.assertIn("five_xx_by_client_masked", text)
        self.assertIn("client_503_retry_pattern", text)
        self.assertIn("client_503_retry_pattern_by_plane", text)
        self.assertIn("retry_after_classes", text)
        self.assertIn("last_1h_statuses", text)
        self.assertIn("route_classes", text)
        self.assertIn("safe-route-class=LEGACY_UNPROJECTED", text)
        self.assertIn("map $uri $cpa_route_class", text)
        self.assertIn("route=$cpa_route_class", text)
        self.assertIn("retry_after=$cpa_retry_after_class", text)
        self.assertIn("map $upstream_http_retry_after $cpa_retry_after_class", text)
        self.assertGreaterEqual(text.count("without_retry_log_format,"), 2)
        self.assertGreaterEqual(text.count("legacy_route_log_format,"), 2)
        # Cache usage telemetry: aggregated from the in-memory usage queue via
        # the management key file; per-model sums only, no raw records. The
        # destructive endpoint requires a separate human acknowledgement and
        # is consumed in one process, never through a shell variable.
        self.assertIn("==cache-usage==", text)
        self.assertIn("cache_usage=UNAVAILABLE_NON_CONSUMING_DOCTOR", text)
        self.assertIn("usage-queue?count=1000", text)
        self.assertIn("-ConsumeUsageQueue", text)
        self.assertIn("-AcknowledgeUsageQueueConsumption", text)
        self.assertIn("__CPA_DOCTOR_CONSUME_USAGE_QUEUE__", text)
        self.assertIn("__CPA_DOCTOR_USAGE_QUEUE_ACK__", text)
        self.assertIn(
            "Usage queue consumption is only available with the default strict doctor.",
            text,
        )
        self.assertIn("I_UNDERSTAND_RAW_USAGE_QUEUE", text)
        self.assertIn("raw records never enter a shell variable", text)
        self.assertIn("response_too_large", text)
        self.assertIn("ProxyHandler({})", text)
        self.assertIn("hit_ratio", text)
        self.assertIn("aggregate sums only", text)
        self.assertIn("bucketed per provider/model lane", text)
        # Silent model substitution telemetry (upstream >= v7.3.8): counted,
        # redaction-safe (no log line text echoed), capability-aware, and
        # observation-grade. Two severity tiers: >5 events/7d is ELEVATED
        # (warrants quality-canary review); 1-5 is OBSERVED; 0 is OK.
        self.assertIn("==model-substitution==", text)
        self.assertIn("model_substitution_warnings_7d=", text)
        self.assertIn("grep -c 'upstream served model'", text)
        self.assertIn("WARN_SUBSTITUTION_ELEVATED", text)
        self.assertIn("WARN_SUBSTITUTION_OBSERVED", text)
        self.assertIn("model_substitution=UNAVAILABLE_VERSION", text)
        self.assertIn("sort -V", text)
        # Coverage annotation must mention the elevated threshold so operators
        # understand what triggers the higher-severity label.
        self.assertIn(
            "WARN_SUBSTITUTION_ELEVATED (>5 in 7d) warrants quality-canary review", text
        )
        self.assertNotIn(
            "not a strict gate",
            text.split("WARN_SUBSTITUTION_ELEVATED")[0],
            msg="coverage annotation must appear after the ELEVATED label",
        )
        # P2-C: HTTP (cleartext) provider slots from the deployed route manifest
        # must be surfaced each doctor run so operators never rely on memory.
        # The check reads cpa_provider_routes.json and emits a reminder line
        # without mark_fail (slot-3 is a user-authorised exception).
        self.assertIn("insecure_http_providers=", text)
        self.assertIn("no mark_fail", text)
        self.assertIn("user-authorised", text)
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
        # Apply/RotatePath/DeactivateOAuthLuna/QuarantineOAuthLuna share the
        # updater flock so the daily timer cannot interleave with a guardrail
        # transaction.
        self.assertEqual(text.count("exec 9>/run/vps-ssh-launcher-maintenance.lock"), 4)
        self.assertEqual(text.count("flock -n 9"), 4)
        # OAuth lane availability is a property of the whole route, not of the
        # bare `gpt-6-luna` name; the doctor must expose the derived alias set
        # and the partial state so a dropped bare name is not read as an outage.
        self.assertIn("catalog_oauth_aliases=", text)
        self.assertIn("catalog_oauth_missing=", text)
        self.assertIn("available_partial", text)
        self.assertIn("unknown_route_manifest", text)
        # P2-D: local throttle rejections must carry a back-off signal, and the
        # header must stay scoped to those rejections.
        self.assertIn("safe-throttle-retry-after=OK", text)
        self.assertIn("throttle-retry-after-map-count=1", text)
        self.assertIn("add_header Retry-After $cpa_throttle_retry_after always;", text)
        self.assertIn(
            'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {',
            text,
        )
        # nginx reaches CPA over loopback, so the jail must never be able to ban
        # 127.0.0.1: the doctor's own unauthenticated probe would otherwise feed
        # it 401s until the gateway lost its upstream. Guard the source of truth
        # and the deployed reading.
        self.assertIn("fail2ban-ban-scope=loopback_exempt_incremental", text)
        self.assertIn("ignoreip = 127.0.0.1/8 ::1", text)
        jail_source = (
            repo_root / "scripts" / "remote" / "cpa-fail2ban-jail.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("ignoreip = 127.0.0.1/8 ::1", jail_source)
        self.assertIn("bantime = 3600", jail_source)
        self.assertIn("bantime.increment = true", jail_source)
        self.assertIn("bantime.factor = 2", jail_source)
        self.assertIn("bantime.maxtime = 604800", jail_source)

    def test_cpa_doctor_catalog_check_fails_closed_on_unknown_ids(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        # Execute the doctor's embedded catalog checker exactly as the remote
        # shell does: program from -c, manifest path as argv[1], catalog on
        # stdin. String assertions alone cannot prove the fail-closed branch.
        marker = "if printf '%s' \"$MODEL_CATALOG\" | python3 -c '"
        program = text.split(marker, 1)[1].split(
            '\' "$DIR/cpa_provider_routes.json"; then', 1
        )[0]
        manifest = repo_root / "scripts" / "remote" / "cpa_provider_routes.json"
        allowed_ids = [
            "glm-5.3",
            "glm-5.3-flash",
            "deepseek-flash",
            "deepseek-v4-pro",
            "gpt-6-astra",
            "gpt-5.6-sol",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "gpt-6-luna",
            "gpt-5.6-luna",
        ]
        for ids, expected_code, expected_unknown in (
            (allowed_ids, 0, "none"),
            ([*allowed_ids, "gpt-5.5"], 1, "gpt-5.5"),
            ([*allowed_ids, "r1/unknown-model"], 1, "r1/unknown-model"),
            # A legitimate cooldown only removes IDs: still a clean contract.
            (allowed_ids[:-1], 0, "none"),
            ([], 0, "none"),
        ):
            with self.subTest(ids=ids):
                completed = subprocess.run(
                    [sys.executable, "-c", program, str(manifest)],
                    input=json.dumps({"data": [{"id": item} for item in ids]}),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, expected_code, completed.stderr)
                self.assertIn(f"MODEL_IDS_UNKNOWN={expected_unknown}", completed.stdout)
                self.assertIn("MODEL_IDS=", completed.stdout)
        # Malformed catalog and absent manifest both fail closed without a
        # traceback reaching the doctor output.
        for raw, argv in (
            ("{not json", str(manifest)),
            ("{}", str(manifest)),
            ('{"data": []}', str(manifest) + ".absent"),
        ):
            with self.subTest(raw=raw, argv=argv):
                completed = subprocess.run(
                    [sys.executable, "-c", program, argv],
                    input=raw,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 1)
                self.assertNotIn("Traceback", completed.stderr)

    def test_cpa_throttle_retry_after_is_scoped_and_syntactically_valid(self) -> None:
        # A locally throttled client must get an explicit back-off signal, while
        # every other response (200/401/404 and pass-through upstream 429/5xx)
        # must keep its own headers untouched. The header is therefore gated by a
        # map over the throttle status variables rather than applied blindly.
        repo_root = Path(__file__).resolve().parent
        source = (repo_root / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        # The embedded heredoc is opaque to PowerShell's parser and to bash -n, so
        # a syntax error in it would only surface on the remote host mid-apply.
        block = apply_payload.split("if ! python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[
            0
        ]
        compile(block, "cpa-guardrails-nginx-patch", "exec")

        self.assertIn(
            'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {',
            block,
        )
        # The map body is built as an escaped Python literal, so assert the
        # source form: a rendered `default "";` never appears in the payload.
        self.assertIn('default \\"\\";', block)
        self.assertIn('\\"~REJECTED\\" 1;', block)
        self.assertIn("add_header Retry-After $cpa_throttle_retry_after always;", block)
        # Both limiter kinds reject through the same map key, so the header also
        # covers limit_conn rejections, not just limit_req.
        self.assertIn("$limit_req_status:$limit_conn_status", block)
        # Idempotence: a second apply must not duplicate the map, and a
        # hand-edited map must fail closed instead of being silently accepted.
        self.assertIn("throttle_retry_after_map + nginx", block)
        self.assertIn(
            "existing cpa_throttle_retry_after map differs from approved throttle map",
            block,
        )
        # Post-write verification must include the new contract, so a partial
        # write rolls back before the reload instead of shipping a half contract.
        for anchor in (
            "'add_header Retry-After $cpa_throttle_retry_after always;'",
            "'map \"$limit_req_status:$limit_conn_status\" $cpa_throttle_retry_after {'",
        ):
            self.assertIn(anchor, apply_payload)

    def test_cpa_doctor_luna_state_covers_the_whole_oauth_route(self) -> None:
        # Upstream/account entitlement churn can drop the bare `gpt-6-luna`
        # while the compatibility alias `gpt-5.6-luna` keeps serving. Keying the
        # OAuth lane state off that single name made one doctor run contradict
        # itself: ==client-model-catalog== listed the route while
        # ==cooldown-state== reported luna_state=unavailable_unclassified. The
        # state must come from the manifest's whole OAuth alias set.
        import http.server
        import socket
        import threading

        import yaml

        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        doctor = text.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = next(
            chunk.split("\nPY\n", 1)[0]
            for chunk in doctor.split("python3 - \"$DIR\" <<'PY'\n")[1:]
            if "cooldown_state_coverage=" in chunk
        )
        manifest_path = repo_root / "scripts" / "remote" / "cpa_provider_routes.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        oauth_aliases = sorted(
            {
                model["alias"]
                for route in manifest["oauth_routes"]
                for model in route["models"]
            }
        )
        self.assertEqual(oauth_aliases, ["gpt-5.6-luna", "gpt-6-luna"])

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # The block targets the real loopback gateway; retarget it at the stub
        # so the catalog branch is exercised without a live CPA.
        self.assertIn("http://127.0.0.1:8317/v1/models", block)
        block = block.replace(
            "http://127.0.0.1:8317/v1/models", f"http://127.0.0.1:{port}/v1/models"
        )

        served: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps({"data": [{"id": model} for model in served]}).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                return

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # addCleanup is LIFO: register the close first so shutdown runs before
        # the listening socket disappears from under serve_forever.
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "auth").mkdir()
            (root / "config.yaml").write_text(
                yaml.safe_dump({"api-keys": ["TEST_KEY"]}), encoding="utf-8"
            )
            (root / "cpa_provider_routes.json").write_bytes(manifest_path.read_bytes())

            def readings(models: list[str]) -> dict[str, str]:
                served[:] = models
                completed = subprocess.run(
                    [sys.executable, "-c", block, str(root)],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                parsed: dict[str, str] = {}
                for line in completed.stdout.decode("utf-8").splitlines():
                    if "=" in line:
                        key, _, value = line.partition("=")
                        parsed[key] = value
                return parsed

            # Only the compatibility alias is advertised: the lane is alive.
            partial = readings(["gpt-5.6-luna", "glm-5.3-flash"])
            self.assertEqual(partial["luna_state"], "available_partial")
            self.assertEqual(partial["catalog_gpt6_luna"], "absent")
            self.assertEqual(partial["catalog_oauth_aliases"], "gpt-5.6-luna")
            self.assertEqual(partial["catalog_oauth_missing"], "gpt-6-luna")

            # Both aliases advertised: fully available.
            full = readings(["gpt-5.6-luna", "gpt-6-luna"])
            self.assertEqual(full["luna_state"], "available")
            self.assertEqual(full["catalog_oauth_missing"], "none")
            self.assertEqual(full["catalog_gpt6_luna"], "present")

            # Nothing from the OAuth route: a genuine unclassified absence.
            absent = readings(["glm-5.3-flash"])
            self.assertEqual(absent["luna_state"], "unavailable_unclassified")
            self.assertEqual(absent["catalog_oauth_aliases"], "none")
            self.assertEqual(absent["catalog_oauth_missing"], "gpt-5.6-luna,gpt-6-luna")

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
        # Chunked uploads must pace their SSH channels instead of bursting.
        self.assertIn("Start-Sleep -Milliseconds 100", function)

    def test_cpa_guardrails_projection_hash_contract(self) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # Apply must verify every projected payload byte-for-byte at write
        # time; a mismatch rolls back instead of surviving as silent drift.
        self.assertIn("PROJECTION_HASH_MISMATCH", apply_payload)
        self.assertIn("PROJECTION_HASH_VERIFIED", apply_payload)
        for token in (
            "__CPA_UPDATER_SHA256__",
            "__CPA_HEALTH_SHA256__",
            "__CPA_POLICY_SHA256__",
            "__CPA_PROVIDER_ROUTES_SHA256__",
            "__CPA_FAIL2BAN_FILTER_SHA256__",
            "__CPA_FAIL2BAN_JAIL_SHA256__",
        ):
            self.assertIn(token, apply_payload)

        # Doctor compares the deployed copies against the same repo-side
        # hashes, so repo-ahead drift fails the contract instead of waiting
        # for a human to eyeball the sha listing.
        self.assertIn("==projection-drift==", doctor)
        self.assertIn("__CPA_PROJECTION_HASH_PAIRS__", doctor)
        self.assertIn('mark_fail "projection-drift-$drift_name"', doctor)
        self.assertIn("LIVE_MISSING", doctor)
        self.assertIn("MISMATCH", doctor)

        # The injected pair list must pass a strict format check before it
        # ever reaches the remote shell, and doctor hashes must anchor to the
        # committed source of truth (HEAD blob), not the working tree: with a
        # parallel session holding uncommitted edits in this worktree, a
        # working-tree anchor would report phantom drift.
        self.assertIn("^/[A-Za-z0-9._/-]+=[0-9a-f]{64}$", source)
        self.assertIn("Get-LfNormalizedSha256", source)
        self.assertIn("Get-HeadBlobSha256", source)
        self.assertIn('Get-HeadBlobSha256 "scripts/remote/cpa-auto-update.sh"', source)
        self.assertNotIn("/opt/cliproxyapi/auto-update.sh=$updaterSha256", source)

    def test_cpa_guardrails_apply_restarts_admission_service(self) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # enable --now is a no-op on an already enabled+active service, so a
        # re-apply would leave the previous generation's process running the
        # old projected code (2026-09-26: the SSE stream fix shipped without
        # ever being loaded). The forward path must restart the unit.
        self.assertNotIn("enable --now cpa-admission.service", apply_payload)
        self.assertIn("systemctl restart cpa-admission.service", apply_payload)
        self.assertIn("ROLLBACK admission_restart", apply_payload)

    def test_cpa_guardrails_doctor_bounds_access_log_scan(self) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        self.assertIn("scan_cap_bytes = 64 * 1024 * 1024", doctor)
        self.assertIn("log_scan_truncated", doctor)
        # The partial line at the truncation boundary must be dropped.
        self.assertIn("log_handle.readline()", doctor)

    def test_cpa_guardrails_doctor_observes_updater_timer_freshness(self) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        self.assertIn("LastTriggerUSec", doctor)
        self.assertIn("timer_last_trigger_age_hours", doctor)
        self.assertIn("timer_last_trigger=STALE", doctor)

    def test_cpa_guardrails_provider_env_defaults_to_appdata(self) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )

        # Provider credentials live in the user profile next to target.json;
        # the repo root holds no private env file.
        self.assertIn(
            'Join-Path $env:APPDATA "vps-ssh-launcher\\providers.env"',
            source,
        )
        self.assertNotIn("副本", source)

    def test_cpa_guardrails_payloads_are_valid_bash(self) -> None:
        bash = self._resolve_bash()
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
            "quarantine_oauth_luna": source.split("$quarantineScript = @'\n", 1)[
                1
            ].split("\n'@", 1)[0],
            "apply": source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0],
        }
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                completed = subprocess.run(
                    self._bash_command(bash, "-n"),
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

    def _run_oauth_retire_catalog_contract(
        self, catalog: dict[str, Any]
    ) -> "subprocess.CompletedProcess[bytes]":
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        block = payload.split(
            "python3 - /tmp/cpa-oauth-retire-catalog.json <<'PY'\n", 1
        )[1].split("\nPY\n", 1)[0]
        manifest_text = (
            Path(__file__).parent / "scripts/remote/cpa_provider_routes.json"
        ).read_text(encoding="utf-8")
        manifest_b64 = base64.b64encode(manifest_text.encode("utf-8")).decode("ascii")
        script = block.replace("__CPA_OAUTH_RETIRE_MANIFEST_B64__", manifest_b64)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(catalog, handle)
            catalog_path = handle.name
        try:
            return subprocess.run(
                [sys.executable, "-c", script, catalog_path],
                capture_output=True,
                timeout=30,
                check=False,
            )
        finally:
            os.unlink(catalog_path)

    def test_cpa_oauth_luna_deactivation_survival_contract_is_manifest_derived(
        self,
    ) -> None:
        manifest = json.loads(
            (
                Path(__file__).parent / "scripts/remote/cpa_provider_routes.json"
            ).read_text(encoding="utf-8")
        )
        provider_aliases = {
            model["alias"]
            for provider in manifest["providers"]
            for model in provider["models"]
        }
        optional = {
            model
            for provider in manifest["providers"]
            for model in provider.get("optional_models", [])
        }
        oauth_aliases = {
            model["alias"]
            for route in manifest["oauth_routes"]
            for model in route["models"]
        }

        def catalog(*ids: str) -> dict[str, Any]:
            return {"data": [{"id": model} for model in ids]}

        # Full post-removal catalog: every provider alias survives, no OAuth
        # alias remains.
        completed = self._run_oauth_retire_catalog_contract(catalog(*provider_aliases))
        self.assertEqual(completed.returncode, 0, completed.stderr)

        # Optional aliases may be absent (availability-filtered catalog).
        required_only = provider_aliases - optional
        completed = self._run_oauth_retire_catalog_contract(catalog(*required_only))
        self.assertEqual(completed.returncode, 0, completed.stderr)

        # A cooled-down channel hides a required alias: informational marker,
        # not a failure — the doctor gate owns persistent absence.
        incomplete = required_only - {"glm-5.3-flash"}
        completed = self._run_oauth_retire_catalog_contract(catalog(*incomplete))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "CATALOG_INCOMPLETE missing=glm-5.3-flash", completed.stdout.decode()
        )

        # A surviving OAuth alias means the removal failed its own goal.
        completed = self._run_oauth_retire_catalog_contract(
            catalog(*(required_only | oauth_aliases))
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CPA_ROUTE_VERIFICATION_FAILED", completed.stderr.decode())

        # Unknown IDs are not an alternate namespace; they fail closed.
        completed = self._run_oauth_retire_catalog_contract(
            catalog(*required_only, "ghost-model")
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unknown=ghost-model", completed.stderr.decode())

    def test_cpa_oauth_luna_deactivation_refuses_empty_manifest_contract(
        self,
    ) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        block = payload.split(
            "python3 - /tmp/cpa-oauth-retire-catalog.json <<'PY'\n", 1
        )[1].split("\nPY\n", 1)[0]
        empty_b64 = base64.b64encode(
            json.dumps({"providers": [], "oauth_routes": []}).encode("utf-8")
        ).decode("ascii")
        script = block.replace("__CPA_OAUTH_RETIRE_MANIFEST_B64__", empty_b64)
        completed = subprocess.run(
            [sys.executable, "-c", script, os.devnull],
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("REFUSE invalid route manifest", completed.stderr.decode())

    def test_cpa_oauth_quarantine_is_reversible_and_non_destructive(self) -> None:
        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payload = source.split("$quarantineScript = @'\n", 1)[1].split("\n'@", 1)[0]
        # Two explicit switches, exclusive with every other transaction mode.
        self.assertIn("[switch]$QuarantineOAuthLuna", source)
        self.assertIn("[switch]$RestoreOAuthLuna", source)
        self.assertIn("-not $QuarantineOAuthLuna", source)
        self.assertIn("-not $RestoreOAuthLuna", source)
        self.assertIn("$quarantineScript.Replace(", source)
        self.assertIn("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__", source)
        # Reversibility: the transaction only rewrites the OAuth exclusion list
        # plus a marker file. It must never read, copy, delete or replay
        # credential material, and never clear quota/cooldown state.
        self.assertIn('config_after["oauth-excluded-models"]["codex"] = after', payload)
        self.assertIn("marker_path.unlink()", payload)
        self.assertNotIn("reset-quota", payload)
        self.assertNotIn('cp -a "$AUTH_DIR"', payload)
        self.assertNotIn("--codex-device-login", payload)
        self.assertIn("QUOTA_STATE_RESET=no", payload)
        self.assertIn("OAUTH_CREDENTIAL_RETAINED=yes", payload)
        # Both directions verify the routed catalog and roll back on failure.
        self.assertIn("QUARANTINE_APPLIED", payload)
        self.assertIn("QUARANTINE_RELEASED", payload)
        self.assertIn("ROLLBACK_VERIFIED", payload)
        # A refusal must not cost a container restart. When config.yaml is
        # byte-identical to the backup nothing was mutated, so the rollback
        # restores files, skips the restart and reports the skip instead of
        # claiming a verified rollback it never performed.
        self.assertIn('cmp -s "$BK/config.yaml" "$CONFIG"', payload)
        self.assertIn("ROLLBACK_SKIPPED no_mutation", payload)
        restore_all = payload.split("restore_all() {\n", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("ROLLBACK_SKIPPED no_mutation", restore_all)
        skipped_branch, verified_branch = restore_all.split(
            "ROLLBACK_SKIPPED no_mutation", 1
        )
        self.assertNotIn(
            "docker restart cli-proxy-api",
            skipped_branch,
            "the no-mutation path must return before any container restart",
        )
        self.assertIn("docker restart cli-proxy-api", verified_branch)
        self.assertIn("survived", payload)
        self.assertIn("still_blocked", payload)
        # -Apply recomputes the exclusion list, so it must not silently undo an
        # explicit quarantine decision.
        apply_script = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.assertIn("REFUSE OAuth lane quarantine is active", apply_script)
        # The doctor owns the state readback, including the unmarked-block case.
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.assertIn("==oauth-quarantine==", doctor)
        self.assertIn("oauth_quarantine=UNMARKED_OAUTH_BLOCK", doctor)
        self.assertIn("mark_fail oauth-quarantine", doctor)

    def test_cpa_oauth_quarantine_state_change_is_reversible_and_policy_clean(
        self,
    ) -> None:
        import runpy

        import yaml

        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payload = source.split("$quarantineScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = payload.split('python3 - "$CONFIG" "$MARKER" "$MODE" <<\'PY\'\n', 1)[
            1
        ].split("\nPY\n", 1)[0]
        manifest_b64 = base64.b64encode(
            (
                Path(__file__).parent / "scripts/remote/cpa_provider_routes.json"
            ).read_bytes()
        ).decode("ascii")
        script = block.replace("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__", manifest_b64)

        policy = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            marker_path = Path(directory) / "oauth-quarantine.json"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

            def validate(candidate: dict[str, Any]) -> list[str]:
                return cast(
                    list[str],
                    policy["validate_config"](candidate, marker_path=marker_path),
                )

            def read_config() -> dict[str, Any]:
                return cast(
                    dict[str, Any],
                    yaml.safe_load(config_path.read_text(encoding="utf-8")),
                )

            def run(mode: str) -> "subprocess.CompletedProcess[bytes]":
                return subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(config_path),
                        str(marker_path),
                        mode,
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )

            self.assertEqual(validate(read_config()), [])

            quarantined = run("quarantine")
            self.assertEqual(quarantined.returncode, 0, quarantined.stderr)
            self.assertIn(b"QUARANTINE_APPLIED", quarantined.stdout)
            self.assertTrue(marker_path.exists())
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(
                marker["previous_codex_exclusions"],
                config["oauth-excluded-models"]["codex"],
            )
            after = read_config()
            self.assertEqual(
                marker["applied_codex_exclusions"],
                after["oauth-excluded-models"]["codex"],
            )
            # The quarantine state must satisfy the semantic policy, and it must
            # change nothing outside the OAuth exclusion list.
            self.assertEqual(validate(after), [])
            self.assertEqual(
                {
                    key: value
                    for key, value in after.items()
                    if key != "oauth-excluded-models"
                },
                {
                    key: value
                    for key, value in config.items()
                    if key != "oauth-excluded-models"
                },
            )
            self.assertEqual(
                sorted(
                    set(after["oauth-excluded-models"]["codex"])
                    - set(config["oauth-excluded-models"]["codex"])
                ),
                ["gpt-5.6-luna", "gpt-6-luna"],
            )
            if os.name != "nt":
                self.assertEqual(int(config_path.stat().st_mode) & 0o777, 0o600)
                self.assertEqual(int(marker_path.stat().st_mode) & 0o777, 0o600)

            # Re-quarantining is refused instead of silently no-op.
            self.assertNotEqual(run("quarantine").returncode, 0)

            # A manual edit during the quarantine window must not be silently
            # overwritten by restore; the operator must resolve the drift.
            drifted = read_config()
            drifted["oauth-excluded-models"]["codex"].append("manual-drift")
            config_path.write_text(yaml.safe_dump(drifted), encoding="utf-8")
            refused_restore = run("restore")
            self.assertNotEqual(refused_restore.returncode, 0)
            self.assertTrue(marker_path.exists())
            config_path.write_text(yaml.safe_dump(after), encoding="utf-8")

            released = run("restore")
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertIn(b"QUARANTINE_RELEASED", released.stdout)
            self.assertFalse(marker_path.exists())
            self.assertEqual(read_config(), config)
            self.assertEqual(validate(read_config()), [])

            # Restoring without an active quarantine is refused.
            self.assertNotEqual(run("restore").returncode, 0)

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
        self.assertIn("limit_req_status 429;", apply_script)
        self.assertIn("limit_conn_status 429;", apply_script)
        self.assertIn("ensure_nginx_directive", apply_script)

    def test_cpa_apply_ensure_nginx_directive_repairs_missing_status(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        apply_script = text.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        embedded_python = apply_script.split("if ! python3 - <<'PY'\n", 1)[1].split(
            "\nPY\nthen", 1
        )[0]
        start = embedded_python.index("def ensure_nginx_directive(")
        end = embedded_python.index("\nnginx = ensure_nginx_directive(", start)
        namespace: dict[str, Any] = {}
        exec(embedded_python[start:end], namespace)
        ensure = cast(Any, namespace["ensure_nginx_directive"])

        base = (
            "server {\n"
            "  limit_req_zone $binary_remote_addr zone=cpa_rl:1m rate=10r/s;\n"
            "  limit_conn_zone $binary_remote_addr zone=cpa_cc:1m;\n"
            "  location / {\n"
            "    limit_req zone=cpa_rl burst=10;\n"
            "    limit_conn cpa_cc 6;\n"
            "  }\n"
            "}\n"
        )
        repaired = ensure(
            base, "limit_req_status 429;", "limit_req zone=cpa_rl burst=10;"
        )
        repaired = ensure(repaired, "limit_conn_status 429;", "limit_conn cpa_cc 6;")
        # Inserted inside the limiter's own block, preserving its indentation.
        self.assertIn("\n    limit_req_status 429;\n", repaired)
        self.assertIn("\n    limit_conn_status 429;\n", repaired)
        self.assertEqual(repaired.count("limit_req_status 429;"), 1)
        # Idempotent: a config that already carries the directive is untouched.
        self.assertEqual(
            ensure(
                repaired, "limit_req_status 429;", "limit_req zone=cpa_rl burst=10;"
            ),
            repaired,
        )
        # A missing anchor fails closed instead of silently dropping the control.
        with self.assertRaises(SystemExit):
            ensure(
                "server {}\n",
                "limit_req_status 429;",
                "limit_req zone=cpa_rl burst=10;",
            )

    def test_cpa_apply_exit_and_signal_failures_invoke_rollback_once(self) -> None:
        bash = self._resolve_bash()
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
                    self._bash_command(bash),
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

    def test_cpa_shared_account_admission_is_projected_and_route_scoped(self) -> None:
        repo_root = Path(__file__).resolve().parent
        guardrails = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        admission_config = json.loads(
            (repo_root / "scripts" / "remote" / "cpa-admission.json").read_text(
                encoding="utf-8"
            )
        )
        admission_unit = (
            repo_root / "scripts" / "remote" / "cpa-admission.service"
        ).read_text(encoding="utf-8")

        self.assertIn("__CPA_ADMISSION_B64__", guardrails)
        self.assertIn("__CPA_ADMISSION_CONFIG_B64__", guardrails)
        self.assertIn("__CPA_ADMISSION_UNIT_B64__", guardrails)
        self.assertIn(
            "proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;", guardrails
        )
        self.assertIn(
            "proxy_pass http://127.0.0.1:8317/v1/models$is_args$args;", guardrails
        )
        self.assertIn("retry_after_max_seconds", guardrails)
        self.assertIn("cpa-luna-admission.service", guardrails)
        self.assertIn("LEGACY_ADMISSION_WAS_ACTIVE", guardrails)
        self.assertIn("systemctl stop cpa-luna-admission.service", guardrails)
        self.assertIn("systemctl disable cpa-luna-admission.service", guardrails)
        self.assertIn('rm -f "$LEGACY_ADMISSION_UNIT"', guardrails)
        self.assertIn("legacy-admission=absent", guardrails)
        self.assertEqual(admission_config["retry_after_max_seconds"], 86400)
        self.assertEqual(
            {lane["name"]: lane["models"] for lane in admission_config["lanes"]},
            {
                "chatgpt-oauth": ["gpt-6-luna", "gpt-5.6-luna"],
                "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
                "deepseek-official": ["deepseek-flash", "deepseek-v4-pro"],
            },
        )
        self.assertIn(
            "cpa-admission.py /opt/cliproxyapi/cpa-admission.json", admission_unit
        )

    def test_cpa_acceptance_synthetic_upstream_matches_wire_contract(self) -> None:
        import io
        import runpy

        module = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-acceptance.py")
        )
        handler_class = module["Upstream"]
        module["STATE"]["mode"] = "ok"

        def run_handler(path: str, body: dict[str, Any]) -> tuple[int, bytes]:
            payload = json.dumps(body).encode()
            handler = handler_class.__new__(handler_class)
            handler.rfile = io.BytesIO(payload)
            handler.wfile = io.BytesIO()
            handler.headers = {"Content-Length": str(len(payload))}
            handler.request_version = "HTTP/1.1"
            handler.requestline = f"POST {path} HTTP/1.1"
            handler.path = path
            module["STATE"]["calls"] = 0
            handler.do_POST()
            return module["STATE"]["calls"], handler.wfile.getvalue()

        # Openai-compat lanes relay the upstream chat body verbatim (v7.3.16),
        # so the fixture must answer chat completions with a real chat payload.
        calls, raw = run_handler(
            "/v1/chat/completions",
            {"model": "glm-5.3-flash", "messages": [], "max_tokens": 64},
        )
        self.assertEqual(calls, 1)
        head, _, body = raw.partition(b"\r\n\r\n")
        self.assertIn(b"200 OK", head)
        self.assertIn(b"application/json", head)
        payload = json.loads(body)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["model"], "glm-5.3-flash")
        self.assertEqual(payload["choices"][0]["message"]["content"], "OK")
        self.assertEqual(payload["choices"][0]["finish_reason"], "stop")

        calls, raw = run_handler(
            "/v1/chat/completions",
            {"model": "glm-5.3-flash", "messages": [], "stream": True},
        )
        self.assertEqual(calls, 1)
        self.assertIn(b'"object": "chat.completion.chunk"', raw)
        self.assertIn(b'"finish_reason": "stop"', raw)
        self.assertIn(b"data: [DONE]", raw)

        # The codex-api-key responses lane keeps the responses-SSE contract.
        calls, raw = run_handler(
            "/v1/responses",
            {"model": "gpt-6-luna", "input": "Reply OK", "stream": True},
        )
        self.assertEqual(calls, 1)
        self.assertIn(b'"type": "response.created"', raw)

        module["STATE"]["mode"] = "http503"
        calls, raw = run_handler("/v1/chat/completions", {"model": "glm-5.3-flash"})
        self.assertEqual(calls, 1)
        self.assertIn(b"503", raw.partition(b"\r\n\r\n")[0])
        self.assertIn(b"server_is_overloaded", raw)

    def test_cpa_health_skips_manifest_image_models_in_generation_matrices(
        self,
    ) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        required_models = [
            "glm-5.3-flash",
            "glm-5.3",
            "gpt-6-astra",
            "gpt-5.6-sol",
            "gpt-6-astra-cii",
            "gpt-6-sol-cii",
            "gpt-6-sol-91",
            "gpt-5.6-terra",
            "deepseek-flash",
            "deepseek-v4-pro",
        ]
        catalog = {
            "data": [
                {"id": model}
                for model in required_models
                + ["gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna", "gpt-image-2.5"]
            ]
        }
        self.assertEqual(
            check({}, "readiness", mock.Mock(return_value=catalog), mock.Mock()), 0
        )
        probed = required_models + ["gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna"]

        def echo(_path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            if body is None:
                return catalog
            return {
                "model": CPA_TEST_PROVIDER_ALIASES.get(body["model"], body["model"]),
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }

        request = mock.Mock(side_effect=echo)
        lines: list[str] = []
        # The image-model filter is independent of the OAuth lane opt-out, so
        # opt the lane back in to keep the full expected probe set observable.
        with mock.patch.dict(os.environ, {"CPA_HEALTH_INCLUDE_OAUTH": "1"}):
            self.assertEqual(
                check({}, "generation-all", request, mock.Mock(), lines.append), 0
            )
        requested = {call.args[1]["model"] for call in request.call_args_list[1:]}
        self.assertEqual(request.call_count, 1 + len(probed))
        self.assertEqual(requested, set(probed))
        self.assertNotIn("gpt-image-2.5", requested)
        self.assertTrue(
            any(
                line.startswith("ROUTE_PREPARED model=gpt-image-2.5 ")
                and "status=skipped kind=image" in line
                for line in lines
            )
        )

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
        self.assertIn("[string]$RemoteApplySha256", text)
        self.assertIn("-Apply requires a 64-hex -RemoteApplySha256", text)
        self.assertIn("REMOTE_APPLY_SCRIPT_HASH_MISMATCH", text)
        self.assertIn("POST_APPLY_VERIFICATION_FAILED", text)
        self.assertIn("restore_known_state", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)

        check_command = text.split("$checkCommand = @'", 1)[1].split("'@", 1)[0]
        # Regression guard: single-quoted here-strings pass backticks to bash
        # verbatim, which once broke the read-only check silently.
        self.assertNotIn(
            "`",
            check_command,
            "single-quoted here-strings pass backticks to bash verbatim; "
            "escape $ only inside double-quoted here-strings",
        )

    def test_vps_maintenance_wrapper_is_fresh_and_dry_run_by_default(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "vps_maintenance.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Fresh maintenance runs require -RunIntegration", text)
        self.assertIn("-live-inventory", text)
        self.assertIn("-run-integration", text)
        self.assertIn("-Apply requires -RemoteWrite", text)
        self.assertIn("-AutoApply", text)
        self.assertIn('"--unattended"', text)
        self.assertIn("VPS_SSH_LAUNCHER_RUN_INTEGRATION", text)
        self.assertNotIn("Register-ScheduledTask", text)

    def test_vps_maintenance_task_is_observe_only(self) -> None:
        text = (
            Path(__file__).resolve().parent
            / "scripts"
            / "install_vps_maintenance_task.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess = $true", text)
        self.assertIn('[string]$At = "20:00"', text)
        self.assertIn("-RunIntegration", text)
        self.assertIn("[switch]$AutoApply", text)
        self.assertIn("-AutoApply", text)
        self.assertIn('"-WindowStyle", "Hidden"', text)
        self.assertIn("-Hidden `", text)
        # S4U keeps the daily run alive with no interactive logon; the two-hour
        # limit avoids killing a remote transaction mid-flight.
        self.assertIn("-LogonType S4U", text)
        self.assertIn("New-TimeSpan -Hours 2", text)
        # S4U needs elevation and Unregister-then-Register can lose the task
        # when Register fails: both must be guarded.
        self.assertIn("requires an elevated pwsh", text)
        self.assertIn(
            "A failed update must not leave the host without its daily task.", text
        )
        self.assertIn("mode=observe-only", text)
        self.assertIn("silent=true", text)
        self.assertNotIn('"-Apply"', text)
        self.assertNotIn('"-RemoteWrite"', text)

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
        self.assertIn("-Apply requires -Version", text)
        self.assertIn("-Apply requires a 64-hex -InstalledSha256", text)
        self.assertIn("-Apply requires a 64-hex -VasmaSha256", text)
        self.assertIn("TARGET_VERSION", text)
        self.assertIn("EXPECTED_SHA256", text)
        self.assertIn("EXPECTED_VASMA_SHA256", text)
        self.assertIn("verify_target_xray", text)
        self.assertIn("verify_target_singbox", text)
        self.assertIn("pinned Xray version and hash already match", text)
        self.assertIn("pinned sing-box version and hash already match", text)
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", text)

        # The menu pipeline is position-coupled to the deployed vasma prompts;
        # both wrappers must verify the expected menu anchors before driving it.
        self.assertEqual(text.count("verify_vasma_anchors"), 5)
        self.assertIn("16.core管理", text)
        self.assertIn("xrayVersionManageMenu", text)
        self.assertIn("singBoxVersionManageMenu", text)
        self.assertIn("1.升级Xray-core", text)
        self.assertIn("1.升级 sing-box", text)
        self.assertIn("1.Upgrade Xray-core", text)
        self.assertIn("1. Upgrade sing-box", text)
        self.assertIn("menu anchors missing", text)
        self.assertIn("exit 12", text)
        self.assertIn("exit 13", text)
        self.assertIn("read_crontab_or_empty", text)
        self.assertIn("SINGBOX_ROUTE_FRAGMENT", text)
        self.assertNotIn("crontab -l 2>/dev/null | grep", text)

        # Scheduling must live in /etc/cron.d, not root's crontab: vasma's
        # installCronTLS rewrites `crontab -l` with `sed '/v2ray-agent/d'`,
        # which silently deleted the crontab line on bwg on 2026-09-24.
        self.assertIn("/etc/cron.d/vps-launcher-kernel-update", text)
        self.assertIn("root /bin/bash", text)

    def test_rendered_vasma_wrappers_are_valid_bash(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        for function_name in ("write_xray_wrapper", "write_singbox_wrapper"):
            with self.subTest(wrapper=function_name):
                wrapper = self._render_embedded_wrapper(source, function_name)
                completed = subprocess.run(
                    self._bash_command(bash, "-n"),
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
        bash = self._resolve_bash()
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
                    self._bash_command(bash, "-s"),
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

    def test_system_maintenance_cron_safety_contracts(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")

        # Monthly maintenance must never reboot the host on its own.
        self.assertIn("NOT rebooting automatically", text)
        self.assertNotIn("systemctl reboot", text)
        self.assertNotIn("reboot -f", text)
        self.assertNotIn("shutdown -r", text)

        # Unattended apt runs must be non-interactive and must keep local
        # config files instead of blocking the cron job on a conffile prompt.
        self.assertIn("DEBIAN_FRONTEND=noninteractive", text)
        self.assertIn("--force-confdef", text)
        self.assertIn("--force-confold", text)
        # Allow dependency-only package transitions (for example Ubuntu's
        # split linux-firmware packages) without enabling removals as
        # full-upgrade would.
        self.assertIn("apt-get upgrade --with-new-pkgs", text)
        self.assertIn("dpkg --audit", text)
        self.assertIn("apt-get check", text)
        self.assertIn("apt-get -s -o Debug::NoLocking=1 upgrade", text)
        self.assertIn("package-state-sha256-before=", text)
        self.assertIn("package-state-sha256-after=", text)
        self.assertIn("FAILURE_SUMMARY=", text)

        # Kernel updates share the same lock, so a monthly run must never
        # overlap an in-flight vasma kernel update.
        self.assertIn('LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"', text)

        # Every apply path must be wrapped in the rollback trap with verified
        # backup/restore state, and cron install may only drop its own line.
        self.assertIn("backup_apply_state", text)
        self.assertIn("restore_apply_state", text)
        self.assertIn("trap rollback_apply ERR INT TERM", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("read_crontab_or_empty", text)
        self.assertIn(
            "sed -E '/\\/usr\\/local\\/sbin\\/monthly-maintenance\\.sh/d'", text
        )
        self.assertNotIn("crontab -l 2>/dev/null | grep", text)

        # An apt phase that bounces dockerd must be followed by an explicit
        # container recovery check: snapshot before, re-verify after, one
        # explicit start attempt per straggler, and a recorded failure if
        # anything stays down. The wrapper must never restart the daemon.
        self.assertIn("DOCKER_SNAPSHOT", text)
        self.assertIn("re-verifying docker containers", text)
        self.assertIn("attempting explicit start", text)
        self.assertIn("containers still down after recovery attempt", text)
        self.assertNotIn("systemctl restart docker", text)

        # Proxy checks are collected in one pass so one broken service does
        # not hide the state of the remaining services from the maintenance log.
        self.assertIn("local checked=0 failed=0", text)
        self.assertIn('return "`$failed"', text)
        self.assertIn("verify_proxy_services", text)

        # Scheduling must live in /etc/cron.d, not root's crontab: vasma's
        # installCronTLS rewrites `crontab -l` with `sed '/v2ray-agent/d'`,
        # which silently deleted the crontab line on bwg on 2026-09-24.
        self.assertIn("/etc/cron.d/vps-launcher-monthly-maintenance", text)
        self.assertIn("root /bin/bash", text)

    def test_rendered_maintenance_wrapper_is_valid_bash(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parent / "scripts" / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        wrapper = self._render_embedded_wrapper(source, "write_maintenance_wrapper")
        completed = subprocess.run(
            self._bash_command(bash, "-n"),
            input=wrapper.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
        output = (completed.stdout + completed.stderr).decode(
            "utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, output)

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

    def test_run_gates_covers_profiles_without_duplicate_tools(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")

        self.assertIn("project_environment.ps1", text)
        self.assertIn('"vps_ssh_launcher"', text)
        self.assertIn('"pytest"', text)
        self.assertIn('"test_cpa_admission.py"', text)
        self.assertIn('[ValidateSet("Focused", "Full", "Integration")]', text)
        self.assertIn('[string]$Profile = "Full"', text)
        self.assertIn("[string[]]$FocusPath = @()", text)
        self.assertIn('-split ","', text)
        self.assertIn('"focused:test"', text)
        self.assertIn('"integration:test"', text)
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
            repo_root / "scripts" / "v2ray_agent_script_update_cron.ps1",
            repo_root / "scripts" / "system_maintenance_cron.ps1",
            repo_root / "scripts" / "vps_maintenance.ps1",
            repo_root / "scripts" / "v2ray_agent_renewtls_cron.ps1",
            repo_root / "scripts" / "bwg_full_maintenance.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn("project_environment.ps1", text)
                self.assertNotIn("function Resolve-ProjectPython", text)

    def test_v2ray_agent_script_updater_only_replaces_management_script(self) -> None:
        repo_root = Path(__file__).resolve().parent
        updater = repo_root / "scripts" / "remote" / "v2ray-agent-script-update.sh"
        text = updater.read_text(encoding="utf-8")
        self.assertIn("SOURCE_REPOSITORY=", text)
        self.assertIn("SOURCE_REF=", text)
        self.assertIn("EXPECTED_CANDIDATE_SHA=", text)
        self.assertIn("candidate_sha_unpinned", text)
        self.assertIn("--check", text)
        self.assertIn("--apply", text)
        self.assertIn("flock -n", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("coreVersionManageMenu", text)
        self.assertIn("xrayVersionManageMenu", text)
        self.assertNotIn("/usr/bin/vasma", text)
        self.assertNotIn("/usr/sbin/vasma", text)
        self.assertNotIn("printf '16", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("rc-service .* restart", text)

        completed = subprocess.run(
            ["bash", "-n", updater.relative_to(repo_root).as_posix()],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_v2ray_agent_script_projection_is_pinned_and_backup_first(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "v2ray_agent_script_update_cron.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[string]$InstallSha256", text)
        self.assertIn("-Apply requires -InstallSha256", text)
        self.assertIn("strict-host-key-checking", text)
        self.assertIn("/var/backups/v2ray-agent-script-update-deploy", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("RUNTIME_VERIFY_OK", text)
        self.assertIn("/etc/cron.d/vps-launcher-v2ray-agent-update", text)
        self.assertIn("v2ray-agent-source-pin.json", text)
        self.assertIn("SOURCE_REF=", text)

    def test_v2ray_agent_source_pin_and_renewtls_lock_contracts(self) -> None:
        repo_root = Path(__file__).resolve().parent
        pin = json.loads(
            (repo_root / "scripts/remote/v2ray-agent-source-pin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pin["repository"], "https://github.com/mack-a/v2ray-agent")
        self.assertRegex(pin["ref"], r"^[0-9a-f]{40}$")
        self.assertRegex(pin["install_sha256"], r"^[0-9a-f]{64}$")

        updater = (repo_root / "scripts/remote/v2ray-agent-script-update.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("SOURCE_REF=", updater)
        self.assertIn("EXPECTED_CANDIDATE_SHA=", updater)
        self.assertIn("candidate_sha_unpinned", updater)
        self.assertNotIn(
            'SOURCE_URL="https://raw.githubusercontent.com/mack-a/v2ray-agent/master',
            updater,
        )

        renewtls = (repo_root / "scripts/v2ray_agent_renewtls_cron.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", renewtls)
        self.assertIn("/etc/cron.d/vps-launcher-v2ray-agent-renewtls", renewtls)
        self.assertIn("/etc/v2ray-agent/install.sh RenewTLS", renewtls)
        self.assertIn("verify_rollback_state", renewtls)
        self.assertIn("ROLLBACK_VERIFIED", renewtls)

    def test_bwg_full_maintenance_is_scoped_and_serial(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts/bwg_full_maintenance.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('[ValidateSet("Observe", "RunNow")]', text)
        self.assertIn('"bwg"', text)
        self.assertIn("-RunIntegration", text)
        self.assertIn("monthly-maintenance.sh", text)
        self.assertIn("vps-launcher-v2ray-agent-update.sh --apply", text)
        self.assertIn("ssh_tool.py", text)
        self.assertIn("REMOTE_OUTPUT_LINES=", text)
        self.assertIn("cpa-doctor-pre", text)
        self.assertIn("cpa-doctor-post", text)
        self.assertNotIn("DeactivateOAuthLuna", text)
        self.assertNotIn("ConsumeUsageQueue", text)
        self.assertNotIn("RotatePath", text)

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
        self.assertIn('"-Profile", "Integration"', workflow)
        self.assertIn("python -m pip install -e . pytest", workflow)
        self.assertNotIn('"${{ inputs.integration_profile }}"', workflow)

    def test_ci_workflow_filters_receipts_and_cancels_stale_non_main_runs(
        self,
    ) -> None:
        workflow = (
            Path(__file__).resolve().parent / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("branches:\n      - main", workflow)
        self.assertIn('"docs/change-evidence/**"', workflow)
        self.assertIn('".workbuddy-ai/**"', workflow)
        self.assertIn('"outputs/**"', workflow)
        self.assertIn("group: ci-${{ github.workflow }}-${{ github.ref }}", workflow)
        self.assertIn(
            "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}", workflow
        )
        self.assertIn("-Profile Full @gateArgs", workflow)

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
