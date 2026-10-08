"""test_cpa_policy_runtime.py - split from test_scripts.py (domain: policy)."""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, cast
from cpa_catalog_expectations import (
    ADMISSION_LANE_MODELS,
    OAUTH_ROUTE_ALIASES,
)

from script_validation_support import (
    CPA_TEST_PROVIDER_ALIASES,
    HEALTH_FIXTURE_CATALOG_IDS,
    ScriptValidationMixin,
    read_guardrail_source,
)


class CpaPolicyRuntimeTests(ScriptValidationMixin, unittest.TestCase):
    def test_sidecar_projection_checks_installed_app_before_mutation(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/cockpit_sidecar_guardrails.ps1"
        ).read_text(encoding="utf-8")
        project = source.split('if ($Mode -eq "Project") {', 1)[1]
        self.assertIn("Installed Cockpit version mismatch; no files changed", project)
        self.assertLess(
            project.index("Installed Cockpit version mismatch"),
            project.index("Copy-Item"),
        )
        self.assertIn("-not $installedAppVersion", project)

    def test_cpa_policy_rejects_nested_retry_and_quota_fallback_overrides(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        route_manifest = policy["ROUTE_MANIFEST"]
        self.assertEqual(policy["_route_manifest_issues"](route_manifest), [])
        self.assertEqual(
            policy["EXPECTED_OAUTH_ROUTE_ALIASES"],
            set(OAUTH_ROUTE_ALIASES),
        )
        duplicate_route_manifest = json.loads(json.dumps(route_manifest))
        duplicate_route_manifest["providers"][1]["models"][0]["alias"] = "gpt-6-astra"
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
                    "stream-bootstrap-buffering": False,
                    "stream-bootstrap-timeout": "0",
                },
                "oauth-excluded-models": {
                    "codex": ["codex-*", "gpt-5.7*"]
                    + list(route_manifest["oauth_exclusions"])
                },
                "openai-compatibility": compatibility,
            },
        )
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
            "gpt-6.1-sol-input",
            "gpt-6-astra",
            "gpt-6-astra-ciii",
            "gpt-6.1-sol-ciii",
            "gpt-6.1-sol-91",
        ]
        config["openai-compatibility"][ai_input_index]["models"].remove(
            {"name": "gpt-6-astra", "alias": "gpt-6-astra"}
        )
        issues = policy["validate_config"](config)
        self.assertTrue(any("models=" in issue for issue in issues))
        config["openai-compatibility"][ai_input_index]["models"].append(
            {"name": "gpt-6-astra", "alias": "gpt-6-astra"}
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
        config["codex"]["stream-bootstrap-timeout"] = "0"
        # Re-enabling the hold must be a policy violation, not a quiet
        # regression: it delays every response header until the upstream
        # generates its first token.
        config["codex"]["stream-bootstrap-buffering"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("stream-bootstrap-buffering" in issue for issue in issues))
        config["codex"]["stream-bootstrap-buffering"] = False
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
            "gpt-6-astra"
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

    def test_cpa_policy_oauth_quarantine_requires_a_matching_marker(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        oauth_aliases = sorted(policy["EXPECTED_OAUTH_ROUTE_ALIASES"])
        self.assertEqual(
            oauth_aliases,
            sorted(OAUTH_ROUTE_ALIASES),
        )

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
                if pattern != "gpt-6.1-sol"
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
                {"version": 1, "state": "quarantined", "aliases": ["gpt-6-sol-input"]}
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

    def test_cpa_cache_canary_measures_usage_without_exposing_session_or_prompt(
        self,
    ) -> None:
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        cache_canary = script["cache_canary"]
        format_metrics = script["_format_cache_metrics"]
        catalog = {"data": [{"id": model} for model in HEALTH_FIXTURE_CATALOG_IDS]}
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

    def test_cpa_cache_canary_selects_non_oauth_lane_model(self) -> None:
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        cache_canary = script["cache_canary"]
        format_metrics = script["_format_cache_metrics"]
        catalog = {"data": [{"id": model} for model in HEALTH_FIXTURE_CATALOG_IDS]}
        success = {
            "model": "glm-5.3",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1500,
                "prompt_tokens_details": {"cached_tokens": 1400},
            },
        }
        request = mock.Mock(side_effect=[catalog, success, success])
        result, metrics = cache_canary({}, request, mock.Mock(), model="glm-5.3")
        self.assertEqual(result, 0)
        self.assertEqual(metrics[1]["cache_read_tokens"], 1400)
        rendered = format_metrics(2, metrics[1], model="glm-5.3")
        self.assertIn("model=glm-5.3", rendered)
        self.assertIn("hit_ratio=0.9333", rendered)
        bodies = [call.args[1] for call in request.call_args_list[1:]]
        self.assertEqual(bodies[0]["model"], "glm-5.3")
        self.assertEqual(bodies[0]["max_tokens"], 1024)

        # An OAuth-lane or unknown name is refused locally before any request,
        # keeping the canary off the shared Plus account by construction.
        denied = mock.Mock()
        with self.assertRaises(ValueError):
            cache_canary({}, denied, mock.Mock(), model="gpt-6-luna")
        with self.assertRaises(ValueError):
            cache_canary({}, denied, mock.Mock(), model="not-a-model")
        denied.assert_not_called()

        # A model echo mismatch is a local contract failure, not a cache sample.
        mismatch = mock.Mock(
            side_effect=[catalog, {**success, "model": "glm-5.3-flash"}]
        )
        self.assertEqual(
            cache_canary({}, mismatch, mock.Mock(), model="glm-5.3")[0], 20
        )

    def test_cpa_quality_eval_requires_reasoning_instruction_tool_and_context_cases(
        self,
    ) -> None:
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        check = script["check"]
        cases = script["_QUALITY_EVAL_CASES"]
        models = tuple(HEALTH_FIXTURE_CATALOG_IDS)
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

    def test_cpa_recovery_verify_handles_public_gateway_mode(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "cpa_recovery_workflow.ps1").read_text(
            encoding="utf-8"
        )
        # The public fq route intentionally has no local 10909/14185 provider
        # gateway. Verify must use the selected provider target to choose the
        # correct state contract instead of failing on absent local listeners.
        for token in (
            "cockpit_provider_health.py",
            "COCKPIT_GATEWAY_MODE=public_gateway",
            "COCKPIT_SIDECAR_VERIFY=SKIPPED_PUBLIC_GATEWAY",
            "COCKPIT_GATEWAY_MODE=local_gateway",
            "Unsupported Cockpit provider target host",
        ):
            self.assertIn(token, text)

    def test_cpa_throttle_retry_after_is_scoped_and_syntactically_valid(self) -> None:
        # A locally throttled client must get an explicit back-off signal, while
        # every other response (200/401/404 and pass-through upstream 429/5xx)
        # must keep its own headers untouched. The header is therefore gated by a
        # map over the throttle status variables rather than applied blindly.
        source = read_guardrail_source()
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

    def test_cpa_fail2ban_policy_has_versioned_source_and_is_projected(self) -> None:
        guardrails = read_guardrail_source()

        # Projection integrity only: the versioned filter/jail sources must be
        # carried to the host via their placeholders, or the remote jail keeps
        # running stale policy while the repo looks correct.
        self.assertIn("__CPA_FAIL2BAN_FILTER_B64__", guardrails)
        self.assertIn("__CPA_FAIL2BAN_JAIL_B64__", guardrails)

    def test_cpa_shared_account_admission_is_projected_and_route_scoped(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        guardrails = read_guardrail_source()
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
            ADMISSION_LANE_MODELS,
        )
        self.assertIn(
            "cpa-admission.py /opt/cliproxyapi/cpa-admission.json", admission_unit
        )


if __name__ == "__main__":
    unittest.main()
