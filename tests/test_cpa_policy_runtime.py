"""test_cpa_policy_runtime.py - split from test_scripts.py (domain: policy)."""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, cast
from cpa_catalog_expectations import (
    ADMISSION_LANE_MODELS,
    ANTIGRAVITY_OAUTH_ROUTE_ALIASES,
    OAUTH_ROUTE_ALIASES,
)

from script_validation_support import (
    CLAUDE_TEST_ROUTE_ALIASES,
    CODEX_TEST_ROUTE_ALIASES,
    CPA_TEST_PROVIDER_ALIASES,
    HEALTH_FIXTURE_CATALOG_IDS,
    ScriptValidationMixin,
    read_guardrail_source,
)


class CpaPolicyRuntimeTests(ScriptValidationMixin, unittest.TestCase):
    def test_retry_cap_is_bounded_but_not_zero(self) -> None:
        """A failed request may fail over, but may not walk the whole pool."""
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts" / "remote" / "cpa_policy.py"))
        retry_path = ("routing", "retry", "max-retry-credentials")
        self.assertEqual(policy["EXPECTED_CONFIG_PATHS"][retry_path], 1)
        self.assertEqual(
            policy["EXPECTED_CONFIG_PATHS"][("routing", "retry", "request-retry")],
            0,
        )
        apply_source = (
            repo_root / "scripts" / "remote" / "cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('retry_after["max-retry-credentials"] = 1', apply_source)

    def test_session_affinity_ttl_preserves_workday_cache_locality(self) -> None:
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts" / "remote" / "cpa_policy.py"))
        self.assertEqual(policy["EXPECTED_ROUTING"]["session-affinity-ttl"], "6h")
        self.assertEqual(
            policy["EXPECTED_ROUTING"]["session-affinity-rebind-on"], "quota"
        )
        apply_source = (
            repo_root / "scripts" / "remote" / "cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('"session-affinity-ttl": "6h"', apply_source)

    def test_apply_replaces_antigravity_exclusions_from_manifest(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        apply_source = (
            repo_root / "scripts" / "remote" / "cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        start = apply_source.index("antigravity_oauth_aliases = {")
        end = apply_source.index(
            'oauth_block_after["excluded-models"] = oauth_exclusions'
        )
        snippet = apply_source[start:end]
        manifest = json.loads(
            (repo_root / "scripts" / "remote" / "cpa_provider_routes.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            oauth_exclusions: dict[str, Any] = {
                "antigravity": [
                    *manifest["antigravity_oauth_exclusions"],
                    "claude-opus-5-5-high",
                ]
            }
            scope: dict[str, Any] = {
                "route_manifest": manifest,
                "oauth_exclusions": oauth_exclusions,
                "config_path": config_path,
            }
            exec(snippet, scope)
            self.assertEqual(
                oauth_exclusions["antigravity"],
                manifest["antigravity_oauth_exclusions"],
            )
            self.assertNotIn("claude-opus-5-5-high", oauth_exclusions["antigravity"])

            clashing_manifest = json.loads(json.dumps(manifest))
            clashing_manifest["antigravity_oauth_exclusions"].append(
                "claude-opus-5-5-high"
            )
            with self.assertRaises(SystemExit) as clash_exc:
                exec(
                    snippet,
                    {
                        "route_manifest": clashing_manifest,
                        "oauth_exclusions": {"antigravity": []},
                        "config_path": config_path,
                    },
                )
            self.assertIn(
                "REFUSE antigravity exclusion blocks a declared antigravity alias",
                str(clash_exc.exception),
            )

            (Path(directory) / "antigravity-quarantine.json").write_text(
                "{}", encoding="utf-8"
            )
            with self.assertRaises(SystemExit) as quarantine_exc:
                exec(
                    snippet,
                    {
                        "route_manifest": manifest,
                        "oauth_exclusions": {"antigravity": []},
                        "config_path": config_path,
                    },
                )
            self.assertIn("quarantine is active", str(quarantine_exc.exception))

    def test_thinking_levels_contract_is_declared_and_enforced(self) -> None:
        """An unsupported effort must be clamped, not forwarded for substitution.

        The antigravity lane is served over the codex (Responses) surface, whose
        built-in capability for these models is low/medium/high while the
        catalog publishes the gemini-4-*-xhigh slugs. The manifest declares the
        levels (provider hosts declare theirs on the model), the policy
        validates the declaration against the runtime vocabulary, and the
        runtime compares the projected model capabilities against it.
        """
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts" / "remote" / "cpa_policy.py"))
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        self.assertEqual(
            manifest["oauth_settings_contract"]["channels"]["antigravity"][
                "thinking-levels"
            ],
            ["low", "medium", "high"],
        )
        self.assertEqual(policy["_route_manifest_issues"](manifest), [])

        typo = json.loads(json.dumps(manifest))
        typo["oauth_settings_contract"]["channels"]["antigravity"][
            "thinking-levels"
        ].append("turbo")
        self.assertTrue(
            any(
                "unknown level" in issue
                for issue in policy["_route_manifest_issues"](typo)
            )
        )

        unserved = json.loads(json.dumps(manifest))
        unserved["oauth_settings_contract"]["channels"]["claude"] = {
            "thinking-levels": ["low"]
        }
        self.assertTrue(
            any(
                "does not serve" in issue
                for issue in policy["_route_manifest_issues"](unserved)
            )
        )

        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])
        glm_provider = next(
            provider
            for provider in config["api-keys"]["openai-compatibility"]
            if provider["base-url"].startswith("https://open.bigmodel.cn")
        )
        for model in glm_provider["models"]:
            del model["thinking"]
        self.assertTrue(
            any(
                "thinking.levels drift" in issue
                for issue in policy["validate_config"](config)
            )
        )

        apply_source = (
            repo_root / "scripts" / "remote" / "cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('entry["thinking"] = {"levels": thinking_levels}', apply_source)

    def test_safe_bootstrap_retry_is_pinned_once(self) -> None:
        """A dead pre-first-byte stream must be retried once, not by the client.

        Upstream defaults `requests.streaming.bootstrap-retries` to 0. Leaving it
        unpinned meant every pre-first-byte failure surfaced to the client, which
        then re-issued the same turn itself -- an extra upstream spend against a
        shared account for a turn the gateway could have replayed safely. The
        value stays 1: bootstrap retries stop as soon as a byte reaches the
        client, and `routing.retry.request-retry` stays 0, so this cannot turn
        into a retry loop.
        """
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts" / "remote" / "cpa_policy.py"))
        self.assertEqual(
            policy["EXPECTED_CONFIG_PATHS"][
                ("requests", "streaming", "bootstrap-retries")
            ],
            1,
        )

    def test_gemini_capacity_marker_is_lane_scoped(self) -> None:
        """RESOURCE_EXHAUSTED must cool the Antigravity lane, and only it.

        The string appears in no capacity_markers list (there is no "quota
        exceeded"/"rate limit" substring), so the classifier needed its own
        marker. Pinning it here keeps a future lane edit from dropping the only
        signal Google sends for a quota refusal.
        """
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        admission = runpy.run_path(
            str(repo_root / "scripts" / "remote" / "cpa-admission.py")
        )
        self.assertEqual(admission["GEMINI_CAPACITY_MARKERS"], ("resource_exhausted",))
        manifest = json.loads(
            (repo_root / "scripts" / "remote" / "cpa-admission.json").read_text(
                encoding="utf-8"
            )
        )
        lanes = {lane["name"]: lane for lane in manifest["lanes"]}
        pooled = lanes["workbuddy-oauth"].get("pooled_models", [])
        self.assertEqual(pooled, [])
        self.assertTrue(set(pooled) <= set(lanes["workbuddy-oauth"]["models"]))

    def test_sidecar_projection_checks_installed_app_before_mutation(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/cockpit_sidecar_guardrails.ps1"
        ).read_text(encoding="utf-8")
        project = source.split('if ($Mode -eq "Project") {', 1)[1]
        self.assertIn("Installed Cockpit version mismatch; no files changed", project)
        self.assertLess(
            project.index("Installed Cockpit version mismatch"),
            project.index("Write-JsonAtomic"),
        )
        self.assertIn("-not $installedAppVersion", project)
        # The generator fix ships in the official 1.3.66 build, so Project must
        # not sideload a replacement binary: the two persistent collection
        # files are the only writable surface left.
        self.assertNotIn("Copy-Item", project)

    def test_sidecar_policy_pins_the_generator_fix_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = json.loads(
            (repo_root / "scripts" / "cockpit_sidecar_policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(policy["cockpitVersion"], "1.3.67")
        self.assertEqual(policy["generatorFixedFrom"], "1.3.66")
        self.assertRegex(policy["sidecarSha256"], r"^[0-9A-F]{64}$")
        self.assertEqual(
            policy["expectedProviderManifest"],
            {"maxAccountConcurrency": 3, "accountConcurrencyWaitMs": 45000},
        )
        self.assertEqual(
            policy["persistentCollection"],
            {
                "maxAccountConcurrency": 3,
                "accountConcurrencyWaitMs": 45000,
                "maxRetryCredentials": 0,
                "maxRetryIntervalMs": 3000,
            },
        )
        self.assertEqual(
            policy["knownUpstreamDefaults"],
            {"request-retry": 1, "stream-bootstrap-buffering": True},
        )
        # The retired 1.3.65 sideload contract must not linger: a consumer that
        # still reads requiredListeners / sidecarConfig would silently accept
        # 10909+14185 as the definition of a healthy install.
        for retired in (
            "requiredListeners",
            "sidecarConfig",
            "knownGeneratedProviderManifest",
        ):
            self.assertNotIn(retired, policy)

    def test_sidecar_guardrails_verify_asserts_gate_fields_not_listeners(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "scripts" / "cockpit_sidecar_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        verify = source.split("$report = Get-Report", 1)[1]
        for token in (
            "INSTALLED_HASH",
            "INSTALLED_VERSION",
            "RUNNING_OFFICIAL_IMAGE",
            "HOST_LOADED=INFERRED_FROM_EXECUTABLE_PATH",
            "PERSISTENT_SETTINGS",
            "GATE_FIELDS",
            "COCKPIT_SIDECAR_VERIFY=PASS",
        ):
            self.assertIn(token, verify)
        # 10909/14185 were the 1.3.65 patch contract. Verify may report the
        # listeners but must not assert them again.
        self.assertNotIn("10909", verify)
        self.assertNotIn("14185", verify)

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
        duplicate_route_manifest["providers"][1]["models"][0]["alias"] = "gpt-6.2-astra"
        self.assertTrue(
            any(
                "assigned more than once" in issue
                for issue in policy["_route_manifest_issues"](duplicate_route_manifest)
            )
        )
        returned = json.loads(json.dumps(route_manifest))
        slot1 = next(
            provider for provider in returned["providers"] if provider["slot"] == 1
        )
        slot1["models"].append(
            {
                "name": "gpt-6.1-sol",
                "alias": "gpt-6.1-sol-input",
                "max-context-length": 500000,
            }
        )
        returned["oauth_model_aliases"] = {"gpt-6.1-sol-input": "gpt-6.1-sol"}
        self.assertTrue(
            any(
                "retired_models cannot return as provider aliases" in issue
                for issue in policy["_route_manifest_issues"](returned)
            )
        )
        for tombstone in ("oauth_exclusions", "codex_api_key_exclusions"):
            missing = json.loads(json.dumps(route_manifest))
            missing[tombstone].remove("gpt-6.1-sol-input")
            self.assertTrue(
                any(
                    "retired names" in issue
                    for issue in policy["_route_manifest_issues"](missing)
                )
            )
        duplicate_route_manifest = json.loads(json.dumps(route_manifest))
        duplicate_route_manifest["oauth_routes"][0]["models"][0]["alias"] = (
            "gpt-6.2-astra"
        )
        self.assertTrue(
            any(
                "assigned to multiple routes" in issue
                for issue in policy["_route_manifest_issues"](duplicate_route_manifest)
            )
        )
        config = self._valid_cpa_policy_config(policy)
        ai_input_index = next(
            index
            for index, provider in enumerate(route_manifest["providers"])
            if provider["host"] == "input.codes"
        )
        self.assertEqual(policy["validate_config"](config), [])
        self.assertEqual(policy["validate_config"](config), [])
        # The muse-spark managed route retired 2026-10-18 with its channel, so
        # the production manifest has no managed codex route. The managed-group
        # pin machinery stays covered via a synthetic expectation injected into
        # the policy module's live globals (runpy returns a snapshot copy, so
        # rebinding the returned dict would be invisible to validate_config),
        # decoupled from the production manifest.
        synthetic_route: dict[str, Any] = {
            "name": "zen-fixture",
            "base-url": "https://zen.fixture.example/v1",
            "headers": {"x-fixture-session": "cpa-bwg-gateway-01"},
            "models": [{"name": "fixture-model", "alias": "fixture-model"}],
        }
        policy["validate_config"].__globals__["EXPECTED_CODEX_ROUTE_GROUPS"] = {
            synthetic_route["base-url"]: synthetic_route
        }
        managed_route = synthetic_route
        config["api-keys"]["codex"] = [
            {
                "base-url": managed_route["base-url"],
                "keys": [{"api-key": "FIXTURE_CODEX_KEY"}],
                "headers": dict(managed_route["headers"]),
                "models": json.loads(json.dumps(managed_route["models"])),
                "excluded-models": list(route_manifest["codex_api_key_exclusions"]),
            }
        ]
        self.assertEqual(policy["validate_config"](config), [])
        config["api-keys"]["codex"][0]["excluded-models"].remove("gpt-6-luna")
        issues = policy["validate_config"](config)
        self.assertTrue(any("bare-route overlap" in issue for issue in issues))
        config["api-keys"]["codex"][0]["excluded-models"].append("gpt-6-luna")
        config["api-keys"]["codex"][0]["headers"] = {"x-other": "1"}
        issues = policy["validate_config"](config)
        self.assertTrue(any("headers transport override" in issue for issue in issues))
        config["api-keys"]["codex"][0]["headers"] = dict(managed_route["headers"])
        config["api-keys"].pop("codex")
        config["oauth"]["excluded-models"]["codex"] = [
            "codex-*",
            "gpt-5.7*",
            "gpt-6*",
        ]
        issues = policy["validate_config"](config)
        self.assertTrue(any("configured OAuth routes" in issue for issue in issues))
        config["oauth"]["excluded-models"]["codex"] = [
            "codex-*",
            "gpt-5.7*",
            "gpt-6.1-sol-input",
            "gpt-6-astra",
            "gpt-6-astra-ciii",
            "gpt-6.1-sol-ciii",
            "gpt-6.1-sol-91",
        ]
        ai_models = config["api-keys"]["openai-compatibility"][ai_input_index]["models"]
        ai_astra = next(
            model for model in ai_models if model.get("name") == "gpt-6.2-astra"
        )
        ai_models.remove(ai_astra)
        issues = policy["validate_config"](config)
        self.assertTrue(any("models=" in issue for issue in issues))
        ai_models.append(ai_astra)
        config["api-keys"]["openai-compatibility"][0]["request-retry"] = 1
        issues = policy["validate_config"](config)
        self.assertTrue(any("request-retry" in issue for issue in issues))
        config["api-keys"]["openai-compatibility"][0]["request-retry"] = 0
        config["quota-exceeded"]["switch-project"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("switch-project" in issue for issue in issues))
        config["quota-exceeded"]["switch-project"] = False
        config["routing"]["session-affinity-subagents"] = False
        issues = policy["validate_config"](config)
        self.assertTrue(any("session-affinity-subagents" in issue for issue in issues))
        config["routing"]["session-affinity-subagents"] = True
        config["routing"]["session-affinity-rebind-on"] = "any"
        issues = policy["validate_config"](config)
        self.assertTrue(any("session-affinity-rebind-on" in issue for issue in issues))
        config["routing"]["session-affinity-rebind-on"] = "quota"
        config["upstream"]["codex"]["stream-bootstrap-timeout"] = "30s"
        issues = policy["validate_config"](config)
        self.assertTrue(any("stream-bootstrap-timeout" in issue for issue in issues))
        config["upstream"]["codex"]["stream-bootstrap-timeout"] = "0"
        # Re-enabling the hold must be a policy violation, not a quiet
        # regression: it delays every response header until the upstream
        # generates its first token.
        config["upstream"]["codex"]["stream-bootstrap-buffering"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("stream-bootstrap-buffering" in issue for issue in issues))
        config["upstream"]["codex"]["stream-bootstrap-buffering"] = False
        config["api-keys"]["openai-compatibility"][ai_input_index]["disabled"] = True
        issues = policy["validate_config"](config)
        self.assertTrue(any("input.codes.disabled" in issue for issue in issues))
        config["api-keys"]["openai-compatibility"][ai_input_index].pop("disabled")
        config["api-keys"]["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://input.codes"
        )
        issues = policy["validate_config"](config)
        self.assertTrue(any("input.codes.base-url" in issue for issue in issues))
        config["api-keys"]["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://input.codes/v1"
        )
        config["api-keys"]["openai-compatibility"][ai_input_index]["base-url"] = (
            "http://35.213.82.91:8003/v1"
        )
        issues = policy["validate_config"](config)
        self.assertTrue(
            any("exactly one input.codes provider" in issue for issue in issues)
        )

        config["api-keys"]["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://input.codes/v1"
        )
        for invalid_url in (
            "http://input.codes/v1",
            "https://user:pass@input.codes/v1",
            "https://input.codes:8443/v1",
            "https://input.codes/v1?x=1",
            "https://input.codes/v2",
        ):
            config["api-keys"]["openai-compatibility"][ai_input_index]["base-url"] = (
                invalid_url
            )
            with self.subTest(invalid_url=invalid_url):
                self.assertTrue(
                    any(
                        "base-url must be exact" in issue
                        for issue in policy["validate_config"](config)
                    )
                )
        config["api-keys"]["openai-compatibility"][ai_input_index]["base-url"] = (
            "https://input.codes/v1"
        )
        config["api-keys"]["openai-compatibility"].append(
            {
                "name": "ai.input.im",
                "base-url": "https://ai.input.im/v1",
                "keys": [{"api-key": "test"}],
                "models": [],
            }
        )
        self.assertTrue(
            any(
                "retired provider ai.input.im must be removed" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["api-keys"]["openai-compatibility"].pop()
        config["api-keys"]["openai-compatibility"][ai_input_index]["keys"] = [
            {"api-key": "AI_TEST_KEY"},
            {"api-key": "AI_TEST_KEY_2"},
        ]
        self.assertTrue(
            any(
                "exactly one entry" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["api-keys"]["openai-compatibility"][ai_input_index]["keys"] = [
            {"api-key": "AI_TEST_KEY"}
        ]
        config["api-keys"]["openai-compatibility"][ai_input_index]["headers"] = {
            "X-Test": "blocked"
        }
        self.assertTrue(
            any(
                "headers transport override" in issue
                for issue in policy["validate_config"](config)
            )
        )

        config["api-keys"]["openai-compatibility"][ai_input_index].pop("headers")
        ai_models = config["api-keys"]["openai-compatibility"][ai_input_index]["models"]
        ai_astra = next(
            model for model in ai_models if model.get("name") == "gpt-6.2-astra"
        )
        ai_astra["name"] = "different-upstream-model"
        self.assertTrue(
            any("models=" in issue for issue in policy["validate_config"](config))
        )
        ai_astra["name"] = "gpt-6.2-astra"
        ai_astra["max-context-length"] = 500001
        self.assertTrue(
            any(
                "max-context-length" in issue
                for issue in policy["validate_config"](config)
            )
        )
        ai_astra["max-context-length"] = next(
            model["max-context-length"]
            for model in route_manifest["providers"][0]["models"]
            if model["name"] == "gpt-6.2-astra"
        )
        config["oauth"]["settings"]["codex"][0]["max-context-length"] = 300000
        self.assertTrue(
            any(
                "oauth.settings.codex context-window drift" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["oauth"]["settings"]["codex"] = json.loads(
            json.dumps(route_manifest["oauth_settings"]["codex"])
        )
        self.assertEqual(
            config["oauth"]["model-alias"]["workbuddy-ai-provider"],
            route_manifest["oauth_model_aliases"]["workbuddy-ai-provider"],
        )
        config["oauth"]["model-alias"]["workbuddy-ai-provider"][0]["fork"] = False
        self.assertTrue(
            any(
                "oauth.model-alias.workbuddy-ai-provider rename drift" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["oauth"]["model-alias"] = json.loads(
            json.dumps(route_manifest["oauth_model_aliases"])
        )
        config["oauth"]["model-alias"]["codex"] = [{"name": "stale", "alias": "old"}]
        self.assertTrue(
            any(
                "undeclared channel" in issue
                for issue in policy["validate_config"](config)
            )
        )
        config["oauth"]["model-alias"] = json.loads(
            json.dumps(route_manifest["oauth_model_aliases"])
        )
        config["api-keys"]["openai-compatibility"].append(
            {
                "name": "unexpected",
                "base-url": "https://example.invalid/v1",
                "keys": [{"api-key": "EXTRA_TEST_KEY"}],
                "models": [{"name": "other", "alias": "other"}],
            }
        )
        self.assertTrue(
            any(
                "unexpected openai-compatibility" in issue
                for issue in policy["validate_config"](config)
            )
        )

    def test_cpa_policy_pins_antigravity_sensitive_words(self) -> None:
        import json
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])
        # The list is the reviewed fingerprint-breaking set: shrinking,
        # growing, or replacing it silently re-opens the masked-429 content
        # filter (or obfuscates more than reviewed) on the antigravity lane.
        for bad in (
            [],
            ["gpt"],
            ["gpt", "openai", "codex", "claude"],
            ["openai", "gpt"],
            "gpt",
        ):
            mutated = json.loads(json.dumps(config))
            mutated["oauth"]["providers"]["antigravity"]["sensitive-words"] = bad
            issues = policy["validate_config"](mutated)
            self.assertTrue(
                any("sensitive-words" in issue for issue in issues),
                f"expected sensitive-words pin to reject {bad!r}",
            )

    def test_cpa_policy_pins_antigravity_sensitive_words_replace(self) -> None:
        import json
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])
        # The swap table is the effective masking surface since the 11-17 WAF
        # escalation: shrinking, growing, or retargeting it silently re-opens
        # the fingerprint 429 (or rewrites more than reviewed) on the
        # antigravity lane.
        for bad in (
            {},
            {"codex": "agentx"},
            {
                "codex": "helper",
                "openai": "vendor",
                "chatgpt": "chatservice",
                "gpt": "agentx",
            },
            {
                "codex": "",
                "openai": "vendor",
                "chatgpt": "chatservice",
                "gpt": "agentx",
            },
        ):
            mutated = json.loads(json.dumps(config))
            mutated["oauth"]["providers"]["antigravity"]["sensitive-words-replace"] = (
                bad
            )
            issues = policy["validate_config"](mutated)
            self.assertTrue(
                any("sensitive-words-replace" in issue for issue in issues),
                f"expected sensitive-words-replace pin to reject {bad!r}",
            )

    def test_cpa_policy_pins_antigravity_tos_pause_window(self) -> None:
        import json
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        # Absence is the CPA default, but the declared in-service posture is
        # explicit: dropping the key, or shortening/zeroing the pool pause,
        # silently removes the reviewed response to a pool-level TOS signal.
        for bad in (None, "0", "", "30m", 7200):
            mutated = json.loads(json.dumps(config))
            antigravity = mutated["oauth"]["providers"]["antigravity"]
            if bad is None:
                antigravity.pop("tos-pause-window", None)
            else:
                antigravity["tos-pause-window"] = bad
            issues = policy["validate_config"](mutated)
            self.assertTrue(
                any("tos-pause-window" in issue for issue in issues),
                f"expected tos-pause-window pin to reject {bad!r}",
            )

    def test_cpa_policy_pins_antigravity_per_credential_daily_cap(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        for bad in (None, 0, -1, 100, "150", True):
            mutated = json.loads(json.dumps(config))
            antigravity = mutated["oauth"]["providers"]["antigravity"]
            if bad is None:
                antigravity.pop("per-credential-daily-cap", None)
            else:
                antigravity["per-credential-daily-cap"] = bad
            issues = policy["validate_config"](mutated)
            self.assertTrue(
                any("per-credential-daily-cap" in issue for issue in issues),
                f"expected per-credential-daily-cap pin to reject {bad!r}",
            )

    def test_cpa_policy_pins_antigravity_connection_pool(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])

        mutations: list[tuple[str, Any]] = [
            ("missing", None),
            (
                "disabled",
                {
                    "enabled": False,
                    "idle-conn-timeout": "30s",
                    "max-idle-conns-per-host": 2,
                },
            ),
            (
                "int-bool",
                {
                    "enabled": 1,
                    "idle-conn-timeout": "30s",
                    "max-idle-conns-per-host": 2,
                },
            ),
            (
                "bad-timeout",
                {
                    "enabled": True,
                    "idle-conn-timeout": "0s",
                    "max-idle-conns-per-host": 2,
                },
            ),
            (
                "bad-max-idle",
                {
                    "enabled": True,
                    "idle-conn-timeout": "30s",
                    "max-idle-conns-per-host": -1,
                },
            ),
            (
                "bool-max-idle",
                {
                    "enabled": True,
                    "idle-conn-timeout": "30s",
                    "max-idle-conns-per-host": True,
                },
            ),
        ]
        for label, pool_value in mutations:
            with self.subTest(case=label):
                mutated = json.loads(json.dumps(config))
                antigravity = mutated["oauth"]["providers"]["antigravity"]
                if pool_value is None:
                    antigravity.pop("connection-pool", None)
                else:
                    antigravity["connection-pool"] = pool_value
                issues = policy["validate_config"](mutated)
                self.assertTrue(
                    any("connection-pool" in issue for issue in issues),
                    f"expected connection-pool pin to reject {label}: {issues}",
                )

    def test_cpa_policy_pins_oauth_request_scoped_errors_and_regex_semantics(
        self,
    ) -> None:
        import re
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])

        # Unrelated operator channels and empty optional `match` lists from
        # management-API reserialization must stay accepted.
        tolerated = json.loads(json.dumps(config))
        tolerated["oauth"]["request-scoped-errors"]["antigravity"][0]["match"] = []
        tolerated["oauth"]["request-scoped-errors"]["vertex"] = [
            {"status": 400, "match": ["maximum_context_length"], "action": "stop"}
        ]
        self.assertEqual(policy["validate_config"](tolerated), [])

        # Drift mutations on managed channels must fail closed.
        bad_cases: list[tuple[str, Any]] = [
            ("missing-root", None),
            ("non-mapping-root", []),
            (
                "missing-antigravity",
                {"codex": config["oauth"]["request-scoped-errors"]["codex"]},
            ),
            (
                "missing-codex",
                {
                    "antigravity": config["oauth"]["request-scoped-errors"][
                        "antigravity"
                    ]
                },
            ),
            (
                "wrong-action",
                {
                    **config["oauth"]["request-scoped-errors"],
                    "antigravity": [
                        {
                            **config["oauth"]["request-scoped-errors"]["antigravity"][
                                0
                            ],
                            "action": "continue",
                        }
                    ],
                },
            ),
            (
                "bool-status",
                {
                    **config["oauth"]["request-scoped-errors"],
                    "codex": [
                        {
                            **config["oauth"]["request-scoped-errors"]["codex"][0],
                            "status": True,
                        }
                    ],
                },
            ),
            (
                "unknown-field",
                {
                    **config["oauth"]["request-scoped-errors"],
                    "codex": [
                        {
                            **config["oauth"]["request-scoped-errors"]["codex"][0],
                            "extra": "unexpected",
                        }
                    ],
                },
            ),
        ]
        for label, value in bad_cases:
            with self.subTest(drift=label):
                mutated = json.loads(json.dumps(config))
                if value is None:
                    mutated["oauth"].pop("request-scoped-errors", None)
                else:
                    mutated["oauth"]["request-scoped-errors"] = value
                issues = policy["validate_config"](mutated)
                self.assertTrue(
                    any("request-scoped-errors" in issue for issue in issues),
                    f"expected request-scoped-errors pin to reject {label}: {issues}",
                )

        # Verify actual upstream body matching: the bare RESOURCE_EXHAUSTED
        # 429 shape runs stop-and-cooldown (2026-11-22 field evidence: genuine
        # per-model bucket exhaustion wears this shape and must cool the
        # (auth, model) state so rebind-on=quota can move the session), and
        # details-bearing real quota/rate-limit bodies are never intercepted.
        rules = policy["EXPECTED_OAUTH_REQUEST_SCOPED_ERRORS"]
        agy_pattern = re.compile(rules["antigravity"][0]["match-regexr"][0])
        for bare_waf_body in (
            '{"error":{"code":429,"message":"Resource has been exhausted (e.g. check quota).","status":"RESOURCE_EXHAUSTED"}}',
            '{\n  "error": {\n    "code": 429,\n    "message": "Resource has been exhausted (e.g. check quota).",\n    "status": "RESOURCE_EXHAUSTED"\n  }\n}\n',
            '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}',
        ):
            self.assertIsNotNone(agy_pattern.search(bare_waf_body), bare_waf_body)
        for real_quota_body in (
            '{"error":{"code":429,"message":"Rate limit exceeded","status":"RESOURCE_EXHAUSTED","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"RATE_LIMIT_EXCEEDED"}]}}',
            '{\n  "error": {\n    "code": 429,\n    "message": "You have exhausted your capacity on this model.",\n    "status": "RESOURCE_EXHAUSTED",\n    "details": [{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"QUOTA_EXHAUSTED"}]\n  }\n}\n',
            '{"error":{"status":"RESOURCE_EXHAUSTED","message":"QUOTA_EXHAUSTED"}}',
            "auth in short cooldown, 12s remaining",
        ):
            self.assertIsNone(agy_pattern.search(real_quota_body), real_quota_body)

        codex_pattern = re.compile(rules["codex"][0]["match-regexr"][0])
        for bare_codex_429 in (
            '{"detail":"Rate limit exceeded"}',
            ' { "detail" : "Rate limit exceeded" } \n',
        ):
            self.assertIsNotNone(codex_pattern.search(bare_codex_429), bare_codex_429)
        for real_codex_429 in (
            '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached","resets_in_seconds":3600}}',
            '{"detail":"Rate limit exceeded","resets_in_seconds":300}',
            '{"error":{"code":"turn_rate_limit_exceeded","message":"Model at capacity"}}',
        ):
            self.assertIsNone(codex_pattern.search(real_codex_429), real_codex_429)

        # Second Antigravity rule stops the #7748 TOS-pause 503 cascade without
        # intercepting ordinary upstream 503 overload bodies.
        tos_pause_rule = rules["antigravity"][1]
        self.assertEqual(tos_pause_rule["status"], 503)
        self.assertEqual(tos_pause_rule["action"], "stop")
        tos_pause_needle = tos_pause_rule["match"][0]
        self.assertIn(tos_pause_needle, "antigravity pool paused after TOS_VIOLATION")
        for ordinary_503 in (
            '{"error":{"code":503,"message":"The service is currently unavailable.","status":"UNAVAILABLE"}}',
            "upstream overloaded",
            "auth in short cooldown, 12s remaining",
        ):
            self.assertNotIn(tos_pause_needle, ordinary_503)

        dropped_tos_rule = json.loads(json.dumps(config))
        dropped_tos_rule["oauth"]["request-scoped-errors"]["antigravity"] = [
            rules["antigravity"][0]
        ]
        self.assertTrue(
            any(
                "request-scoped-errors" in issue
                for issue in policy["validate_config"](dropped_tos_rule)
            )
        )

    def test_cpa_policy_pins_auth_auto_refresh_workers_and_capacity_statuses(
        self,
    ) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])

        for bad_workers in (None, 0, 2, 16, True):
            with self.subTest(workers=bad_workers):
                mutated = json.loads(json.dumps(config))
                if bad_workers is None:
                    mutated["oauth"].pop("auth-auto-refresh-workers", None)
                else:
                    mutated["oauth"]["auth-auto-refresh-workers"] = bad_workers
                issues = policy["validate_config"](mutated)
                self.assertTrue(
                    any("auth-auto-refresh-workers" in issue for issue in issues),
                    f"expected auth-auto-refresh-workers pin to reject {bad_workers!r}: {issues}",
                )

        admission = json.loads(json.dumps(policy["ADMISSION_CONFIG"]))
        admission["lanes"][0]["capacity_statuses"] = [429]
        admission_issues = policy["_admission_config_issues"](
            policy["ROUTE_MANIFEST"], admission
        )
        self.assertTrue(
            any("429 and 503" in issue for issue in admission_issues),
            f"expected missing 503 in capacity_statuses to fail: {admission_issues}",
        )

    def test_cpa_policy_retires_opencode_slot_and_keeps_rse_rules_validated(
        self,
    ) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])

        # 2026-11-20: the user cancelled the OpenCode Go subscription and the
        # slot retired (opencode.ai sits in retired_hosts), so no manifest
        # provider carries pinned headers or request-scoped errors any more.
        self.assertFalse(
            [
                item
                for item in config["api-keys"]["openai-compatibility"]
                if item["name"] == "opencode"
            ]
        )

        # The request-scoped-errors normalization stays a generic manifest
        # rule; exercise it on a synthetic carrier the way the retired slot
        # carried its rules.
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        manifest["providers"].append(
            {
                "slot": 2,
                "name": "opencode",
                "host": "opencode.example.invalid",
                "path": "/v1",
                "models": [
                    {
                        "name": "carrier-model",
                        "alias": "carrier-model",
                        "max-context-length": 1000000,
                    }
                ],
                "request-scoped-errors": [
                    {"status": 403, "match": ["1010"], "action": "stop"}
                ],
            }
        )
        self.assertEqual(policy["_route_manifest_issues"](manifest), [])
        for label, rules in (
            ("wrong-action", [{"status": 403, "match": ["1010"], "action": "pause"}]),
            ("missing-matcher", [{"status": 403, "action": "stop"}]),
            ("bad-status", [{"status": "403", "match": ["1010"], "action": "stop"}]),
            ("empty-list", []),
        ):
            with self.subTest(case=label):
                mutated = json.loads(json.dumps(manifest))
                mutated["providers"][-1]["request-scoped-errors"] = rules
                issues = policy["_route_manifest_issues"](mutated)
                self.assertTrue(
                    any("request-scoped-errors" in issue for issue in issues),
                    issues,
                )

    def test_cpa_policy_rejects_oauth_setting_manifest_drift(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        manifest["oauth_settings"]["codex"][0]["max-context-length"] = 0
        issues = policy["_route_manifest_issues"](manifest)
        self.assertTrue(
            any(
                "max-context-length must be a positive integer" in issue
                for issue in issues
            ),
            issues,
        )
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        # The workbuddy-ai-provider settings channel is removed while the
        # INTL lane is closed dark (2026-11-17); the policy rule it exercised
        # (settings entries must reference declared models) is validated
        # through a fresh channel instead.
        manifest["oauth_settings"]["workbuddy-ai-provider"] = [
            {"name": "undeclared-model", "max-context-length": 1000}
        ]
        issues = policy["_route_manifest_issues"](manifest)
        self.assertTrue(
            any("names undeclared model" in issue for issue in issues),
            issues,
        )
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        manifest["oauth_model_aliases"]["workbuddy-ai-provider"][0]["name"] = (
            "deepseek-v4.1-flash-unknown"
        )
        issues = policy["_route_manifest_issues"](manifest)
        self.assertTrue(
            any("source is not an upstream model" in issue for issue in issues),
            issues,
        )
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        # 2026-11-17: a plugin-pin source may omit fork (replacement alias —
        # the interim hy4free rode on hy4flash-free until the same-day rename
        # retired it); only non-plugin channels still require fork=true for
        # independent admission state.
        manifest["oauth_model_aliases"]["zhipu-plan"] = [
            {"name": "glm-5.3", "alias": "glm-5.3-work"}
        ]
        issues = policy["_route_manifest_issues"](manifest)
        self.assertTrue(
            any("fork=true" in issue for issue in issues),
            issues,
        )

    def test_cpa_policy_pins_codex_payload_tool_rules(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])
        # With the last managed codex route retired (muse-spark, 2026-10-18)
        # the codex-derived override half is empty: no tool_search rewrite can
        # ride on a future route unless the manifest regains a managed route.
        # The only surviving override entries are the zhipu developer-role
        # rewrites (2026-11-03): zhipu rejects the desktop `developer` role
        # with raw 400 code 1214 and upstream #7202 sanctioned the
        # payload.override rewrite instead of a code fix. A dropped or
        # drifted rule must fail validation closed.
        self.assertFalse(
            any(
                "tool_search" in json.dumps(rule)
                for rule in policy["EXPECTED_PAYLOAD_TOOL_RULES"]["override"]
            )
        )
        manifest = json.loads(
            (
                Path(__file__).parents[1] / "scripts/remote/cpa_provider_routes.json"
            ).read_text(encoding="utf-8")
        )
        override_rules = policy["EXPECTED_PAYLOAD_TOOL_RULES"]["override"]
        self.assertTrue(override_rules)
        self.assertTrue(
            all(
                rule["params"] == {'messages.#(role=="developer")#.role': "system"}
                and all(
                    model.get("from-protocol") == "responses"
                    for model in rule["models"]
                )
                for rule in override_rules
            )
        )
        expected_override_names = sorted(
            set(manifest["zhipu_developer_role_override"]["models"])
            | set(manifest["workbuddy_developer_role_override"]["models"])
        )
        self.assertEqual(
            sorted(rule["models"][0]["name"] for rule in override_rules),
            expected_override_names,
        )
        # A config that lost the developer-role override (e.g. restored from
        # a pre-rules backup) must fail exact-match validation closed.
        trimmed = self._valid_cpa_policy_config(policy)
        trimmed["requests"]["payload"]["override"] = [
            rule
            for rule in trimmed["requests"]["payload"]["override"]
            if rule["models"][0]["name"]
            != manifest["zhipu_developer_role_override"]["models"][0]
        ]
        issues = policy["validate_config"](trimmed)
        self.assertTrue(any("requests.payload" in issue for issue in issues))
        serialized = json.dumps(policy["EXPECTED_PAYLOAD_TOOL_RULES"])
        self.assertNotIn("block_binding", serialized)
        self.assertNotIn("messages.-1", serialized)
        self.assertNotIn('"betas"', serialized)
        # Restoring an older config (e.g. from a pre-rules backup) drops the
        # section entirely; the gateway would then pass the raw desktop tool
        # declarations upstream again and desktop would regress to 400s.
        del config["requests"]["payload"]
        issues = policy["validate_config"](config)
        self.assertTrue(any("requests.payload" in issue for issue in issues))
        # A stale rule left behind from the retired route must also fail
        # closed: the expected shape is exactly {"override": [], "filter": []}.
        config["requests"]["payload"] = {
            "override": [
                {
                    "selector": 'tools.#(type=="tool_search")',
                    "params": {"required": ["query"]},
                }
            ],
            "filter": [],
        }
        issues = policy["validate_config"](config)
        self.assertTrue(any("requests.payload" in issue for issue in issues))

    def test_claude_oauth_filters_fallbacks_for_all_declared_models(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        rules = policy["EXPECTED_PAYLOAD_TOOL_RULES"]["filter"]
        actual = {
            model["name"]
            for rule in rules
            if rule["params"] == ["fallbacks"]
            for model in rule["models"]
            if model.get("protocol") == "claude"
        }
        # The claude-oauth lane retired 2026-10-20: no declared aliases, no
        # derived fallbacks rule. Exact-match validation must reject both a
        # resurrected rule and a missing-rule config for any future lane.
        self.assertEqual(actual, set(policy["EXPECTED_CLAUDE_ROUTE_ALIASES"]))
        self.assertEqual(actual, set())
        config = self._valid_cpa_policy_config(policy)
        self.assertTrue(
            all(
                rule.get("params") != ["fallbacks"]
                for rule in config["requests"]["payload"]["filter"]
            )
        )
        resurrected = dict(config)
        resurrected["requests"]["payload"] = {
            **config["requests"]["payload"],
            "filter": [
                *config["requests"]["payload"]["filter"],
                {
                    "models": [{"name": "claude-haiku-5-5", "protocol": "claude"}],
                    "params": ["fallbacks"],
                },
            ],
        }
        self.assertTrue(
            any(
                "requests.payload" in issue
                for issue in policy["validate_config"](resurrected)
            )
        )

    def test_antigravity_exclusion_drift_and_quarantine_are_provider_scoped(
        self,
    ) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        with tempfile.TemporaryDirectory() as directory:
            luna_marker = Path(directory) / "oauth-quarantine.json"
            ag_marker = Path(directory) / "antigravity-quarantine.json"

            def validate(candidate: dict[str, Any]) -> list[str]:
                return cast(
                    list[str],
                    policy["validate_config"](
                        candidate,
                        marker_path=luna_marker,
                        antigravity_marker_path=ag_marker,
                    ),
                )

            config = self._valid_cpa_policy_config(policy)
            self.assertEqual(validate(config), [])
            for bad_exclusions in ([], ["*"], None, [True]):
                with self.subTest(exclusions=bad_exclusions):
                    bad = json.loads(json.dumps(config))
                    bad["oauth"]["excluded-models"]["antigravity"] = bad_exclusions
                    self.assertTrue(validate(bad))

            previous = config["oauth"]["excluded-models"]["antigravity"]
            config["oauth"]["excluded-models"]["antigravity"] = [
                *previous,
                "gemini-3.8-flash-high",
            ]
            self.assertTrue(
                validate(config), "blocking an active model requires a marker"
            )
            # The marker must name every configured lane alias, and a
            # quarantined apply projects all of them into the exclusions.
            aliases = list(ANTIGRAVITY_OAUTH_ROUTE_ALIASES)
            config["oauth"]["excluded-models"]["antigravity"] = [
                *previous,
                *aliases,
            ]
            marker = {
                "version": 1,
                "state": "quarantined",
                "provider": "antigravity",
                "aliases": aliases,
                "previous_provider_exclusions": previous,
                "applied_provider_exclusions": config["oauth"]["excluded-models"][
                    "antigravity"
                ],
            }
            ag_marker.write_text(json.dumps(marker), encoding="utf-8")
            self.assertEqual(validate(config), [])
            marker["provider"] = "codex"
            ag_marker.write_text(json.dumps(marker), encoding="utf-8")
            self.assertTrue(
                any("provider mismatch" in issue for issue in validate(config))
            )
            marker["provider"] = "antigravity"
            ag_marker.write_text(json.dumps(marker), encoding="utf-8")
            # One account's stop must not authorize blocking its sibling.
            config["oauth"]["excluded-models"]["codex"].append("gpt-6-luna")
            self.assertTrue(
                any("configured OAuth" in issue for issue in validate(config))
            )

    def test_antigravity_static_upstream_models_must_be_routed_or_excluded(
        self,
    ) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        manifest["antigravity_oauth_exclusions"].remove("gemini-3-flash")
        issues = policy["_route_manifest_issues"](manifest)
        self.assertTrue(
            any(
                "uncovered Antigravity upstream static models" in issue
                for issue in issues
            ),
            issues,
        )
        manifest = json.loads(json.dumps(policy["ROUTE_MANIFEST"]))
        manifest["antigravity_oauth_exclusions"].append("gemini-3.8-flash-high")
        issues = policy["_route_manifest_issues"](manifest)
        self.assertTrue(
            any("cannot be both routed and excluded" in issue for issue in issues),
            issues,
        )

    def test_cpa_policy_requires_claude_subscription_oauth_fail_close(self) -> None:
        import runpy

        repo_root = Path(__file__).parents[1]
        policy = runpy.run_path(str(repo_root / "scripts/remote/cpa_policy.py"))
        config = self._valid_cpa_policy_config(policy)
        # Dropping any tombstone widens the lane beyond the three claimed
        # aliases and must fail the exact-list comparison.
        config["oauth"]["excluded-models"]["claude"].remove("claude-opus-4-6")
        issues = policy["validate_config"](config)
        self.assertTrue(
            any("oauth.excluded-models.claude" in issue for issue in issues),
            issues,
        )
        apply_source = (repo_root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'oauth_exclusions["claude"] = list(manifest_claude_exclusions)',
            apply_source,
        )

    def test_cpa_policy_requires_stabilized_claude_device_profile(self) -> None:
        import runpy

        repo_root = Path(__file__).parents[1]
        policy = runpy.run_path(str(repo_root / "scripts/remote/cpa_policy.py"))
        config = self._valid_cpa_policy_config(policy)
        header_defaults = config["upstream"]["claude"]["header-defaults"]
        self.assertIs(header_defaults["stabilize-device-profile"], True)
        self.assertEqual(policy["validate_config"](config), [])
        header_defaults["stabilize-device-profile"] = False
        issues = policy["validate_config"](config)
        self.assertTrue(
            any("stabilize-device-profile" in issue for issue in issues),
            issues,
        )
        apply_source = (repo_root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'claude_header_defaults_after["stabilize-device-profile"] = True',
            apply_source,
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
            config["oauth"]["excluded-models"]["codex"] = [
                *config["oauth"]["excluded-models"]["codex"],
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
                        for pattern in config["oauth"]["excluded-models"]["codex"]
                        if pattern not in oauth_aliases
                    ],
                    "applied_codex_exclusions": list(
                        config["oauth"]["excluded-models"]["codex"]
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
                        for pattern in config["oauth"]["excluded-models"]["codex"]
                        if pattern not in oauth_aliases
                    ],
                    "applied_codex_exclusions": list(
                        config["oauth"]["excluded-models"]["codex"]
                    ),
                }
            )
            partial = json.loads(json.dumps(config))
            partial["oauth"]["excluded-models"]["codex"] = [
                pattern
                for pattern in partial["oauth"]["excluded-models"]["codex"]
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
            "model": "glm-5.3-flash",
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

    def test_cpa_cache_canary_covers_both_downstream_protocols(self) -> None:
        import runpy

        script = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )
        cache_canary = script["cache_canary"]
        format_metrics = script["_format_cache_metrics"]
        catalog = {"data": [{"id": model} for model in HEALTH_FIXTURE_CATALOG_IDS]}
        # The responses protocol reports cache reads under
        # usage.input_tokens_details.cached_tokens, not prompt_tokens_details.
        responses_first = {
            "model": "glm-5.3-flash",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "OK"}],
                }
            ],
            "usage": {
                "input_tokens": 1500,
                "input_tokens_details": {"cached_tokens": 0},
            },
        }
        responses_second = {
            **responses_first,
            "usage": {
                "input_tokens": 1500,
                "input_tokens_details": {"cached_tokens": 1400},
            },
        }
        request = mock.Mock(side_effect=[catalog, responses_first, responses_second])
        result, metrics = cache_canary({}, request, mock.Mock(), protocol="responses")
        self.assertEqual(result, 0)
        self.assertEqual(metrics[0]["cache_read_tokens"], 0)
        self.assertEqual(metrics[1]["cache_read_tokens"], 1400)
        rendered = format_metrics(2, metrics[1], protocol="responses")
        self.assertIn("protocol=responses", rendered)
        self.assertIn("hit_ratio=0.9333", rendered)
        paths = [call.args[0] for call in request.call_args_list[1:]]
        self.assertEqual(paths, ["responses", "responses"])
        bodies = [call.args[1] for call in request.call_args_list[1:]]
        self.assertEqual(bodies[0]["input"][0]["type"], "message")
        self.assertEqual(bodies[0]["max_output_tokens"], 1024)
        # The responses protocol must not invent the chat-only session field.
        self.assertNotIn("session_id", bodies[0])

        # An unsupported protocol is refused locally, before any request.
        denied = mock.Mock()
        with self.assertRaises(ValueError):
            cache_canary({}, denied, mock.Mock(), protocol="completions")
        denied.assert_not_called()

        # A status that is not completed is a contract failure, not a sample.
        incomplete = mock.Mock(
            side_effect=[catalog, {**responses_first, "status": "in_progress"}]
        )
        self.assertEqual(
            cache_canary({}, incomplete, mock.Mock(), protocol="responses")[0], 20
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
                if model in CODEX_TEST_ROUTE_ALIASES:
                    # Codex-family routes answer over the Responses API.
                    if "expected" in case:
                        output: list[dict[str, Any]] = [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": json.dumps(case["expected"]),
                                    }
                                ],
                            }
                        ]
                    else:
                        output = [
                            {
                                "type": "function_call",
                                "name": case["expected_tool"],
                                "arguments": json.dumps(case["expected_arguments"]),
                            }
                        ]
                    responses.append(
                        {"model": model, "status": "completed", "output": output}
                    )
                elif model in CLAUDE_TEST_ROUTE_ALIASES:
                    # Claude-family routes answer over Anthropic Messages.
                    if "expected" in case:
                        responses.append(
                            {
                                "model": model,
                                "stop_reason": "end_turn",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(case["expected"]),
                                    }
                                ],
                            }
                        )
                    else:
                        responses.append(
                            {
                                "model": model,
                                "stop_reason": "tool_use",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": case["expected_tool"],
                                        "input": case["expected_arguments"],
                                    }
                                ],
                            }
                        )
                elif "expected" in case:
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
            elif body["model"] in CODEX_TEST_ROUTE_ALIASES:
                # Responses API flattens the selection to the function name.
                self.assertEqual(
                    body["tool_choice"],
                    {
                        "type": "function",
                        "name": tool_case["tool_choice"]["function"]["name"],
                    },
                )
            elif body["model"] in CLAUDE_TEST_ROUTE_ALIASES:
                # Anthropic Messages selects the tool by name directly.
                self.assertEqual(
                    body["tool_choice"],
                    {
                        "type": "tool",
                        "name": tool_case["tool_choice"]["function"]["name"],
                    },
                )
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
            "COCKPIT_GATEWAY_MODE=other_provider",
            "COCKPIT_SIDECAR_VERIFY=SKIPPED_OTHER_PROVIDER",
            "Unsupported Cockpit provider target host",
        ):
            self.assertIn(token, text)
        # A third-party provider is a recorded mode, not a failure; the guard
        # must therefore sit on the hostless target, not on "anything that is
        # not fq or loopback".
        self.assertLess(
            text.index("[string]::IsNullOrWhiteSpace($targetUri.Host)"),
            text.index("Unsupported Cockpit provider target host"),
        )
        self.assertLess(
            text.index("Unsupported Cockpit provider target host"),
            text.index("COCKPIT_GATEWAY_MODE=other_provider"),
        )

    def test_cpa_recovery_restart_and_recover_mode_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "cpa_recovery_workflow.ps1").read_text(
            encoding="utf-8"
        )
        # RestartAndRecover clears the CPA in-process cooldown with a container
        # restart (save-cooldown-status is false, so nothing is persisted) and
        # then reuses the RecoverAfterReset probe for the admission lane.
        for token in (
            '"RecoverAfterReset", "RestartAndRecover"',
            "$Mode requires exactly one external recovery confirmation",
            "RESTART_RESULT=OK",
            "RESTART_RESULT=LANE_BUSY",
            "CPA_RESTART_STAGE=COMPLETE",
        ):
            self.assertIn(token, text)
        # The lane-busy preflight must gate the restart: a restart while any
        # chatgpt-oauth request is in flight would cut that request mid-stream.
        self.assertLess(
            text.index("RESTART_RESULT=LANE_BUSY"),
            text.index("docker restart cli-proxy-api"),
        )
        # The probe stage runs only after the restart stage reports complete.
        self.assertLess(
            text.index("CPA_RESTART_STAGE=COMPLETE"),
            text.index("RECOVERY_MODEL__"),
        )

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
            "ExecStart=/opt/cliproxyapi/cpa-admission-launch.sh", admission_unit
        )
        self.assertIn(
            "/opt/cliproxyapi/cpa-admission.py",
            (repo_root / "scripts/remote/cpa-admission-launch.sh").read_text(
                encoding="utf-8"
            ),
        )
        self.assertIn(
            "/opt/cliproxyapi/cpa-admission.json",
            (repo_root / "scripts/remote/cpa-admission-launch.sh").read_text(
                encoding="utf-8"
            ),
        )

    def test_tls_handshake_timeout_and_connection_pool_are_pinned(self) -> None:
        """uTLS handshake timeout and openai-compat connection-pool must be enforced."""
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts/remote/cpa_policy.py"))
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])

        config["requests"]["tls-handshake-timeout-seconds"] = 0
        issues = policy["validate_config"](config)
        self.assertTrue(
            any("tls-handshake-timeout-seconds" in issue for issue in issues), issues
        )
        config["requests"]["tls-handshake-timeout-seconds"] = 15

        compat_entry = config["api-keys"]["openai-compatibility"][0]
        compat_entry.pop("connection-pool", None)
        issues = policy["validate_config"](config)
        self.assertTrue(any("connection-pool" in issue for issue in issues), issues)

        compat_entry["connection-pool"] = {"enabled": False}
        issues = policy["validate_config"](config)
        self.assertTrue(any("connection-pool" in issue for issue in issues), issues)

        compat_entry["connection-pool"] = {}
        self.assertEqual(policy["validate_config"](config), [])

    def test_shutdown_timeout_and_stream_probe_bytes_are_pinned(self) -> None:
        """server.shutdown-timeout and admission stream_probe_bytes must be validated."""
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts/remote/cpa_policy.py"))
        config = self._valid_cpa_policy_config(policy)
        self.assertEqual(policy["validate_config"](config), [])

        config["server"]["shutdown-timeout"] = 0
        issues = policy["validate_config"](config)
        self.assertTrue(any("shutdown-timeout" in issue for issue in issues), issues)
        config["server"]["shutdown-timeout"] = 15
        self.assertEqual(policy["validate_config"](config), [])

        admission = json.loads(
            (repo_root / "scripts/remote/cpa-admission.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(admission.get("stream_probe_bytes"), 2097152)
        self.assertEqual(
            policy["_admission_config_issues"](policy["ROUTE_MANIFEST"], admission),
            [],
        )

        bad_admission = dict(admission, stream_probe_bytes=1024)
        bad_issues = policy["_admission_config_issues"](
            policy["ROUTE_MANIFEST"], bad_admission
        )
        self.assertTrue(
            any("stream_probe_bytes" in issue for issue in bad_issues), bad_issues
        )

    def test_antigravity_natural_prompts_are_not_rewritten(self) -> None:
        """Natural user text must remain verbatim to preserve cache prefixes."""
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        policy = runpy.run_path(str(repo_root / "scripts/remote/cpa_policy.py"))
        manifest = policy["ROUTE_MANIFEST"]
        self.assertNotIn("antigravity_fingerprint_swap", manifest)
        self.assertEqual(policy["EXPECTED_PAYLOAD_TOOL_RULES"]["replace"], [])


class CpaPolicyProbeTests(unittest.TestCase):
    """The on-demand probe must work without any external linter installed.

    The host has neither pip nor ruff (verified 2026-11-08), so a probe that
    depended on ruff could only report that it did not run. These contracts pin
    the standard-library checks that replaced it.
    """

    @staticmethod
    def _policy() -> dict[str, Any]:
        import runpy

        repo_root = Path(__file__).resolve().parents[1]
        return runpy.run_path(str(repo_root / "scripts" / "remote" / "cpa_policy.py"))

    def test_probe_rules_come_from_the_route_manifest(self) -> None:
        policy = self._policy()
        rules = policy["_probe_lint_rules"]()
        self.assertTrue(rules)
        self.assertTrue(all(isinstance(rule, str) and rule for rule in rules))

    def test_probe_rejects_a_module_that_lost_a_required_helper(self) -> None:
        issues = self._policy()["lint_source"](
            "def validate_config():\n    pass\n", ["F"]
        )
        self.assertTrue(issues)
        self.assertIn("missing helper", issues[0])
        # A helper the doctor calls is part of the contract, not an optional.
        self.assertIn("load_quarantine_aliases", issues[0])

    def test_probe_accepts_a_complete_module(self) -> None:
        policy = self._policy()
        source = "\n".join(
            ['"""doc"""', "import ast"]
            + [
                f"def {name}():\n    pass"
                for name in sorted(policy["_PROBE_REQUIRED_HELPERS"])
            ]
        )
        self.assertEqual(policy["lint_source"](source, ["F"]), [])

    def test_probe_reports_a_syntax_error_instead_of_passing(self) -> None:
        issues = self._policy()["lint_source"]("def broken(:\n", ["F"])
        self.assertTrue(issues)
        self.assertIn("syntax error", issues[0])

    def test_probe_refuses_to_run_without_declared_rules(self) -> None:
        issues = self._policy()["lint_source"]("x = 1\n", [])
        self.assertTrue(issues)
        self.assertIn("probe_lint_rules", issues[0])


if __name__ == "__main__":
    unittest.main()
