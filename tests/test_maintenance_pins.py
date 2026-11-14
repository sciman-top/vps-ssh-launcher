"""Refusal contract for the shared maintenance pin validators.

`pins.py` is the one gate every resource pin passes through: it decides which
Compose paths, service names and digests may reach a remote command builder.
Its rejections were reached only indirectly, so a loosened regex or a dropped
CPA-marker check would have shown up as a wider remote write, not a test.
"""

from __future__ import annotations

import unittest

from vps_ssh_launcher.maintenance import pins

DIGEST = "sha256:" + "a" * 64


class VersionPinTests(unittest.TestCase):
    def test_a_numeric_version_is_normalised_without_the_v_prefix(self) -> None:
        self.assertEqual(pins.normalize_version("v26.3.27"), "26.3.27")
        self.assertEqual(pins.normalize_version(" 26.3.27 "), "26.3.27")

    def test_non_string_and_non_numeric_versions_are_refused(self) -> None:
        for value in (26.3, None, "latest", "26.3", "v26.3.27-rc1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    pins.normalize_version(value)


class Sha256PinTests(unittest.TestCase):
    def test_a_prefixed_uppercase_digest_is_normalised(self) -> None:
        self.assertEqual(pins.normalize_sha256("sha256:" + "A" * 64), "a" * 64)

    def test_a_short_or_non_hex_digest_is_refused(self) -> None:
        for value in ("a" * 63, "g" * 64, 12, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "64 hex"):
                    pins.normalize_sha256(value)

    def test_a_docker_digest_requires_the_algorithm_prefix(self) -> None:
        self.assertEqual(pins.normalize_digest(DIGEST), DIGEST)
        for value in ("a" * 64, "sha512:" + "a" * 64, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "sha256:"):
                    pins.normalize_digest(value)


class ComposePathTests(unittest.TestCase):
    def test_an_absolute_non_cpa_path_is_accepted(self) -> None:
        self.assertEqual(
            pins.normalize_compose_file(" /srv/app/compose.yml "),
            "/srv/app/compose.yml",
        )

    def test_relative_nul_and_parent_segments_are_refused(self) -> None:
        for value in (
            "srv/app/compose.yml",
            "/srv/../etc/compose.yml",
            "/srv/app\x00/compose.yml",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "absolute"):
                    pins.normalize_compose_file(value)

    def test_a_path_targeting_the_cpa_container_is_refused(self) -> None:
        # The generic adapter must never be able to reconcile CPA: that file is
        # owned by the guardrail projection, not by a Compose plan.
        for value in (
            "/srv/cli-proxy-api/compose.yml",
            "/srv/CLIProxyAPI/compose.yml",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must not target CPA"):
                    pins.normalize_compose_file(value)


class ServicePinTests(unittest.TestCase):
    def test_services_are_sorted_and_deduplicated_by_rejection(self) -> None:
        self.assertEqual(pins.normalize_services(["web", "api"]), ("api", "web"))
        with self.assertRaisesRegex(ValueError, "duplicates"):
            pins.normalize_services(["api", "api"])

    def test_an_empty_or_non_list_service_set_is_refused(self) -> None:
        cases: list[object] = [[], None, "api"]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "non-empty array"):
                    pins.normalize_services(value)

    def test_a_service_name_that_is_not_a_simple_token_is_refused(self) -> None:
        cases: list[object] = [["api;rm -rf /"], ["-api"], ["api/../x"], [12]]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "simple allowlisted"):
                    pins.normalize_services(value)

    def test_a_cpa_service_name_is_refused(self) -> None:
        for value in (["cli-proxy-api"], ["CLIProxyAPI"]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "refuses CPA"):
                    pins.normalize_services(value)


class DigestTableTests(unittest.TestCase):
    def test_digests_must_cover_exactly_the_allowlisted_services(self) -> None:
        self.assertEqual(
            pins.normalize_digests({"api": DIGEST}, services=("api",)),
            {"api": DIGEST},
        )
        with self.assertRaisesRegex(ValueError, "exactly the allowlisted"):
            pins.normalize_digests({"api": DIGEST, "web": DIGEST}, services=("api",))
        with self.assertRaisesRegex(ValueError, "service-to-digest"):
            pins.normalize_digests(None, services=("api",))

    def test_each_digest_value_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "sha256:"):
            pins.normalize_digests({"api": "a" * 64}, services=("api",))


if __name__ == "__main__":
    unittest.main()
