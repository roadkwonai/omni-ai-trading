"""Offline checks for the broker environment boundary; never calls an API."""

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from nhplug_mcp import build_environment, main


class EnvironmentTests(unittest.TestCase):
    def test_modes_pin_hosts_and_isolate_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".envrc"
            path.write_text(
                "export APP_KEY='test-key'\nexport APP_SECRET='test-secret'\n"
                "export GITHUB_TOKEN='unrelated-secret'\n"
                "export ACCOUNT_NUMBER='live-account'\n"
            )
            inherited = {
                "PATH": "/usr/bin", "NHPLUG_BASE_URL": "https://evil.invalid",
                "NHPLUG_ENABLE_TRADING": "true", "NHPLUG_ALLOW_HOSTS": "evil.invalid",
                "NHPLUG_PAPER_ETF_STOP_PATH": "/tmp/untrusted/STOP",
            }
            for mode, host, trading in (
                ("readonly", "api", "false"), ("paper", "moapi", "true"),
            ):
                with self.subTest(mode=mode):
                    env = build_environment(path, mode, "selected-account", inherited)
                    self.assertEqual(env["NHPLUG_BASE_URL"], f"https://{host}.nhplug.com:8443")
                    self.assertEqual(env["NHPLUG_AUTH_URL"], "https://api.nhplug.com:8443")
                    self.assertEqual(env["NHPLUG_ENABLE_TRADING"], trading)
                    self.assertEqual(env["NHPLUG_APP_KEY"], "test-key")
                    self.assertEqual(env["NHPLUG_APP_SECRET"], "test-secret")
                    self.assertEqual(env["NHPLUG_DEFAULT_ACCOUNT"], "selected-account")
                    self.assertEqual(env["NHPLUG_ALLOW_HOSTS"], "")
                    self.assertNotIn("GITHUB_TOKEN", env)
                    self.assertNotIn("ACCOUNT_NUMBER", env)
                    self.assertNotIn("NHPLUG_PAPER_ETF_STOP_PATH", env)

    def test_missing_credentials_fail_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".envrc"
            path.write_text("APP_KEY='sensitive-value'\n")
            with self.assertRaisesRegex(ValueError, "APP_SECRET") as error:
                build_environment(path, "paper", "", {})
            self.assertNotIn("sensitive-value", str(error.exception))

    def test_unknown_mode_cannot_enable_trading(self):
        with self.assertRaises(ValueError):
            build_environment(Path("/unused"), "live", "", {})

    def test_guarded_paper_pins_separate_binary_and_stop_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            env_file = root / 'credentials'
            env_file.write_text("APP_KEY='test-key'\nAPP_SECRET='test-secret'\n")
            server = root / 'integrations/nhplug-mcp-etf7/dist/index.js'
            server.parent.mkdir(parents=True)
            server.touch()
            argv = ['nhplug_mcp.py', '--mode', 'paper', '--account', 'test-account',
                    '--env-file', str(env_file), '--guarded-paper']
            with patch('sys.argv', argv), patch.dict('os.environ', {'VIBE_TRADING_HOME': str(root)}), \
                    patch('nhplug_mcp.shutil.which', return_value='/node'), \
                    patch('nhplug_mcp.os.chdir') as chdir, patch('nhplug_mcp.os.execve') as execute:
                main()
            chdir.assert_called_once_with(server.parent.parent)
            self.assertEqual(execute.call_args.args[1], ['/node', str(server)])
            env = execute.call_args.args[2]
            self.assertEqual(env['NHPLUG_PAPER_ETF_STOP_PATH'], str(root / 'nhplug/shared-etf-paper/STOP'))
            self.assertEqual(env['NHPLUG_BASE_URL'], 'https://moapi.nhplug.com:8443')
            self.assertEqual(env['NHPLUG_ENABLE_TRADING'], 'true')

    def test_guarded_server_cannot_run_in_readonly_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.envrc'
            path.write_text("APP_KEY='test-key'\nAPP_SECRET='test-secret'\n")
            with self.assertRaisesRegex(ValueError, 'requires paper mode'):
                build_environment(path, 'readonly', 'test-account', {}, guarded_paper_root=root)


if __name__ == "__main__":
    unittest.main()
