"""Contract tests for the shared proxy TLS cipher surface.

The REALITY fallback target pins an explicit TLS 1.2 cipher list, while the
lane vhosts this repo projects used to inherit the nginx/OpenSSL default. The
result was two different TLS parameter sets on ports of the same hostname, which
one ClientHello per IP:port is enough to group. These tests pin the contract and
the four projectors that must consume it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "scripts" / "lib" / "proxy_tls_contract.ps1"
LANES = (
    "cdn_fallback_lane.ps1",
    "xhttp_nginx_front.ps1",
    "zz_xhttp_nginx_front.ps1",
    "reality_alt_sni_lane.ps1",
)


class ProxyTlsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_literal_is_the_single_source_of_truth(self) -> None:
        self.assertEqual(
            self.text.count("$ProxyTlsCipherSuite = "),
            1,
            "the literal must be declared once",
        )
        # Byte-identical to what vasma writes into subscribe.conf: the fallback
        # target and every lane port have to present the same TLS parameters.
        self.assertIn(
            "TLS13_AES_128_GCM_SHA256:TLS13_AES_256_GCM_SHA384:"
            "TLS13_CHACHA20_POLY1305_SHA256:"
            "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:"
            "ECDHE-ECDSA-CHACHA20-POLY1305",
            self.text,
        )

    def test_cbc_only_suites_are_not_reintroduced(self) -> None:
        # These are exactly the two suites the nginx default adds on top of the
        # fallback's list, i.e. the observable difference this contract removes.
        # Only the rendered literal matters; the comment above it names them on
        # purpose so the next reader knows what was dropped and why.
        match = re.search(r"\$ProxyTlsCipherSuite = '([^']+)'", self.text)
        self.assertIsNotNone(match)
        assert match is not None
        literal = match.group(1)
        self.assertNotIn("ECDHE-ECDSA-AES128-SHA256", literal)
        self.assertNotIn("ECDHE-ECDSA-AES256-SHA384", literal)
        self.assertNotIn("CBC", literal)

    def test_exposes_a_validating_accessor(self) -> None:
        self.assertIn("function Get-ProxyTlsCipherSuite", self.text)
        self.assertIn("-notmatch", self.text)
        self.assertIn("throw", self.text)


class LaneConsumesTlsContractTests(ScriptValidationMixin, unittest.TestCase):
    def test_every_lane_dot_sources_and_renders_the_contract(self) -> None:
        for name in LANES:
            with self.subTest(lane=name):
                text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn("lib\\proxy_tls_contract.ps1", text)
                self.assertIn("$tlsCiphers = Get-ProxyTlsCipherSuite", text)
                self.assertIn("ssl_ciphers __TLS_CIPHERS__;", text)
                self.assertIn('Replace("__TLS_CIPHERS__", $tlsCiphers)', text)
                # The bash heredoc must never carry the literal, or a lane that
                # forgets the replace would still render a stale list.
                self.assertNotIn("$ProxyTlsCipherSuite", text)

    def test_alt_sni_pins_both_vhosts(self) -> None:
        text = (ROOT / "scripts" / "reality_alt_sni_lane.ps1").read_text(
            encoding="utf-8"
        )
        # The fallback vhost and the subscription-port standby must agree, or
        # the recovery name keeps its own fingerprint. Each line appears twice:
        # once in the renderer and once in the idempotency check that refuses to
        # short-circuit a host whose vhost still lacks it.
        self.assertEqual(text.count("ssl_ciphers __TLS_CIPHERS__;"), 4)

    def test_drift_gate_audits_the_rendered_list(self) -> None:
        text = (ROOT / "scripts" / "proxy_hardening_drift.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("==tls-cipher-surface==", text)
        self.assertIn("tls_cipher_surface=drift", text)
        self.assertIn("tls_cipher_drift file=", text)
        self.assertIn("drift+=('nginx-stealth')", text)
        match = re.search(r"expected_ciphers='([^']+)'", text)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertNotIn("SHA256:ECDHE-ECDSA-AES128-SHA256", match.group(1))

    def test_loopback_checks_do_not_skip_certificate_verification(self) -> None:
        helper = CONTRACT.read_text(encoding="utf-8").split(
            "function Get-VerifiedLocalTlsCaPython", 1
        )[1]
        self.assertIn("verified_pinned_tls_curl_args", helper)
        self.assertIn("context.check_hostname = True", helper)
        self.assertIn("context.verify_mode = ssl.CERT_REQUIRED", helper)
        self.assertIn("VPS_SSH_LAUNCHER_TLS_CA_BUNDLE", helper)
        self.assertIn("not os.path.isabs(configured)", helper)
        for path in (
            "cdn_fallback_lane.ps1",
            "proxy_hardening_drift.ps1",
        ):
            text = (ROOT / "scripts" / path).read_text(encoding="utf-8")
            self.assertIn("lib\\proxy_tls_contract.ps1", text)
            self.assertIn("Get-VerifiedLocalTlsCaPython", text)
            self.assertIn("__VERIFIED_TLS_HELPER__", text)
        cdn = (ROOT / "scripts" / "cdn_fallback_lane.ps1").read_text(encoding="utf-8")
        decoy = cdn.split("decoys_ok() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("verified_pinned_tls_curl_args", decoy)
        self.assertNotIn("curl -sk", cdn)
        drift = (ROOT / "scripts" / "proxy_hardening_drift.ps1").read_text(
            encoding="utf-8"
        )
        stealth = drift.split("echo '==nginx-stealth=='", 1)[1].split(
            "echo '==xhttp-front=='", 1
        )[0]
        self.assertIn("verify_tls", stealth)
        self.assertNotIn("curl -sk", stealth)

    def _resolve_bash_openssl(self) -> tuple[Path, Path] | None:
        text = CONTRACT.read_text(encoding="utf-8")
        self.assertIn("return @'\n", text)
        git_root = Path(os.environ.get("ProgramFiles", ""), "Git")
        candidates: list[tuple[Path, Path]] = []
        if os.name != "nt":
            bash = Path(shutil.which("bash") or "")
            openssl = Path(shutil.which("openssl") or "")
            candidates.append((bash, openssl))
        else:
            windows_bash_candidates = [git_root / "bin" / "bash.exe"]
            system_bash = (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "bash.exe"
            )
            if system_bash.is_file():
                windows_bash_candidates.append(system_bash)
            launcher = shutil.which("bash")
            if launcher is not None:
                windows_bash_candidates.append(Path(launcher))
            for bash in windows_bash_candidates:
                if not bash.is_file():
                    continue
                located = subprocess.run(
                    [
                        str(bash),
                        "-c",
                        "command -v openssl >/dev/null 2>&1 || exit 1; "
                        "command -v cygpath >/dev/null 2>&1 || exit 1; "
                        'cygpath -w -- "$(command -v openssl)"',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if (
                    located.returncode == 0
                    and located.stdout.strip()
                    and Path(located.stdout.strip()).is_file()
                ):
                    candidates.append((bash, Path(located.stdout.strip())))
        bash_tool = next(
            (
                (bash, openssl)
                for bash, openssl in candidates
                if bash.is_file() and openssl.is_file()
            ),
            None,
        )
        if bash_tool is None:
            return None
        return bash_tool

    def test_private_tls_helper_rejects_missing_or_relative_trust(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        helper = text.split("return @'\n", 1)[1].split("\n'@", 1)[0]
        bash_tool = self._resolve_bash_openssl()
        if bash_tool is None:
            self.skipTest("Git Bash + OpenSSL toolchain is unavailable")
        _, openssl = bash_tool
        python = Path(sys.executable)
        # These tests exercise the Python helper directly, so use a real
        # Windows drive root rather than a POSIX virtual path. Git Bash and
        # Python can each mutate an inherited TMP/TEMP in their own startup.
        # The same test distribution can run this method concurrently. Give
        # each instance its own tree below a run directory. The C:\ bridge
        # name pattern ends with '-'; a generated child must have at least one
        # more character or the tree filter treats it as an unowned prefix.
        fixture_base = Path(
            tempfile.mkdtemp(
                prefix="cpa-tls-run-", dir=os.environ.get("SystemDrive", "C:") + "\\"
            )
        )
        fixture_root = fixture_base / "fixture"
        fixture_root.mkdir()

        self.addCleanup(shutil.rmtree, fixture_base, ignore_errors=True)
        with tempfile.TemporaryDirectory(dir=fixture_root) as directory:
            fixture = Path(directory)
            cert = fixture / "localhost.pem"
            generate = subprocess.run(
                [
                    str(openssl),
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost",
                    "-days",
                    "1",
                    "-keyout",
                    str(fixture / "localhost.key"),
                    "-out",
                    str(cert),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(generate.returncode, 0, generate.stderr)
            localhost_check = subprocess.run(
                [
                    str(openssl),
                    "x509",
                    "-in",
                    str(cert),
                    "-checkhost",
                    "localhost",
                    "-noout",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(localhost_check.returncode, 0, localhost_check.stderr)
            localhost_der = fixture / "localhost.der"
            localhost_der.write_bytes(
                subprocess.run(
                    [
                        str(openssl),
                        "x509",
                        "-in",
                        str(cert),
                        "-outform",
                        "DER",
                    ],
                    capture_output=True,
                    timeout=15,
                    check=True,
                ).stdout
            )
            fixture_helper = fixture / "verify_tls_fixture.py"
            fixture_helper.write_text(
                helper
                + "\nif __name__ == '__main__':\n"
                + "    print('\\0'.join(verified_tls_curl_args('localhost', "
                + repr(localhost_der.as_posix())
                + ")), end='')\n",
                encoding="utf-8",
            )
            for env, marker in (
                (
                    {
                        "VPS_LOCAL_TLS_VERIFY_DIR": fixture.as_posix(),
                        "VPS_SSH_LAUNCHER_TLS_CA_BUNDLE": (
                            os.environ.get("SYSTEMDRIVE", "C:") + "relative.pem"
                        ),
                    },
                    "not shell-safe",
                ),
                (
                    {
                        "VPS_LOCAL_TLS_VERIFY_DIR": fixture.as_posix(),
                        "VPS_SSH_LAUNCHER_TLS_CA_BUNDLE": (
                            (fixture / "missing.pem").as_posix()
                        ),
                    },
                    "unavailable",
                ),
            ):
                result = subprocess.run(
                    [str(python), str(fixture_helper)],
                    capture_output=True,
                    text=True,
                    env={**os.environ, **env},
                    timeout=15,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(marker, result.stderr)
            result = subprocess.run(
                [str(python), str(fixture_helper)],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "VPS_LOCAL_TLS_VERIFY_DIR": fixture.as_posix(),
                    "VPS_SSH_LAUNCHER_TLS_CA_BUNDLE": cert.as_posix(),
                    "PATH": os.pathsep.join(
                        [str(openssl.parent), os.environ.get("PATH", "")]
                    ),
                },
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("configured-trust.pem", result.stdout)
            self.assertNotIn(fixture.as_posix(), result.stdout)


if __name__ == "__main__":
    unittest.main()
