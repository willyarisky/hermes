"""Authentication and Token Management for Antigravity (AGY)."""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agy_auth_adapter.oauth import (
    fetch_user_info,
    refresh_access_token,
    run_interactive_login,
)
from agy_auth_adapter.cli_credentials import load_best_cli_credential
from agy_auth_adapter.utils import (
    get_gemini_home,
    get_hermes_home,
    safe_read_json,
    safe_write_json,
)

logger = logging.getLogger("agy_auth_adapter.auth")

KEYRING_SERVICE_NAME = "antigravity"
KEYRING_USERNAME = "oauth_credentials"


@dataclass
class AuthCredentials:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    source: str = "unknown"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        # Buffer of 60 seconds before actual expiration
        return time.time() >= (self.expires_at - 60)


class AGYAuthManager:
    """Manages credentials, tokens, and OAuth lifecycle for Antigravity integration."""

    def __init__(
        self,
        hermes_home: Optional[Path] = None,
        gemini_home: Optional[Path] = None,
    ):
        self.hermes_home = hermes_home or get_hermes_home()
        self.gemini_home = gemini_home or get_gemini_home()
        self.token_file = self.hermes_home / ".antigravity_oauth.json"
        self._cached_creds: Optional[AuthCredentials] = None
        self._last_issue: Optional[str] = None
        # Client the discovered CLI credential was issued to, when its store
        # records it — needed to refresh that credential.
        self._discovered_client: Optional[Dict[str, Optional[str]]] = None

    def get_credentials(self, force_refresh: bool = False) -> Optional[AuthCredentials]:
        """Resolves and returns valid credentials from the priority hierarchy."""
        if self._cached_creds and not force_refresh and not self._cached_creds.is_expired:
            return self._cached_creds

        self._last_issue = None

        # 1. Check Environment Variables
        env_token = (
            os.environ.get("ANTIGRAVITY_TOKEN")
            or os.environ.get("AGY_AUTH_TOKEN")
            or os.environ.get("GEMINI_API_KEY")
        )
        if env_token:
            creds = AuthCredentials(
                access_token=env_token,
                source="environment_variable",
            )
            self._cached_creds = creds
            return creds

        for loader, label in (
            (self._load_from_token_file, "~/.hermes/.antigravity_oauth.json"),
            (self._load_from_gemini_cli, "the Antigravity/Gemini CLI credential file"),
            (self._load_from_cli_discovery, "the installed 'agy' CLI credential store"),
            (self._load_from_keyring, "the system keyring"),
        ):
            creds = loader()
            if not creds:
                continue

            if not creds.is_expired:
                self._cached_creds = creds
                return creds

            if creds.refresh_token:
                logger.info(f"Access token from {label} expired. Refreshing via Google OAuth...")
                try:
                    refreshed = self._refresh_and_save(creds.refresh_token)
                    self._cached_creds = refreshed
                    return refreshed
                except Exception as e:
                    reason = str(e).splitlines()[0]
                    logger.warning(f"Failed to refresh token from {label}: {reason}")
                    if not self._last_issue:
                        self._last_issue = (
                            f"Credentials in {label} expired. Refresh them by running the "
                            "'agy' CLI once (it renews its own session), or re-authenticate "
                            "with 'hermes agy login --token <FRESH_TOKEN>'. "
                            f"Automatic refresh is unavailable here: {reason}"
                        )
            elif not self._last_issue:
                self._last_issue = (
                    f"Credentials in {label} expired and carry no refresh token. "
                    "Re-authenticate with 'hermes agy login --token <FRESH_TOKEN>' or "
                    "'hermes agy login'."
                )

        return None

    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """Returns a valid access token string, or None if unauthenticated."""
        creds = self.get_credentials(force_refresh=force_refresh)
        return creds.access_token if creds else None

    def get_auth_headers(self) -> Dict[str, str]:
        """Returns standard HTTP Authorization headers with Bearer token."""
        token = self.get_access_token()
        if not token:
            raise RuntimeError(
                "Not authenticated with Antigravity (AGY). Run 'hermes agy login' or set ANTIGRAVITY_TOKEN."
            )
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Hermes-Agent-AGY-Adapter/1.0",
        }

    def login(
        self,
        no_keychain: bool = False,
        port: int = 8085,
        headless: bool = False,
    ) -> AuthCredentials:
        """Executes OAuth PKCE login (interactive browser or headless manual code) and stores tokens."""
        logger.info("Initiating Google Antigravity OAuth login...")
        if headless:
            from agy_auth_adapter.oauth import run_headless_login
            raw_tokens = run_headless_login(port=port)
        else:
            raw_tokens = run_interactive_login(port=port)

        creds = AuthCredentials(
            access_token=raw_tokens["access_token"],
            refresh_token=raw_tokens.get("refresh_token"),
            expires_at=raw_tokens.get("expires_at"),
            user_email=raw_tokens.get("user_email"),
            user_name=raw_tokens.get("user_name"),
            source="hermes_oauth_file",
        )

        # Save to active Hermes profile
        safe_write_json(self.token_file, {
            "access_token": creds.access_token,
            "refresh_token": creds.refresh_token,
            "expires_at": creds.expires_at,
            "user_email": creds.user_email,
            "user_name": creds.user_name,
            "updated_at": time.time(),
        })

        # Optionally save to system Keyring if not opted out
        if not no_keychain:
            self._save_to_keyring(creds)

        self._cached_creds = creds
        logger.info(f"Successfully authenticated as {creds.user_email or 'Antigravity User'}")
        return creds

    def export_token(self) -> str:
        """Exports active credentials as a portable JSON string (for remote server deployment)."""
        creds = self.get_credentials()
        if not creds:
            raise RuntimeError("No active credentials found to export. Run 'hermes agy login' first.")

        data = {
            "access_token": creds.access_token,
            "refresh_token": creds.refresh_token,
            "expires_at": creds.expires_at,
            "user_email": creds.user_email,
            "user_name": creds.user_name,
        }
        return json.dumps(data)

    def import_token(self, token_payload: str, no_keychain: bool = False) -> AuthCredentials:
        """Imports credentials from a raw JSON string or token string onto a remote server."""
        try:
            parsed = json.loads(token_payload.strip())
            access_token = parsed.get("access_token")
            refresh_token = parsed.get("refresh_token")
            expires_at = parsed.get("expires_at")
            user_email = parsed.get("user_email")
            user_name = parsed.get("user_name")
        except Exception:
            # Fallback if raw access token string was provided
            access_token = token_payload.strip()
            refresh_token = None
            expires_at = None
            user_email = None
            user_name = None

        if not access_token:
            raise ValueError("Invalid token payload. Could not find access_token.")

        creds = AuthCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            user_email=user_email,
            user_name=user_name,
            source="hermes_oauth_file",
        )

        safe_write_json(self.token_file, {
            "access_token": creds.access_token,
            "refresh_token": creds.refresh_token,
            "expires_at": creds.expires_at,
            "user_email": creds.user_email,
            "user_name": creds.user_name,
            "updated_at": time.time(),
        })

        if not no_keychain:
            self._save_to_keyring(creds)

        self._cached_creds = creds
        return creds

    def logout(self) -> bool:
        """Removes stored tokens and resets cached authentication."""
        success = True
        self._cached_creds = None

        if self.token_file.exists():
            try:
                self.token_file.unlink()
                logger.info(f"Removed token file: {self.token_file}")
            except Exception as e:
                logger.error(f"Error removing {self.token_file}: {e}")
                success = False

        # Clear Keyring entry
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
        except Exception:
            pass

        return success

    def get_status(self) -> Dict[str, Any]:
        """Provides a detailed dictionary describing current auth state."""
        creds = self.get_credentials()
        if not creds:
            return {
                "authenticated": False,
                "message": self._last_issue
                or "Not logged in. Run 'hermes agy login' to authenticate.",
                "token_file": str(self.token_file),
            }

        time_left = max(0, int((creds.expires_at or time.time()) - time.time())) if creds.expires_at else None
        return {
            "authenticated": True,
            "user_email": creds.user_email or "Unknown (token active)",
            "user_name": creds.user_name,
            "source": creds.source,
            "token_file": str(self.token_file) if creds.source == "hermes_oauth_file" else None,
            "expires_in_seconds": time_left,
            "is_expired": creds.is_expired,
            "refreshable": bool(creds.refresh_token),
        }

    def verify_credentials(self, timeout: float = 10.0) -> Dict[str, Any]:
        """Checks the stored credential against Google, without running a completion.

        A token imported with 'login --token' is stored as-is, so the first sign
        that it is wrong is usually a 401 in the middle of a chat. This makes that
        check explicit. Returns a dict with 'ok', 'status', and 'detail'.
        """
        import urllib.error
        import urllib.request

        from agy_auth_adapter.provider import looks_like_api_key

        creds = self.get_credentials()
        if not creds or not creds.access_token:
            return {"ok": False, "status": None, "detail": "No credential stored."}

        if looks_like_api_key(creds.access_token):
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models"
                f"?key={creds.access_token}"
            )
            req = urllib.request.Request(url, method="GET")
        else:
            req = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {creds.access_token}"},
                method="GET",
            )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"ok": True, "status": resp.status, "detail": "Credential accepted by Google."}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:300]
            if e.code == 403:
                # Valid token, but not scoped for the endpoint we probed. That
                # says nothing about whether Antigravity will accept it.
                return {
                    "ok": True,
                    "status": 403,
                    "detail": "Token accepted but lacks scope for this probe — inconclusive.",
                }
            return {"ok": False, "status": e.code, "detail": body}
        except Exception as e:
            return {"ok": False, "status": None, "detail": f"Could not reach Google: {e}"}


    # --- Internal Storage Helpers ---

    def _load_from_token_file(self) -> Optional[AuthCredentials]:
        data = safe_read_json(self.token_file)
        if data and "access_token" in data:
            return AuthCredentials(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expires_at=data.get("expires_at"),
                user_email=data.get("user_email"),
                user_name=data.get("user_name"),
                source="hermes_oauth_file",
            )
        return None

    @staticmethod
    def _normalise_expiry(data: Dict[str, Any]) -> Optional[float]:
        """Returns an absolute expiry in epoch seconds from any known field name.

        The Gemini/Antigravity CLI writes 'expiry_date' in epoch MILLIseconds;
        our own files use 'expires_at' in seconds, and OAuth responses carry a
        relative 'expires_in'. Missing expiry used to read as "never expires",
        so a long-dead token was sent to Google and came back 401.
        """
        expires_at = data.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at > 0:
            return float(expires_at)

        expiry_date = data.get("expiry_date")
        if isinstance(expiry_date, (int, float)) and expiry_date > 0:
            # Milliseconds if it is far beyond any plausible epoch-seconds value.
            return float(expiry_date) / 1000.0 if expiry_date > 1e11 else float(expiry_date)

        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            return time.time() + float(expires_in)

        return None

    def _load_from_gemini_cli(self) -> Optional[AuthCredentials]:
        """Inspects ~/.gemini or ~/.gemini/antigravity-cli for existing credentials."""
        search_paths = [
            self.gemini_home / "antigravity-cli" / "oauth_creds.json",
            self.gemini_home / "antigravity-cli" / "antigravity_oauth.json",
            self.gemini_home / "oauth_creds.json",
            self.gemini_home / "antigravity_oauth.json",
        ]
        for p in search_paths:
            data = safe_read_json(p)
            if data and "access_token" in data:
                return AuthCredentials(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token"),
                    expires_at=self._normalise_expiry(data),
                    user_email=data.get("user_email"),
                    user_name=data.get("user_name"),
                    source=f"gemini_cli:{p.name}",
                )
        return None

    def _load_from_cli_discovery(self) -> Optional[AuthCredentials]:
        """Reads whatever the installed 'agy' CLI already stored, wherever it keeps it."""
        try:
            cred = load_best_cli_credential()
        except Exception as e:  # discovery must never break credential resolution
            logger.debug(f"CLI credential discovery failed: {e}")
            return None
        if not cred:
            return None

        if cred.get("client_id") and cred.get("client_secret"):
            self._discovered_client = {
                "client_id": cred["client_id"],
                "client_secret": cred["client_secret"],
            }

        return AuthCredentials(
            access_token=cred["access_token"],
            refresh_token=cred.get("refresh_token"),
            expires_at=cred.get("expires_at"),
            user_email=cred.get("user_email"),
            user_name=cred.get("user_name"),
            source=f"agy_cli:{cred['path'].name}",
        )

    def _load_from_keyring(self) -> Optional[AuthCredentials]:
        try:
            import json
            import keyring

            raw = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
            if raw:
                data = json.loads(raw)
                if "access_token" in data:
                    return AuthCredentials(
                        access_token=data["access_token"],
                        refresh_token=data.get("refresh_token"),
                        expires_at=data.get("expires_at"),
                        user_email=data.get("user_email"),
                        user_name=data.get("user_name"),
                        source="system_keyring",
                    )
        except Exception:
            pass
        return None

    def _save_to_keyring(self, creds: AuthCredentials) -> None:
        try:
            import json
            import keyring

            payload = json.dumps(asdict(creds))
            keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, payload)
        except Exception:
            pass

    def _refresh_and_save(self, refresh_token: str) -> AuthCredentials:
        client = self._discovered_client or {}
        new_data = refresh_access_token(
            refresh_token,
            client_id=client.get("client_id"),
            client_secret=client.get("client_secret"),
        )
        creds = AuthCredentials(
            access_token=new_data["access_token"],
            refresh_token=new_data.get("refresh_token", refresh_token),
            expires_at=new_data.get("expires_at"),
            user_email=new_data.get("user_email") or (self._cached_creds.user_email if self._cached_creds else None),
            user_name=new_data.get("user_name") or (self._cached_creds.user_name if self._cached_creds else None),
            source="hermes_oauth_file",
        )
        safe_write_json(self.token_file, {
            "access_token": creds.access_token,
            "refresh_token": creds.refresh_token,
            "expires_at": creds.expires_at,
            "user_email": creds.user_email,
            "user_name": creds.user_name,
            "updated_at": time.time(),
        })
        return creds
