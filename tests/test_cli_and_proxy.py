"""Tests for CLI handlers and HTTP Proxy in AGY Auth Adapter."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy_auth_adapter.cli import build_parser, main as cli_main
from agy_auth_adapter.daemon import DaemonManager
from agy_auth_adapter.utils import (
    DEFAULT_PROXY_PORT,
    find_free_port,
    get_configured_proxy_port,
    get_default_proxy_port,
)
from agy_auth_adapter.auth import AGYAuthManager, AuthCredentials
from agy_auth_adapter.provider import AGYAPIError, AGYModelProvider, looks_like_api_key
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


class TestProxyPortResolution(unittest.TestCase):
    """The bridge port must not silently drift from what Hermes is configured to call."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hermes_home = Path(self.temp_dir.name) / ".hermes"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.env = patch.dict(
            os.environ, {"HERMES_HOME": str(self.hermes_home), "AGY_PROXY_PORT": ""}
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def _write_config(self, port):
        (self.hermes_home / "config.yaml").write_text(
            "providers:\n"
            "  agy-proxy:\n"
            f"    base_url: http://127.0.0.1:{port}/v1\n",
            encoding="utf-8",
        )

    def test_packaged_default_is_used_without_config(self):
        self.assertEqual(get_default_proxy_port(), DEFAULT_PROXY_PORT)
        self.assertNotEqual(DEFAULT_PROXY_PORT, 8080)

    def test_existing_config_port_wins_over_the_packaged_default(self):
        self._write_config(8080)
        self.assertEqual(get_configured_proxy_port(), 8080)
        self.assertEqual(get_default_proxy_port(), 8080)

    def test_environment_override_wins_over_config(self):
        self._write_config(8080)
        with patch.dict(os.environ, {"AGY_PROXY_PORT": "9100"}):
            self.assertEqual(get_default_proxy_port(), 9100)

    def test_invalid_environment_override_is_ignored(self):
        with patch.dict(os.environ, {"AGY_PROXY_PORT": "not-a-port"}):
            self.assertEqual(get_default_proxy_port(), DEFAULT_PROXY_PORT)

    def test_find_free_port_skips_listeners(self):
        def fake_connect(addr, timeout=None):
            if addr[1] < DEFAULT_PROXY_PORT + 2:
                return MagicMock()
            raise OSError
        with patch("socket.create_connection", side_effect=fake_connect):
            self.assertEqual(find_free_port(DEFAULT_PROXY_PORT), DEFAULT_PROXY_PORT + 2)


class TestCredentialRouting(unittest.TestCase):
    """An AI Studio API key must not be sent to cloudcode-pa as a Bearer token."""

    def _provider_with_token(self, token, source):
        mgr = MagicMock()
        mgr.get_credentials.return_value = AuthCredentials(access_token=token, source=source)
        mgr.get_auth_headers.return_value = {"Authorization": f"Bearer {token}"}
        return AGYModelProvider(auth_manager=mgr)

    def _captured_request(self, provider):
        with patch("agy_auth_adapter.provider.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__ = lambda s: s
            urlopen.return_value.read.return_value = json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
            ).encode()
            provider.chat_completion(
                model="google-antigravity/gemini-3.7-flash",
                messages=[{"role": "user", "content": "hi"}],
            )
            return urlopen.call_args[0][0]

    def test_api_key_from_token_file_goes_to_generativelanguage(self):
        provider = self._provider_with_token("AIzaSyFAKEKEYFAKEKEYFAKEKEY", "hermes_oauth_file")
        req = self._captured_request(provider)
        self.assertIn("generativelanguage.googleapis.com", req.full_url)
        self.assertIn("key=AIzaSyFAKEKEYFAKEKEYFAKEKEY", req.full_url)
        self.assertNotIn("Authorization", req.headers)

    def test_oauth_token_goes_to_cloudcode(self):
        provider = self._provider_with_token("ya29.a0-fake-oauth-token", "hermes_oauth_file")
        req = self._captured_request(provider)
        self.assertIn("cloudcode-pa.googleapis.com", req.full_url)

    def test_looks_like_api_key(self):
        self.assertTrue(looks_like_api_key("AIzaSyABC"))
        self.assertFalse(looks_like_api_key("ya29.abc"))
        self.assertFalse(looks_like_api_key(""))


class TestUpstreamErrorPassthrough(unittest.TestCase):
    """A rejected credential must surface as 401, not a generic 500."""

    def test_api_error_carries_status_code(self):
        err = AGYAPIError(401, '{"error": {"code": 401}}')
        self.assertEqual(err.status_code, 401)
        self.assertTrue(err.is_auth_error)
        self.assertFalse(AGYAPIError(500, "boom").is_auth_error)

    def test_provider_raises_api_error_with_upstream_status(self):
        mgr = MagicMock()
        mgr.get_credentials.return_value = AuthCredentials(
            access_token="ya29.stale", source="hermes_oauth_file"
        )
        mgr.get_auth_headers.return_value = {"Authorization": "Bearer ya29.stale"}
        provider = AGYModelProvider(auth_manager=mgr)

        http_error = urllib.error.HTTPError(
            url="https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": {"code": 401, "message": "Expected OAuth 2 access token"}}'),
        )
        with patch("agy_auth_adapter.provider.urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(AGYAPIError) as ctx:
                provider.chat_completion(
                    model="google-antigravity/gemini-3.7-flash",
                    messages=[{"role": "user", "content": "hi"}],
                )
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertTrue(ctx.exception.is_auth_error)


class TestCredentialVerification(unittest.TestCase):
    """'hermes agy status --verify' catches a bad token before a chat does."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mgr = AGYAuthManager(
            hermes_home=Path(self.temp_dir.name) / ".hermes",
            gemini_home=Path(self.temp_dir.name) / ".gemini",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_no_credential(self):
        with patch.object(self.mgr, "get_credentials", return_value=None):
            result = self.mgr.verify_credentials()
        self.assertFalse(result["ok"])

    def test_rejected_token_reports_the_status(self):
        creds = AuthCredentials(access_token="ya29.stale", source="hermes_oauth_file")
        http_error = urllib.error.HTTPError(
            url="https://www.googleapis.com/oauth2/v3/userinfo",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "invalid_request"}'),
        )
        with patch.object(self.mgr, "get_credentials", return_value=creds), patch(
            "urllib.request.urlopen", side_effect=http_error
        ):
            result = self.mgr.verify_credentials()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)

    def test_scope_error_is_inconclusive_not_a_failure(self):
        creds = AuthCredentials(access_token="ya29.scoped", source="hermes_oauth_file")
        http_error = urllib.error.HTTPError(
            url="https://www.googleapis.com/oauth2/v3/userinfo",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "insufficient scope"}'),
        )
        with patch.object(self.mgr, "get_credentials", return_value=creds), patch(
            "urllib.request.urlopen", side_effect=http_error
        ):
            result = self.mgr.verify_credentials()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 403)


if __name__ == "__main__":
    unittest.main()
