import argparse
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import ssh_tool


RUN_ENV = "VPS_SSH_LAUNCHER_RUN_INTEGRATION"
CONFIG_ENV = "VPS_SSH_LAUNCHER_INTEGRATION_CONFIG"
PROFILE_ENV = "VPS_SSH_LAUNCHER_INTEGRATION_PROFILE"
COMMAND_ENV = "VPS_SSH_LAUNCHER_INTEGRATION_COMMAND"
EXPECTED_ENV = "VPS_SSH_LAUNCHER_INTEGRATION_EXPECTED"

DEFAULT_COMMAND = "printf vps-ssh-launcher-integration"
DEFAULT_EXPECTED = "vps-ssh-launcher-integration"


def _default_integration_config() -> str:
    config_path = ssh_tool.resolve_default_config_path(Path(__file__).resolve().parent)
    return str(config_path) if config_path is not None else "target.json"


class IntegrationConfigResolutionTests(unittest.TestCase):
    def test_default_integration_config_reuses_launcher_resolution(self) -> None:
        with patch.object(
            ssh_tool,
            "resolve_default_config_path",
            return_value=Path("resolved-target.json"),
        ):
            self.assertEqual(_default_integration_config(), "resolved-target.json")

    def test_default_integration_config_falls_back_to_legacy_target_json(self) -> None:
        with patch.object(ssh_tool, "resolve_default_config_path", return_value=None):
            self.assertEqual(_default_integration_config(), "target.json")


@unittest.skipUnless(
    os.environ.get(RUN_ENV) == "1",
    f"Set {RUN_ENV}=1 to run the real SSH integration test.",
)
class RealSSHIntegrationTests(unittest.TestCase):
    def test_real_ssh_command_round_trip(self) -> None:
        args = argparse.Namespace(
            config=os.environ.get(CONFIG_ENV) or _default_integration_config(),
            profile=os.environ.get(PROFILE_ENV),
            host=None,
            port=None,
            user=None,
            password=None,
            key=None,
            allow_agent=os.environ.get("VPS_SSH_LAUNCHER_INTEGRATION_ALLOW_AGENT")
            == "1",
            strict_host_key_checking=os.environ.get(
                "VPS_SSH_LAUNCHER_INTEGRATION_STRICT_HOST_KEY_CHECKING"
            )
            == "1",
        )
        command = os.environ.get(COMMAND_ENV, DEFAULT_COMMAND)
        expected = os.environ.get(EXPECTED_ENV, DEFAULT_EXPECTED)

        ssh_tool.apply_config(args)
        client = ssh_tool.connect_with_retry(args)
        try:
            code, stdout, stderr = ssh_tool.exec_remote(client, command)
        finally:
            client.close()

        self.assertEqual(code, 0, stderr)
        self.assertIn(expected, stdout)


if __name__ == "__main__":
    unittest.main()
