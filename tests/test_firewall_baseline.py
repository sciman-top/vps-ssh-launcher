"""Contract tests for the managed host firewall baseline."""

from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "firewall_baseline.ps1"
LIB = ROOT / "scripts" / "lib" / "proxy_firewall_contract.ps1"


def _stdout_section(stdout: str, name: str) -> list[str]:
    """Return the non-empty lines between <name>_BEGIN and <name>_END."""
    body = stdout.split(f"{name}_BEGIN", 1)[1].split(f"{name}_END", 1)[0]
    return [line for line in body.splitlines() if line.strip()]


class FirewallBaselineTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")
        self.lib = LIB.read_text(encoding="utf-8")

    def test_defaults_to_read_only_and_owns_only_its_table(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("[ \"$APPLY\" != '1' ]", text)
        self.assertIn("table inet vps_launcher_guard", self.lib)
        self.assertIn("systemctl enable --now", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertNotIn("ufw enable", text)
        self.assertNotIn("iptables -F", text)
        self.assertNotIn("nft flush ruleset", text)

    def test_contract_allows_only_known_public_services(self) -> None:
        lib = self.lib
        self.assertIn(
            "bwg = @(443, 2087, 8443, 10094, 21470, 28317, 29712, 33387)",
            lib,
        )
        self.assertIn(
            "zz  = @(443, 2087, 10094, 12378, 21470, 31854, 35622)",
            lib,
        )
        self.assertIn("bwg = @(443, 8444)", lib)
        self.assertIn("zz  = @(443, 8444)", lib)
        self.assertIn("policy drop;", lib)
        self.assertIn("ct state established,related accept", lib)
        self.assertIn('iif "lo" accept', lib)
        # CVE-2026-98432 mitigation must stay the first input rule so
        # options-bearing packets are dropped before any accept.
        self.assertIn("meta l4proto tcp ip hdrlength > 5 drop", lib)
        self.assertLess(
            lib.index("meta l4proto tcp ip hdrlength > 5 drop"),
            lib.index("ct state established,related accept"),
        )
        self.assertIn("Hysteria2HopStart = 20000", lib)
        self.assertIn("Hysteria2HopEnd = 20999", lib)
        self.assertIn("redirect to :__HOP_TARGET__", lib)
        self.assertIn(
            'Replace("__HOP_TARGET__", "$($contract.Hysteria2HopTarget)")', lib
        )
        self.assertIn("HYSTERIA2_HOP_PORTS=", self.text)

    def test_live_input_rules_must_match_the_exact_contract(self) -> None:
        source = (
            self.text.split("input_chain_state() {", 1)[1]
            .split("<<'PY_FIREWALL'\n", 1)[1]
            .split("\nPY_FIREWALL", 1)[0]
        )
        document = {
            "nftables": [
                {
                    "chain": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "name": "input",
                        "type": "filter",
                        "hook": "input",
                        "prio": -10,
                        "policy": "drop",
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {"meta": {"key": "l4proto"}},
                                    "right": "tcp",
                                }
                            },
                            {
                                "match": {
                                    "op": ">",
                                    "left": {
                                        "payload": {
                                            "protocol": "ip",
                                            "field": "hdrlength",
                                        }
                                    },
                                    "right": 5,
                                }
                            },
                            {"drop": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "in",
                                    "left": {"ct": {"key": "state"}},
                                    "right": ["established", "related"],
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "in",
                                    "left": {"ct": {"key": "state"}},
                                    "right": "invalid",
                                }
                            },
                            {"drop": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {"meta": {"key": "iif"}},
                                    "right": "lo",
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {"meta": {"key": "l4proto"}},
                                    "right": {"set": ["icmp", "ipv6-icmp"]},
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {
                                        "payload": {
                                            "protocol": "udp",
                                            "field": "sport",
                                        }
                                    },
                                    "right": 67,
                                }
                            },
                            {
                                "match": {
                                    "op": "==",
                                    "left": {
                                        "payload": {
                                            "protocol": "udp",
                                            "field": "dport",
                                        }
                                    },
                                    "right": 68,
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {
                                        "payload": {
                                            "protocol": "udp",
                                            "field": "sport",
                                        }
                                    },
                                    "right": 547,
                                }
                            },
                            {
                                "match": {
                                    "op": "==",
                                    "left": {
                                        "payload": {
                                            "protocol": "udp",
                                            "field": "dport",
                                        }
                                    },
                                    "right": 546,
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {
                                        "payload": {
                                            "protocol": "tcp",
                                            "field": "dport",
                                        }
                                    },
                                    "right": {"set": [443, 8443]},
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "vps_launcher_guard",
                        "chain": "input",
                        "expr": [
                            {
                                "match": {
                                    "op": "==",
                                    "left": {
                                        "payload": {
                                            "protocol": "udp",
                                            "field": "dport",
                                        }
                                    },
                                    "right": {"set": [443, 8444]},
                                }
                            },
                            {"accept": None},
                        ],
                    }
                },
            ]
        }

        def classify(candidate: dict) -> str:
            env = os.environ.copy()
            env["FIREWALL_NFT_JSON"] = json.dumps(candidate)
            result = subprocess.run(
                [sys.executable, "-c", source, "443 8443", "443 8444"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        extra_port = copy.deepcopy(document)
        extra_port["nftables"].insert(
            -1,
            {
                "rule": {
                    "family": "inet",
                    "table": "vps_launcher_guard",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "op": "==",
                                "left": {
                                    "payload": {
                                        "protocol": "tcp",
                                        "field": "dport",
                                    }
                                },
                                "right": 22,
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
        )
        self.assertEqual(classify(document), "exact")
        self.assertEqual(classify(extra_port), "live-rules")

    def test_apply_validates_backs_up_and_disarms_signals(self) -> None:
        text = self.text
        self.assertIn('nft -c -f "$tmp_rules"', text)
        self.assertIn("cp -a", text)
        self.assertIn("trap rollback ERR", text)
        self.assertIn("trap 'rollback 129' HUP", text)
        self.assertIn("trap - ERR HUP INT TERM", text)
        self.assertIn('[ -n "${tmp_rules:-}" ] && rm -f -- "$tmp_rules"', text)
        self.assertIn('[ -n "${tmp_unit:-}" ] && rm -f -- "$tmp_unit"', text)
        self.assertIn("systemctl daemon-reload", text)
        self.assertIn("systemctl is-enabled --quiet", text)
        self.assertIn("systemctl is-active --quiet", text)

    def test_rollback_failure_path_sandbox_simulation(self) -> None:
        # The rollback() body only executes when an apply fails mid-flight,
        # which must never be provoked against a live host. Run the payload
        # in a throwaway sandbox with stubbed nft/systemctl/id/flock where
        # `nft -c` always fails, forcing the rollback path: the scratch
        # rules/unit files must be reclaimed (a leftover unit file would sit
        # under the systemd unit directory forever), the backup kept for
        # forensics, and the status file must record the failed exit code.
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        payload = self.text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        rules_stub = "table inet vps_launcher_guard {}\n"
        payload = (
            payload.replace("__APPLY__", "1")
            .replace("__PROFILE__", "bwg")
            .replace(
                "__RULES_B64__",
                base64.b64encode(rules_stub.encode("utf-8")).decode("ascii"),
            )
            .replace("__RULES_SHA__", "0" * 64)
            .replace("__TCP_PORTS__", "443 8443")
            .replace("__UDP_PORTS__", "443 8444")
            .replace("__SSH_PORT__", "29712")
            .replace("__HOP_START__", "20000")
            .replace("__HOP_END__", "20999")
            .replace("__HOP_TARGET__", "8444")
        )
        sandbox = """set -u
SB="$(mktemp -d)"
mkdir -p "$SB/stubs" "$SB/rules" "$SB/unit" "$SB/backups" "$SB/status"
cat > "$SB/stubs/nft" <<'EOF'
#!/bin/bash
if [ "${1:-}" = "-c" ]; then exit 1; fi
if [ "${1:-}" = "list" ]; then exit 1; fi
exit 0
EOF
cat > "$SB/stubs/systemctl" <<'EOF'
#!/bin/bash
case "${1:-}" in
  is-active|is-enabled) exit 1 ;;
  *) exit 0 ;;
esac
EOF
printf '#!/bin/bash\\nprintf "0\\n"\\n' > "$SB/stubs/id"
printf '#!/bin/bash\\nexit 0\\n' > "$SB/stubs/flock"
chmod +x "$SB"/stubs/*
export PATH="$SB/stubs:$PATH"
export VPS_FIREWALL_RULES_DIR="$SB/rules"
export VPS_FIREWALL_UNIT_FILE="$SB/unit/vps-launcher-firewall.service"
export VPS_FIREWALL_BACKUP_DIR="$SB/backups"
export VPS_FIREWALL_STATUS_DIR="$SB/status"
export VPS_FIREWALL_LOCK_FILE="$SB/lock"
bash
echo "SIM_RC=$?"
echo "LEFTOVER_RULES=$(find "$SB/rules" -type f | wc -l)"
echo "LEFTOVER_UNIT=$(find "$SB/unit" -type f | wc -l)"
echo "BACKUPS_KEPT=$(find "$SB/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)"
echo "STATUS_CONTENT_BEGIN"
cat "$SB/status/firewall-baseline.status" 2>/dev/null || echo "STATUS_MISSING"
echo "STATUS_CONTENT_END"
exit 0
"""
        result = subprocess.run(
            self._bash_command(bash, "-c", sandbox),
            input=payload,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertIn("SIM_RC=5", result.stdout)
        self.assertIn("FIREWALL_NFT_CHECK_FAILED", result.stderr)
        self.assertIn("ROLLBACK_VERIFIED backup=", result.stdout)
        self.assertIn("LEFTOVER_RULES=0", result.stdout)
        self.assertIn("LEFTOVER_UNIT=0", result.stdout)
        self.assertIn("BACKUPS_KEPT=1", result.stdout)
        self.assertIn("result=failed", result.stdout)
        self.assertIn("exit_code=5", result.stdout)

    def test_rollback_midflight_restore_sandbox_simulation(self) -> None:
        # The first rollback sandbox forces `nft -c` to fail before any write,
        # so only the scratch-reclaim path runs. This sibling lets apply get
        # all the way past the atomic mv (the `-j list chain` probe fails so
        # the post-write verify trips) and proves the mid-flight restore: the
        # live table is reloaded from the backup dump, the pre-apply rules and
        # unit files are copied back verbatim, and the service is re-enabled
        # and started exactly as it was before the apply.
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        payload = self.text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        rules_stub = "table inet vps_launcher_guard {}\n"
        payload = (
            payload.replace("__APPLY__", "1")
            .replace("__PROFILE__", "bwg")
            .replace(
                "__RULES_B64__",
                base64.b64encode(rules_stub.encode("utf-8")).decode("ascii"),
            )
            .replace("__RULES_SHA__", "0" * 64)
            .replace("__TCP_PORTS__", "443 8443")
            .replace("__UDP_PORTS__", "443 8444")
            .replace("__SSH_PORT__", "29712")
            .replace("__HOP_START__", "20000")
            .replace("__HOP_END__", "20999")
            .replace("__HOP_TARGET__", "8444")
        )
        sandbox = """set -u
SB="$(mktemp -d)"
mkdir -p "$SB/stubs" "$SB/rules" "$SB/unit" "$SB/backups" "$SB/status"
export NFT_CALLS="$SB/stubs/nft.calls"
export SYSTEMCTL_CALLS="$SB/stubs/systemctl.calls"
: > "$NFT_CALLS"
: > "$SYSTEMCTL_CALLS"
cat > "$SB/stubs/nft" <<'EOF'
#!/bin/bash
printf '%s\\n' "$*" >> "$NFT_CALLS"
case "${1:-}" in
  -j) exit 1 ;;
  -c) exit 0 ;;
  list) echo 'table inet vps_launcher_guard {}'; exit 0 ;;
  *) exit 0 ;;
esac
EOF
cat > "$SB/stubs/systemctl" <<'EOF'
#!/bin/bash
printf '%s\\n' "$*" >> "$SYSTEMCTL_CALLS"
exit 0
EOF
printf '#!/bin/bash\\nprintf "0\\n"\\n' > "$SB/stubs/id"
printf '#!/bin/bash\\nexit 0\\n' > "$SB/stubs/flock"
printf '#!/bin/bash\\nexec "__PYEXE__" "$@"\\n' > "$SB/stubs/python3"
chmod +x "$SB"/stubs/*
printf 'table inet old_guard_before {}\\n' > "$SB/rules/firewall.nft"
printf '# pre-apply unit\\n' > "$SB/unit/vps-launcher-firewall.service"
export PATH="$SB/stubs:$PATH"
export VPS_FIREWALL_RULES_DIR="$SB/rules"
export VPS_FIREWALL_UNIT_FILE="$SB/unit/vps-launcher-firewall.service"
export VPS_FIREWALL_BACKUP_DIR="$SB/backups"
export VPS_FIREWALL_STATUS_DIR="$SB/status"
export VPS_FIREWALL_LOCK_FILE="$SB/lock"
bash
echo "SIM_RC=$?"
echo "RULES_RESTORED=$(cat "$SB/rules/firewall.nft")"
echo "UNIT_RESTORED=$(cat "$SB/unit/vps-launcher-firewall.service")"
echo "BACKUPS_KEPT=$(find "$SB/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)"
echo "NFT_CALLS_BEGIN"
cat "$NFT_CALLS"
echo "NFT_CALLS_END"
echo "SYSTEMCTL_CALLS_BEGIN"
cat "$SYSTEMCTL_CALLS"
echo "SYSTEMCTL_CALLS_END"
echo "STATUS_CONTENT_BEGIN"
cat "$SB/status/firewall-baseline.status" 2>/dev/null || echo "STATUS_MISSING"
echo "STATUS_CONTENT_END"
exit 0
""".replace("__PYEXE__", sys.executable.replace("\\", "/"))
        result = subprocess.run(
            self._bash_command(bash, "-c", sandbox),
            input=payload,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertIn("SIM_RC=6", result.stdout)
        self.assertIn(
            "FIREWALL_BASELINE_VERIFY_FAILED reason=contract_73", result.stderr
        )
        self.assertIn("ROLLBACK_VERIFIED backup=", result.stdout)
        self.assertIn("RULES_RESTORED=table inet old_guard_before {}", result.stdout)
        self.assertIn("UNIT_RESTORED=# pre-apply unit", result.stdout)
        self.assertIn("BACKUPS_KEPT=1", result.stdout)
        self.assertIn("result=failed", result.stdout)
        self.assertIn("exit_code=6", result.stdout)
        nft_calls = _stdout_section(result.stdout, "NFT_CALLS")
        # One backup dump feeds the restore; exactly two loads happen (apply,
        # then the rollback reload of the saved live table) plus the delete.
        self.assertIn("list table inet vps_launcher_guard", nft_calls)
        apply_calls = [
            c for c in nft_calls if c.startswith("-f ") and c.endswith("/firewall.nft")
        ]
        restore_calls = [
            c for c in nft_calls if c.startswith("-f ") and c.endswith("/live.nft")
        ]
        self.assertEqual(len(apply_calls), 1)
        self.assertEqual(len(restore_calls), 1)
        self.assertIn("delete table inet vps_launcher_guard", nft_calls)
        systemctl_calls = _stdout_section(result.stdout, "SYSTEMCTL_CALLS")
        self.assertEqual(systemctl_calls.count("daemon-reload"), 2)
        self.assertIn("enable --now vps-launcher-firewall.service", systemctl_calls)
        # The rollback must use the plain enable/start pair, not --now.
        self.assertIn("enable vps-launcher-firewall.service", systemctl_calls)
        self.assertIn("start vps-launcher-firewall.service", systemctl_calls)

    def test_embedded_bash_payload_is_syntax_valid(self) -> None:
        payload = self.text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        result = subprocess.run(
            self._bash_command(bash, "-n"),
            input=payload,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
