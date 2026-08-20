"""Dashboard Authentication Provider for Hermes Agent Web Dashboard."""

import logging
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agy_auth_adapter.auth import AGYAuthManager
from agy_auth_adapter.oauth import (
    create_auth_url,
    exchange_code_for_tokens,
    fetch_user_info,
    refresh_access_token,
)
from agy_auth_adapter.utils import generate_pkce_pair

logger = logging.getLogger("agy_auth_adapter.dashboard")

# Gracefully import Base DashboardAuthProvider from hermes_cli if installed, or define fallback protocol
try:
    from hermes_cli.dashboard_auth import DashboardAuthProvider, LoginStart, Session
except ImportError:
    @dataclass
    class LoginStart:
        url: str
        state: str
        code_verifier: Optional[str] = None

    @dataclass
    class Session:
        user_id: str
        email: str
        access_token: str
        refresh_token: Optional[str] = None
        expires_at: Optional[float] = None
        user_name: Optional[str] = None

    class DashboardAuthProvider:
        """Abstract base class fallback for Hermes Dashboard Auth."""
        name: str = "base"
        display_name: str = "Base Provider"

        def start_login(self, *, redirect_uri: str) -> LoginStart:
            raise NotImplementedError

        def complete_login(
            self,
            *,
            code: str,
            state: str,
            code_verifier: Optional[str] = None,
            redirect_uri: str,
        ) -> Session:
            raise NotImplementedError

        def verify_session(self, *, access_token: str) -> Optional[Session]:
            raise NotImplementedError

        def refresh_session(self, *, refresh_token: str) -> Session:
            raise NotImplementedError

        def revoke_session(self, *, refresh_token: str) -> None:
            pass


class AntigravityDashboardAuthProvider(DashboardAuthProvider):
    """Google Antigravity (AGY) OAuth Identity Provider for Hermes Dashboard."""

    name: str = "agy"
    display_name: str = "Google Antigravity (AGY)"

    def __init__(self, auth_manager: Optional[AGYAuthManager] = None):
        self.auth_manager = auth_manager or AGYAuthManager()

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        """Starts the OAuth login flow for dashboard user."""
        code_verifier, code_challenge = generate_pkce_pair()
        state = secrets.token_hex(16)

        url = create_auth_url(
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
        )

        return LoginStart(
            url=url,
            state=state,
            code_verifier=code_verifier,
        )

    def complete_login(
        self,
        *,
        code: str,
        state: str,
        code_verifier: Optional[str] = None,
        redirect_uri: str,
    ) -> Session:
        """Exchanges callback code for authenticated Hermes session."""
        if not code_verifier:
            raise ValueError("PKCE code_verifier is required to complete AGY login.")

        tokens = exchange_code_for_tokens(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )

        email = tokens.get("user_email") or "antigravity_user@google.com"
        user_id = email

        return Session(
            user_id=user_id,
            email=email,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=tokens.get("expires_at"),
            user_name=tokens.get("user_name"),
        )

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        """Validates existing dashboard session token."""
        if not access_token:
            return None

        user_info = fetch_user_info(access_token)
        if not user_info or "email" not in user_info:
            return None

        return Session(
            user_id=user_info["email"],
            email=user_info["email"],
            access_token=access_token,
            user_name=user_info.get("name"),
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        """Refreshes expired session using the refresh token."""
        tokens = refresh_access_token(refresh_token)
        email = tokens.get("user_email") or "antigravity_user@google.com"

        return Session(
            user_id=email,
            email=email,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", refresh_token),
            expires_at=tokens.get("expires_at"),
            user_name=tokens.get("user_name"),
        )

    def revoke_session(self, *, refresh_token: str) -> None:
        """Revokes active session."""
        logger.info("Revoking AGY dashboard session...")
