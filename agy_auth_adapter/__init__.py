"""Google Antigravity (AGY) Auth Adapter & Provider Plugin for Hermes Agent."""

import logging
from typing import Any

from agy_auth_adapter.auth import AGYAuthManager, AuthCredentials
from agy_auth_adapter.cli import (
    add_agy_subcommands,
    build_parser,
    dispatch as cli_dispatch,
    main as cli_main,
)
from agy_auth_adapter.daemon import DaemonManager
from agy_auth_adapter.dashboard_auth import AntigravityDashboardAuthProvider
from agy_auth_adapter.provider import AGYModelProvider

__version__ = "1.0.0"
__all__ = [
    "AGYAuthManager",
    "add_agy_subcommands",
    "cli_dispatch",
    "AuthCredentials",
    "DaemonManager",
    "AntigravityDashboardAuthProvider",
    "AGYModelProvider",
    "register",
]

logger = logging.getLogger("agy_auth_adapter")


def register(ctx: Any) -> None:
    """Plugin registration hook invoked by the Hermes PluginManager.

    Args:
        ctx: The Hermes PluginContext instance.
    """
    logger.info("Initializing Google Antigravity (AGY) Auth Adapter Plugin for Hermes...")
    auth_manager = AGYAuthManager()
    daemon_manager = DaemonManager()

    # 1. Register Dashboard Auth Provider (if dashboard auth system is present)
    if hasattr(ctx, "register_dashboard_auth_provider"):
        try:
            dashboard_provider = AntigravityDashboardAuthProvider(auth_manager=auth_manager)
            ctx.register_dashboard_auth_provider(dashboard_provider)
            logger.info("Registered Antigravity Dashboard Auth Provider ('agy').")
        except Exception as e:
            logger.warning(f"Could not register DashboardAuthProvider: {e}")

    # 2. Register the CLI command group ('hermes agy ...'). Hermes creates the
    #    subparser and calls setup_fn to fill it, then dispatches to handler_fn.
    if hasattr(ctx, "register_cli_command"):
        try:
            ctx.register_cli_command(
                name="agy",
                help="Google Antigravity (AGY) authentication, daemon, and proxy utilities",
                setup_fn=add_agy_subcommands,
                handler_fn=cli_dispatch,
                description=(
                    "Google Antigravity (AGY) authentication, background daemon, and "
                    "OpenAI-compatible proxy utilities."
                ),
            )
            logger.info("Registered 'hermes agy' CLI command group.")
        except TypeError:
            # Older/alternative PluginContext signature: (name, handler, help)
            try:
                ctx.register_cli_command(
                    name="agy",
                    handler=cli_main,
                    help="Google Antigravity (AGY) authentication, daemon, and proxy utilities",
                )
                logger.info("Registered 'hermes agy' CLI command group (legacy signature).")
            except Exception as e:
                logger.warning(f"Could not register CLI command: {e}")
        except Exception as e:
            logger.warning(f"Could not register CLI command: {e}")

    # 3. Register Custom Model Provider (if supported by Hermes runtime)
    if hasattr(ctx, "register_model_provider"):
        try:
            model_provider = AGYModelProvider(auth_manager=auth_manager)
            ctx.register_model_provider(
                name="antigravity",
                provider=model_provider,
            )
            logger.info("Registered 'antigravity' model provider.")
        except Exception as e:
            logger.warning(f"Could not register Model Provider: {e}")

    # 4. Auto-ensure background daemon is running if configured in Hermes
    if getattr(ctx, "config", None):
        cfg = getattr(ctx, "config", {})
        provider_name = cfg.get("model", {}).get("provider", "")
        if provider_name == "agy-proxy" or cfg.get("plugins", {}).get("agy", {}).get("auto_daemon", True):
            try:
                daemon_manager.ensure_running()
            except Exception as e:
                logger.debug(f"Auto-daemon background check: {e}")

    logger.info("AGY Auth Adapter plugin registered successfully.")
