"""Tests for CLI handlers and HTTP Proxy in AGY Auth Adapter."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy_auth_adapter.cli import build_parser, main as cli_main
from agy_auth_adapter.daemon import DaemonManager
from agy_auth_adapter.provider import AGYModelProvider
from agy_auth_adapter.proxy import AGYProxyHandler


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_parser_subcommands(self):
        args = self.parser.parse_args(["login", "--no-keychain", "--port", "9000"])
        self.assertEqual(args.agy_command, "login")
        self.assertTrue(args.no_keychain)
        self.assertEqual(args.port, 9000)

        args_status = self.parser.parse_args(["status"])
        self.assertEqual(args_status.agy_command, "status")

        args_models = self.parser.parse_args(["models"])
        self.assertEqual(args_models.agy_command, "models")

        args_proxy = self.parser.parse_args(["proxy", "--port", "8081"])
        self.assertEqual(args_proxy.agy_command, "proxy")
        self.assertEqual(args_proxy.port, 8081)

    @patch("agy_auth_adapter.cli.AGYAuthManager")
    def test_cli_status_command(self, mock_auth_cls):
        mock_mgr = MagicMock()
        mock_mgr.get_status.return_value = {
            "authenticated": True,
            "user_email": "user@google.com",
            "source": "hermes_oauth_file",
            "expires_in_seconds": 3500,
        }
        mock_auth_cls.return_value = mock_mgr
        exit_code = cli_main(["status"])
        self.assertEqual(exit_code, 0)

    @patch("agy_auth_adapter.cli.AGYAuthManager")
    def test_cli_logout_command(self, mock_auth_cls):
        mock_mgr = MagicMock()
        mock_auth_cls.return_value = mock_mgr
        exit_code = cli_main(["logout"])
        self.assertEqual(exit_code, 0)
        mock_mgr.logout.assert_called_once()


class TestStreamingAndTranslation(unittest.TestCase):
    def setUp(self):
        self.provider = AGYModelProvider()

    def test_stream_chunk_formatting(self):
        gemini_chunk = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Hello world from AGY"}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }]
        }
        chunk = self.provider._format_gemini_to_openai_chunk(
            gemini_chunk,
            completion_id="cmpl-123",
            created_at=123456,
            model="google-antigravity/gemini-3.7-flash",
        )
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk["object"], "chat.completion.chunk")
        self.assertEqual(chunk["choices"][0]["delta"]["content"], "Hello world from AGY")
        self.assertEqual(chunk["choices"][0]["finish_reason"], "stop")

    def test_function_call_response_formatting(self):
        gemini_resp = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "search_code",
                            "args": {"query": "auth adapter"},
                        }
                    }],
                    "role": "model",
                },
                "finishReason": "STOP",
            }]
        }
        res = self.provider._format_gemini_to_openai_response(
            gemini_resp,
            model="google-antigravity/gemini-3.7-flash",
        )
        self.assertEqual(res["object"], "chat.completion")
        self.assertEqual(res["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(len(res["choices"][0]["message"]["tool_calls"]), 1)
        self.assertEqual(res["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "search_code")


class TestPortConflictDetection(unittest.TestCase):
    """A foreign listener on the proxy port is the usual cause of HTML 404s."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mgr = DaemonManager(hermes_home=Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_probe_reports_free_when_nothing_listens(self):
        with patch("agy_auth_adapter.daemon.socket.create_connection", side_effect=OSError):
            self.assertEqual(self.mgr.probe_port(port=8080), "free")

    def test_probe_reports_agy_when_our_proxy_answers(self):
        with patch("agy_auth_adapter.daemon.socket.create_connection"), patch.object(
            self.mgr, "is_healthy", return_value=True
        ):
            self.assertEqual(self.mgr.probe_port(port=8080), "agy")

    def test_probe_reports_foreign_when_someone_else_answers(self):
        with patch("agy_auth_adapter.daemon.socket.create_connection"), patch.object(
            self.mgr, "is_healthy", return_value=False
        ):
            self.assertEqual(self.mgr.probe_port(port=8080), "foreign")

    def test_start_refuses_to_run_against_a_foreign_listener(self):
        with patch.object(self.mgr, "probe_port", return_value="foreign"), patch(
            "agy_auth_adapter.daemon.subprocess.Popen"
        ) as popen:
            ok, msg = self.mgr.start(port=8080)
        self.assertFalse(ok)
        self.assertIn("already in use", msg)
        popen.assert_not_called()

    def test_status_flags_the_conflict(self):
        with patch.object(self.mgr, "probe_port", return_value="foreign"):
            st = self.mgr.status(port=8080)
        self.assertTrue(st["port_conflict"])
        self.assertFalse(st["running"])


if __name__ == "__main__":
    unittest.main()
