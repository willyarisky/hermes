"""CLI Command Handlers for Hermes Agent Antigravity (AGY) integration."""

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from agy_auth_adapter.auth import AGYAuthManager
from agy_auth_adapter.daemon import DaemonManager
from agy_auth_adapter.provider import DEFAULT_MODELS
from agy_auth_adapter.proxy import run_proxy_server
from agy_auth_adapter.utils import get_hermes_home, safe_read_json, safe_write_json, setup_logger

logger = logging.getLogger("agy_auth_adapter.cli")


def _resolve_login_token(raw: Optional[str]) -> str:
    """Resolves the token for 'login --token' from the flag, stdin, environment, or a prompt."""
    if raw == "-":
        return sys.stdin.read().strip()
    if raw:
        return raw.strip()

    env_token = (
        os.environ.get("ANTIGRAVITY_TOKEN")
        or os.environ.get("AGY_AUTH_TOKEN")
        or os.environ.get("GEMINI_API_KEY")
        or ""
    )
    if env_token:
        return env_token.strip()

    if not sys.stdin.isatty():
        return sys.stdin.read().strip()

    return getpass.getpass("Paste your Antigravity token (input hidden): ").strip()


def cmd_login(args: argparse.Namespace) -> int:
    """Handles 'hermes agy login'."""
    auth_mgr = AGYAuthManager()

    # Token login: no Google OAuth client needed, so this is the path to use on
    # servers and anywhere an Antigravity token is already available.
    if getattr(args, "token", None) is not None:
        token = _resolve_login_token(args.token)
        if not token:
            print(
                "\n[ERROR] No token supplied. Pass it directly (hermes agy login --token '<TOKEN>'), "
                "pipe it in (--token -), or set ANTIGRAVITY_TOKEN.",
                file=sys.stderr,
            )
            return 1
        try:
            creds = auth_mgr.import_token(token_payload=token, no_keychain=args.no_keychain)
            print(f"\n[SUCCESS] Logged into Google Antigravity with the supplied token!")
            if creds.user_email:
                print(f"  User:   {creds.user_email}")
            print(f"  Target: {auth_mgr.token_file}")
            return 0
        except Exception as e:
            print(f"\n[ERROR] Token login failed: {e}", file=sys.stderr)
            return 1

    try:
        creds = auth_mgr.login(
            no_keychain=args.no_keychain,
            port=args.port,
            headless=getattr(args, "headless", False) or getattr(args, "manual", False),
        )
        print(f"\n[SUCCESS] Successfully logged into Google Antigravity!")
        print(f"  User:   {creds.user_email or 'Antigravity User'}")
        print(f"  Source: {creds.source}")
        print(f"  Target: {auth_mgr.token_file}")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Login failed: {e}", file=sys.stderr)
        return 1


def cmd_export_token(args: argparse.Namespace) -> int:
    """Handles 'hermes agy export-token' for copying credentials to remote servers."""
    auth_mgr = AGYAuthManager()
    try:
        token_json = auth_mgr.export_token()
        print("\n--- AGY Exported Token (Copy to Remote Server) ---")
        print(token_json)
        print("--------------------------------------------------")
        print("\nTo import on another server, run:")
        print(f"  hermes agy import-token '{token_json}'\n")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to export token: {e}", file=sys.stderr)
        return 1


def cmd_import_token(args: argparse.Namespace) -> int:
    """Handles 'hermes agy import-token' on remote servers."""
    auth_mgr = AGYAuthManager()
    try:
        creds = auth_mgr.import_token(token_payload=args.token, no_keychain=args.no_keychain)
        print(f"\n[SUCCESS] Successfully imported Antigravity credentials!")
        if creds.user_email:
            print(f"  User:   {creds.user_email}")
        print(f"  Target: {auth_mgr.token_file}")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to import token: {e}", file=sys.stderr)
        return 1


def cmd_logout(args: argparse.Namespace) -> int:
    """Handles 'hermes agy logout'."""
    auth_mgr = AGYAuthManager()
    auth_mgr.logout()
    print("[SUCCESS] Successfully logged out of Google Antigravity.")
    print("Local profile credentials and token files have been cleared.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Handles 'hermes agy status'."""
    auth_mgr = AGYAuthManager()
    daemon_mgr = DaemonManager()

    status = auth_mgr.get_status()
    daemon_status = daemon_mgr.status()

    print("\n--- Antigravity (AGY) Authentication Status ---")
    if status["authenticated"]:
        print(f"Status:        AUTHENTICATED [Active]")
        print(f"User:          {status.get('user_email')}")
        if status.get("user_name"):
            print(f"Name:          {status.get('user_name')}")
        print(f"Auth Source:   {status.get('source')}")
        if status.get("token_file"):
            print(f"Token File:    {status.get('token_file')}")
        if status.get("expires_in_seconds") is not None:
            mins = status['expires_in_seconds'] // 60
            secs = status['expires_in_seconds'] % 60
            print(f"Token Expiry:  In {mins}m {secs}s (Auto-refreshes)")
    else:
        print(f"Status:        UNAUTHENTICATED")
        print(f"Message:       {status.get('message')}")
        print(f"Expected File: {status.get('token_file')}")
        print(f"\nTo authenticate, run: hermes agy login")

    print("\n--- AGY Background Daemon / Bridge Status ---")
    if daemon_status["running"]:
        print(f"Daemon:        RUNNING [PID: {daemon_status['pid']}]")
        print(f"Endpoint:      {daemon_status['endpoint']}")
        print(f"Log File:      {daemon_status['log_file']}")
    else:
        print(f"Daemon:        STOPPED")
        print(f"Endpoint:      {daemon_status['endpoint']} (Inactive)")
        print(f"To start daemon: hermes agy daemon start")
    print("-----------------------------------------------\n")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Handles 'hermes agy models'."""
    print("\nAvailable Antigravity (AGY) Models for Hermes Agent:")
    print("------------------------------------------------------")
    for alias, backend in DEFAULT_MODELS.items():
        print(f" - {alias:<40} -> {backend}")
    print("------------------------------------------------------\n")
    return 0


def cmd_proxy(args: argparse.Namespace) -> int:
    """Handles 'hermes agy proxy'."""
    if getattr(args, "daemon", False):
        daemon_mgr = DaemonManager()
        success, msg = daemon_mgr.start(host=args.host, port=args.port, verbose=args.verbose)
        if success:
            print(f"[SUCCESS] {msg}")
            return 0
        else:
            print(f"[ERROR] {msg}", file=sys.stderr)
            return 1

    setup_logger(verbose=args.verbose)
    run_proxy_server(host=args.host, port=args.port)
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Handles 'hermes agy daemon <action>'."""
    action = args.action
    daemon_mgr = DaemonManager()

    if action == "start":
        success, msg = daemon_mgr.start(host=args.host, port=args.port, verbose=args.verbose)
        if success:
            print(f"[SUCCESS] {msg}")
            return 0
        else:
            print(f"[ERROR] {msg}", file=sys.stderr)
            return 1

    elif action == "stop":
        success, msg = daemon_mgr.stop()
        if success:
            print(f"[SUCCESS] {msg}")
            return 0
        else:
            print(f"[ERROR] {msg}", file=sys.stderr)
            return 1

    elif action == "restart":
        success, msg = daemon_mgr.restart(host=args.host, port=args.port)
        if success:
            print(f"[SUCCESS] {msg}")
            return 0
        else:
            print(f"[ERROR] {msg}", file=sys.stderr)
            return 1

    elif action == "status":
        st = daemon_mgr.status(host=args.host, port=args.port)
        print("\n--- AGY Background Daemon Status ---")
        if st["running"]:
            print(f"Status:   RUNNING [PID: {st['pid']}]")
            print(f"Endpoint: {st['endpoint']}")
            print(f"Health:   HEALTHY")
            print(f"Logs:     {st['log_file']}")
        else:
            print(f"Status:   STOPPED")
            print(f"Endpoint: {st['endpoint']} (offline)")
        print("------------------------------------\n")
        return 0
    else:
        print(f"Unknown daemon action: {action}", file=sys.stderr)
        return 1


def cmd_setup(args: argparse.Namespace) -> int:
    """Automatically configures ~/.hermes/config.yaml for AGY."""
    hermes_home = get_hermes_home()
    config_yaml = hermes_home / "config.yaml"

    print(f"\nConfiguring Hermes configuration at: {config_yaml}")

    # Try importing PyYAML, fallback to line-based config update if missing
    try:
        import yaml
        cfg = {}
        if config_yaml.exists():
            with open(config_yaml, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

        # Set model & provider
        model_cfg = cfg.setdefault("model", {})
        model_cfg["provider"] = "agy-proxy"
        model_cfg["default"] = args.model

        # Configure custom provider
        providers_cfg = cfg.setdefault("providers", {})
        providers_cfg["agy-proxy"] = {
            "base_url": f"http://127.0.0.1:{args.port}/v1",
            "api_key": "antigravity-local-auth",
        }

        # Enable plugin
        plugins_cfg = cfg.setdefault("plugins", {})
        enabled_plugins = plugins_cfg.get("enabled", [])
        if "agy-auth-adapter" not in enabled_plugins:
            if isinstance(enabled_plugins, list):
                enabled_plugins.append("agy-auth-adapter")
            plugins_cfg["enabled"] = enabled_plugins

        with open(config_yaml, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)

        print("[SUCCESS] ~/.hermes/config.yaml updated with AGY provider settings!")
    except ImportError:
        # Fallback to simple snippet printing
        snippet = f"""
# Add this to your ~/.hermes/config.yaml:

model:
  provider: agy-proxy
  default: {args.model}

providers:
  agy-proxy:
    base_url: http://127.0.0.1:{args.port}/v1
    api_key: antigravity-local-auth

plugins:
  enabled:
    - agy-auth-adapter
"""
        print("[NOTE] PyYAML not installed. Please append the following snippet to ~/.hermes/config.yaml:")
        print(snippet)

    # Optionally auto-start background daemon if requested
    if args.start_daemon:
        daemon_mgr = DaemonManager()
        daemon_mgr.start(port=args.port)

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Builds CLI argument parser for AGY subcommands."""
    parser = argparse.ArgumentParser(
        prog="hermes agy",
        description="Google Antigravity (AGY) Auth Adapter & Provider CLI for Hermes Agent.",
    )
    subparsers = parser.add_subparsers(dest="command", help="AGY command to execute")

    # login
    login_parser = subparsers.add_parser(
        "login",
        help="Authenticate with Google Antigravity (token login, or browser OAuth PKCE)",
    )
    login_parser.add_argument(
        "--token",
        nargs="?",
        const="",
        metavar="TOKEN",
        help=(
            "Log in with an existing Antigravity token instead of the OAuth browser flow. "
            "Accepts a raw token or exported JSON; use '-' to read from stdin, or omit the "
            "value to take ANTIGRAVITY_TOKEN / prompt for it."
        ),
    )
    login_parser.add_argument("--no-keychain", action="store_true", help="Do not store credentials in system keyring")
    login_parser.add_argument("--port", type=int, default=8085, help="Callback HTTP server port (default: 8085)")
    login_parser.add_argument(
        "--headless",
        "--manual",
        action="store_true",
        dest="headless",
        help="Headless/remote server mode (prompts to paste OAuth code/URL manually)",
    )

    # export-token
    subparsers.add_parser("export-token", help="Export active credentials JSON for deployment to another server")

    # import-token
    import_parser = subparsers.add_parser("import-token", help="Import credentials JSON or token string onto server")
    import_parser.add_argument("token", type=str, help="Exported JSON token string or API key")
    import_parser.add_argument("--no-keychain", action="store_true", help="Do not store credentials in system keyring")

    # logout
    subparsers.add_parser("logout", help="Log out and clear stored AGY credentials")

    # status
    subparsers.add_parser("status", help="Show AGY authentication, token, and daemon status")

    # models
    subparsers.add_parser("models", help="List supported Antigravity models")

    # proxy
    proxy_parser = subparsers.add_parser("proxy", help="Run local OpenAI-compatible bridge proxy")
    proxy_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    proxy_parser.add_argument("--port", type=int, default=8080, help="Proxy port (default: 8080)")
    proxy_parser.add_argument("--daemon", "-d", action="store_true", help="Run proxy detached in background")
    proxy_parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")

    # daemon
    daemon_parser = subparsers.add_parser("daemon", help="Manage AGY background daemon process")
    daemon_parser.add_argument(
        "action",
        choices=["start", "stop", "restart", "status"],
        help="Action to perform on background daemon",
    )
    daemon_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    daemon_parser.add_argument("--port", type=int, default=8080, help="Proxy port (default: 8080)")
    daemon_parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")

    # setup
    setup_parser = subparsers.add_parser("setup", help="Auto-configure Hermes config.yaml for AGY")
    setup_parser.add_argument("--port", type=int, default=8080, help="Proxy bridge port (default: 8080)")
    setup_parser.add_argument(
        "--model",
        type=str,
        default="google-antigravity/gemini-3.7-flash",
        help="Default model (default: google-antigravity/gemini-3.7-flash)",
    )
    setup_parser.add_argument(
        "--start-daemon",
        "-d",
        action="store_true",
        help="Automatically launch background daemon after setup",
    )

    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 0

    commands = {
        "login": cmd_login,
        "export-token": cmd_export_token,
        "import-token": cmd_import_token,
        "logout": cmd_logout,
        "status": cmd_status,
        "models": cmd_models,
        "proxy": cmd_proxy,
        "daemon": cmd_daemon,
        "setup": cmd_setup,
    }

    handler = commands.get(parsed.command)
    if handler:
        return handler(parsed)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
