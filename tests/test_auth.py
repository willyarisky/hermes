"""Unit tests for AGY Auth Adapter."""

import argparse
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import agy_auth_adapter
from agy_auth_adapter.auth import AGYAuthManager, AuthCredentials
from agy_auth_adapter.cli import build_parser, cmd_login, dispatch
from agy_auth_adapter.dashboard_auth import AntigravityDashboardAuthProvider
from agy_auth_adapter.oauth import create_auth_url, generate_pkce_pair
from agy_auth_adapter.provider import AGYModelProvider


class TestAGYAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hermes_home = Path(self.temp_dir.name) / ".hermes"
        self.gemini_home = Path(self.temp_dir.name) / ".gemini"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.gemini_home.mkdir(parents=True, exist_ok=True)
        self.auth_manager = AGYAuthManager(
            hermes_home=self.hermes_home,
            gemini_home=self.gemini_home,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pkce_generation(self):
        verifier, challenge = generate_pkce_pair()
        self.assertTrue(len(verifier) >= 43)
        self.assertTrue(len(challenge) > 0)
        self.assertNotIn("=", challenge)

    def test_auth_url_creation(self):
        with patch.dict(os.environ, {"AGY_OAUTH_CLIENT_ID": "test-client-id"}):
            url = create_auth_url(
                redirect_uri="http://localhost:8085/oauth/callback",
                state="test-state-123",
                code_challenge="test-challenge-abc",
            )
        self.assertIn("accounts.google.com/o/oauth2/v2/auth", url)
        self.assertIn("client_id=test-client-id", url)
        self.assertIn("state=test-state-123", url)
        self.assertIn("code_challenge=test-challenge-abc", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_missing_oauth_client_raises(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "agy_auth_adapter.oauth._load_client_config_file", return_value={}
        ):
            with self.assertRaises(RuntimeError) as ctx:
                create_auth_url(
                    redirect_uri="http://localhost:8085/oauth/callback",
                    state="s",
                    code_challenge="c",
                )
            self.assertIn("AGY_OAUTH_CLIENT_ID", str(ctx.exception))

    def test_unauthenticated_status(self):
        with patch.dict(os.environ, {}, clear=True), patch("agy_auth_adapter.auth.AGYAuthManager._load_from_keyring", return_value=None):
            mgr = AGYAuthManager(
                hermes_home=self.hermes_home,
                gemini_home=self.gemini_home,
            )
            status = mgr.get_status()
            self.assertFalse(status["authenticated"])
            self.assertIn("Not logged in", status["message"])

    def test_env_token_resolution(self):
        with patch.dict(os.environ, {"ANTIGRAVITY_TOKEN": "mock-env-token-123"}):
            mgr = AGYAuthManager(hermes_home=self.hermes_home)
            creds = mgr.get_credentials()
            self.assertIsNotNone(creds)
            self.assertEqual(creds.access_token, "mock-env-token-123")
            self.assertEqual(creds.source, "environment_variable")
            self.assertEqual(mgr.get_access_token(), "mock-env-token-123")

    def test_token_file_storage_and_loading(self):
        token_data = {
            "access_token": "mock-file-token-456",
            "refresh_token": "mock-refresh-token",
            "expires_at": 9999999999.0,
            "user_email": "tester@example.com",
            "user_name": "Tester",
        }
        token_file = self.hermes_home / ".antigravity_oauth.json"
        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        creds = self.auth_manager.get_credentials()
        self.assertIsNotNone(creds)
        self.assertEqual(creds.access_token, "mock-file-token-456")
        self.assertEqual(creds.user_email, "tester@example.com")
        self.assertFalse(creds.is_expired)

        status = self.auth_manager.get_status()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["user_email"], "tester@example.com")

    def test_logout_removes_token_file(self):
        token_file = self.hermes_home / ".antigravity_oauth.json"
        token_file.write_text('{"access_token": "abc"}', encoding="utf-8")
        self.assertTrue(token_file.exists())

        self.auth_manager.logout()
        self.assertFalse(token_file.exists())

    def test_export_and_import_token(self):
        token_file = self.hermes_home / ".antigravity_oauth.json"
        token_file.write_text(json.dumps({
            "access_token": "exported-access-token-999",
            "refresh_token": "exported-refresh-token",
            "user_email": "export@example.com",
            "expires_at": 9999999999.0,
        }), encoding="utf-8")

        exported_str = self.auth_manager.export_token()
        self.assertIn("exported-access-token-999", exported_str)
        self.assertIn("export@example.com", exported_str)

        # Clear and import into another manager instance
        target_dir = tempfile.TemporaryDirectory()
        target_home = Path(target_dir.name) / ".hermes"
        target_mgr = AGYAuthManager(hermes_home=target_home, gemini_home=self.gemini_home)

        imported_creds = target_mgr.import_token(exported_str, no_keychain=True)
        self.assertEqual(imported_creds.access_token, "exported-access-token-999")
        self.assertEqual(imported_creds.user_email, "export@example.com")
        self.assertTrue((target_home / ".antigravity_oauth.json").exists())
        target_dir.cleanup()


class TestTokenLogin(unittest.TestCase):
    """'hermes agy login --token' must work without any OAuth client configured."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hermes_home = Path(self.temp_dir.name) / ".hermes"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.env_patch = patch.dict(os.environ, {"HERMES_HOME": str(self.hermes_home)}, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _login(self, token):
        args = argparse.Namespace(token=token, no_keychain=True, port=8085, headless=False)
        return cmd_login(args)

    def test_login_with_raw_token(self):
        self.assertEqual(self._login("raw-token-abc"), 0)
        stored = json.loads((self.hermes_home / ".antigravity_oauth.json").read_text())
        self.assertEqual(stored["access_token"], "raw-token-abc")

    def test_login_with_exported_json(self):
        payload = json.dumps({"access_token": "json-token", "user_email": "user@example.com"})
        self.assertEqual(self._login(payload), 0)
        stored = json.loads((self.hermes_home / ".antigravity_oauth.json").read_text())
        self.assertEqual(stored["access_token"], "json-token")
        self.assertEqual(stored["user_email"], "user@example.com")

    def test_login_falls_back_to_environment_token(self):
        with patch.dict(os.environ, {"ANTIGRAVITY_TOKEN": "env-token"}):
            self.assertEqual(self._login(""), 0)
        stored = json.loads((self.hermes_home / ".antigravity_oauth.json").read_text())
        self.assertEqual(stored["access_token"], "env-token")

    def test_login_without_any_token_fails(self):
        blank_env = {"ANTIGRAVITY_TOKEN": "", "AGY_AUTH_TOKEN": "", "GEMINI_API_KEY": ""}
        with patch.dict(os.environ, blank_env), patch.object(sys.stdin, "isatty", return_value=True), patch(
            "agy_auth_adapter.cli.getpass.getpass", return_value=""
        ):
            self.assertEqual(self._login(""), 1)
        self.assertFalse((self.hermes_home / ".antigravity_oauth.json").exists())


class TestPluginRegistration(unittest.TestCase):
    """The plugin must match the Hermes PluginContext contract for 'hermes agy'."""

    class FakeContext:
        """Minimal stand-in for hermes_cli.plugins.PluginContext."""

        def __init__(self):
            self.cli_commands = {}
            self.dashboard_providers = []

        def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
            self.cli_commands[name] = {
                "help": help,
                "setup_fn": setup_fn,
                "handler_fn": handler_fn,
                "description": description,
            }

        def register_dashboard_auth_provider(self, provider):
            self.dashboard_providers.append(provider)

    def test_register_installs_agy_cli_command(self):
        ctx = self.FakeContext()
        agy_auth_adapter.register(ctx)
        self.assertIn("agy", ctx.cli_commands)
        self.assertEqual(len(ctx.dashboard_providers), 1)

    def test_registered_setup_fn_builds_a_working_subparser(self):
        """Mirrors what hermes_cli/main.py does with a plugin CLI command."""
        ctx = self.FakeContext()
        agy_auth_adapter.register(ctx)
        cmd = ctx.cli_commands["agy"]

        root = argparse.ArgumentParser(prog="hermes")
        subparsers = root.add_subparsers(dest="command")
        agy_parser = subparsers.add_parser("agy", help=cmd["help"])
        cmd["setup_fn"](agy_parser)
        agy_parser.set_defaults(func=cmd["handler_fn"])

        args = root.parse_args(["agy", "status"])
        self.assertEqual(args.command, "agy")
        self.assertEqual(args.agy_command, "status")

        status = MagicMock(return_value=0)
        with patch.dict("agy_auth_adapter.cli.COMMANDS", {"status": status}):
            self.assertEqual(args.func(args), 0)
        status.assert_called_once_with(args)

    def test_dispatch_without_subcommand_prints_help(self):
        parser = build_parser()
        parsed = parser.parse_args([])
        with patch.object(parsed._agy_parser, "print_help") as print_help:
            self.assertEqual(dispatch(parsed), 0)
        print_help.assert_called_once()


class TestExpiredCredentialHandling(unittest.TestCase):
    """A stale CLI token must be detected, not forwarded to Google as if fresh."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hermes_home = Path(self.temp_dir.name) / ".hermes"
        self.gemini_home = Path(self.temp_dir.name) / ".gemini"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.gemini_home.mkdir(parents=True, exist_ok=True)
        self.env = patch.dict(
            os.environ,
            {"ANTIGRAVITY_TOKEN": "", "AGY_AUTH_TOKEN": "", "GEMINI_API_KEY": ""},
        )
        self.env.start()
        self.mgr = AGYAuthManager(hermes_home=self.hermes_home, gemini_home=self.gemini_home)

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def _write_cli_creds(self, expiry_date_ms, refresh_token=None):
        payload = {"access_token": "ya29.cli-token", "expiry_date": expiry_date_ms}
        if refresh_token:
            payload["refresh_token"] = refresh_token
        (self.gemini_home / "oauth_creds.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_expiry_date_milliseconds_is_understood(self):
        self._write_cli_creds(int((time.time() + 1800) * 1000))
        with patch.object(self.mgr, "_load_from_keyring", return_value=None):
            creds = self.mgr.get_credentials()
        self.assertIsNotNone(creds)
        self.assertFalse(creds.is_expired)
        self.assertAlmostEqual(creds.expires_at, time.time() + 1800, delta=5)

    def test_expired_cli_token_is_not_used(self):
        self._write_cli_creds(int((time.time() - 3600) * 1000))
        with patch.object(self.mgr, "_load_from_keyring", return_value=None):
            self.assertIsNone(self.mgr.get_credentials())

    def test_status_explains_why_it_is_unauthenticated(self):
        self._write_cli_creds(int((time.time() - 3600) * 1000))
        with patch.object(self.mgr, "_load_from_keyring", return_value=None):
            status = self.mgr.get_status()
        self.assertFalse(status["authenticated"])
        self.assertIn("expired", status["message"])

    def test_expired_token_with_refresh_token_is_refreshed(self):
        self._write_cli_creds(int((time.time() - 3600) * 1000), refresh_token="1//refresh")
        fresh = AuthCredentials(
            access_token="ya29.fresh", expires_at=time.time() + 3600, source="hermes_oauth_file"
        )
        with patch.object(self.mgr, "_load_from_keyring", return_value=None), patch.object(
            self.mgr, "_refresh_and_save", return_value=fresh
        ) as refresh:
            creds = self.mgr.get_credentials()
        refresh.assert_called_once_with("1//refresh")
        self.assertEqual(creds.access_token, "ya29.fresh")


class TestDashboardAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hermes_home = Path(self.temp_dir.name) / ".hermes"
        self.auth_manager = AGYAuthManager(hermes_home=self.hermes_home)
        self.dashboard_provider = AntigravityDashboardAuthProvider(auth_manager=self.auth_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dashboard_provider_start_login(self):
        with patch.dict(os.environ, {"AGY_OAUTH_CLIENT_ID": "test-client-id"}):
            login_start = self.dashboard_provider.start_login(redirect_uri="http://localhost:3000/auth/callback")
        self.assertTrue(hasattr(login_start, "url"))
        self.assertIn("accounts.google.com", login_start.url)
        self.assertTrue(login_start.state)
        self.assertTrue(login_start.code_verifier)


class TestModelProvider(unittest.TestCase):
    def setUp(self):
        self.provider = AGYModelProvider()

    def test_message_formatting(self):
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Hello AGY!"},
            {"role": "assistant", "content": "Hello! How can I help you today?"},
        ]
        gemini_payload = self.provider.format_openai_to_gemini(messages=messages, temperature=0.7)
        self.assertIn("systemInstruction", gemini_payload)
        self.assertIn("contents", gemini_payload)
        self.assertEqual(len(gemini_payload["contents"]), 2)
        self.assertEqual(gemini_payload["contents"][0]["role"], "user")
        self.assertEqual(gemini_payload["contents"][1]["role"], "model")
        self.assertEqual(gemini_payload["generationConfig"]["temperature"], 0.7)

    def test_tool_declarations_formatting(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for location",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        }]
        payload = self.provider.format_openai_to_gemini(messages=[{"role": "user", "content": "weather"}], tools=tools)
        self.assertIn("tools", payload)
        self.assertEqual(payload["tools"][0]["functionDeclarations"][0]["name"], "get_weather")


if __name__ == "__main__":
    unittest.main()
