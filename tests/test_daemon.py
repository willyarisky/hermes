"""Tests for AGY background daemon manager and lifecycle."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy_auth_adapter.cli import build_parser, main as cli_main
from agy_auth_adapter.daemon import DaemonManager


class TestDaemonManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hermes_home = Path(self.temp_dir.name) / ".hermes"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.daemon_mgr = DaemonManager(hermes_home=self.hermes_home, default_port=8089)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_daemon_initial_status(self):
        st = self.daemon_mgr.status(port=8089)
        self.assertFalse(st["running"])
        self.assertFalse(st["healthy"])
        self.assertIsNone(st["pid"])

    def test_pid_file_cleaning_when_stale(self):
        # Write a non-existent PID
        pid_file = self.hermes_home / "agy_proxy.pid"
        pid_file.write_text("999999", encoding="utf-8")
        self.assertEqual(self.daemon_mgr.get_running_pid(), None)
        self.assertFalse(pid_file.exists())

    @patch("urllib.request.urlopen")
    def test_healthcheck_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"service": "Hermes Antigravity Auth Adapter Proxy", "status": "healthy"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        self.assertTrue(self.daemon_mgr.is_healthy(port=8089))

    @patch("agy_auth_adapter.cli.DaemonManager")
    def test_cli_daemon_commands(self, mock_daemon_cls):
        mock_instance = MagicMock()
        mock_instance.start.return_value = (True, "Daemon started (PID: 12345)")
        mock_instance.stop.return_value = (True, "Daemon stopped")
        mock_instance.status.return_value = {
            "running": True,
            "pid": 12345,
            "endpoint": "http://127.0.0.1:8080/v1",
            "log_file": "log.txt",
        }
        mock_daemon_cls.return_value = mock_instance

        # Test daemon start
        exit_code = cli_main(["daemon", "start"])
        self.assertEqual(exit_code, 0)
        mock_instance.start.assert_called_once()

        # Test daemon stop
        exit_code = cli_main(["daemon", "stop"])
        self.assertEqual(exit_code, 0)
        mock_instance.stop.assert_called_once()

        # Test daemon status
        exit_code = cli_main(["daemon", "status"])
        self.assertEqual(exit_code, 0)
        mock_instance.status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
