"""Google OAuth 2.0 PKCE authentication flow for Antigravity (AGY)."""

import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from agy_auth_adapter.utils import generate_pkce_pair

logger = logging.getLogger("agy_auth_adapter.oauth")

# OAuth client credentials are never hardcoded. Supply your own Google OAuth
# "Desktop app" client via the environment (or an ~/.hermes/oauth_client.json file):
#
#   export AGY_OAUTH_CLIENT_ID="<id>.apps.googleusercontent.com"
#   export AGY_OAUTH_CLIENT_SECRET="<secret>"
CLIENT_ID_ENV = "AGY_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "AGY_OAUTH_CLIENT_SECRET"
CLIENT_CONFIG_FILE = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "oauth_client.json"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

DEFAULT_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cloud-platform",
]


_MISSING_CREDENTIALS_HELP = f"""No Google OAuth client configured for Antigravity.
Set {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} in your environment, or create
{CLIENT_CONFIG_FILE} containing:
  {{"client_id": "<id>.apps.googleusercontent.com", "client_secret": "<secret>"}}
Create the client under 'Desktop app' in the Google Cloud Console (APIs & Services > Credentials)."""


def _load_client_config_file() -> Dict[str, str]:
    """Reads OAuth client credentials from the local config file, if present."""
    try:
        with open(CLIENT_CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            "client_id": str(data.get("client_id", "")),
            "client_secret": str(data.get("client_secret", "")),
        }
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Could not read OAuth client config {CLIENT_CONFIG_FILE}: {e}")
        return {}


def get_client_credentials(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    require_secret: bool = True,
) -> Tuple[str, str]:
    """Resolves the OAuth client id/secret from arguments, environment, or config file.

    Raises:
        RuntimeError: if no client id (or secret, when required) can be resolved.
    """
    client_id = client_id or os.environ.get(CLIENT_ID_ENV, "")
    client_secret = client_secret or os.environ.get(CLIENT_SECRET_ENV, "")

    if not client_id or (require_secret and not client_secret):
        file_config = _load_client_config_file()
        client_id = client_id or file_config.get("client_id", "")
        client_secret = client_secret or file_config.get("client_secret", "")

    if not client_id or (require_secret and not client_secret):
        raise RuntimeError(_MISSING_CREDENTIALS_HELP)

    return client_id, client_secret


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Local HTTP request handler to receive the OAuth redirect callback."""

    auth_code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard HTTP server logging to keep terminal clean."""
        pass

    def do_GET(self) -> None:
        parsed_path = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_path.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            OAuthCallbackHandler.state = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html_success = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Antigravity Authentication Succeeded</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #1e293b; padding: 2.5rem; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 420px; border: 1px solid #334155; }
                    h1 { color: #38bdf8; font-size: 1.5rem; margin-bottom: 0.75rem; }
                    p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
                    .badge { display: inline-block; background: #0369a1; color: #e0f2fe; padding: 0.25rem 0.75rem; border-radius: 9999px; font-weight: 600; font-size: 0.8rem; margin-top: 1rem; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Authentication Successful!</h1>
                    <p>Google Antigravity (AGY) credentials have been verified for <strong>Hermes Agent</strong>.</p>
                    <p>You can close this tab and return to your terminal.</p>
                    <span class="badge">Hermes &times; Antigravity</span>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html_success.encode("utf-8"))
        else:
            err = params.get("error", ["Unknown OAuth Error"])[0]
            OAuthCallbackHandler.error = err
            self.send_response(400)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html_err = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authentication Failed</title>
                <style>
                    body {{ font-family: sans-serif; background: #18181b; color: #fafafa; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                    .card {{ background: #27272a; padding: 2rem; border-radius: 8px; text-align: center; max-width: 400px; }}
                    h1 {{ color: #f87171; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Authentication Failed</h1>
                    <p>{err}</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html_err.encode("utf-8"))


def create_auth_url(
    redirect_uri: str,
    state: str,
    code_challenge: str,
    client_id: Optional[str] = None,
    scopes: Optional[list[str]] = None,
) -> str:
    """Builds the Google OAuth 2.0 authorization URL."""
    client_id, _ = get_client_credentials(client_id, require_secret=False)
    scope_str = " ".join(scopes or DEFAULT_SCOPES)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope_str,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent select_account",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Exchanges authorization code and PKCE code_verifier for OAuth tokens."""
    client_id, client_secret = get_client_credentials(client_id, client_secret)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=encoded_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            
            # Calculate absolute expiration timestamp
            expires_in = resp_data.get("expires_in", 3600)
            resp_data["expires_at"] = time.time() + expires_in

            # Fetch user email/profile if access token present
            access_token = resp_data.get("access_token")
            if access_token:
                user_info = fetch_user_info(access_token)
                if user_info:
                    resp_data["user_email"] = user_info.get("email")
                    resp_data["user_name"] = user_info.get("name")
                    resp_data["user_picture"] = user_info.get("picture")

            return resp_data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"OAuth token exchange failed ({e.code}): {error_body}")
        raise RuntimeError(f"OAuth token exchange error: {error_body}") from e
    except Exception as e:
        logger.error(f"OAuth token request failed: {e}")
        raise


def refresh_access_token(
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Refreshes an expired access token using the refresh token."""
    client_id, client_secret = get_client_credentials(client_id, client_secret)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=encoded_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            expires_in = resp_data.get("expires_in", 3600)
            resp_data["expires_at"] = time.time() + expires_in
            if "refresh_token" not in resp_data:
                resp_data["refresh_token"] = refresh_token
            return resp_data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"OAuth token refresh failed ({e.code}): {error_body}")
        raise RuntimeError(f"OAuth token refresh error: {error_body}") from e


def fetch_user_info(access_token: str) -> Optional[Dict[str, Any]]:
    """Retrieves basic profile and email for the authenticated user."""
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"Failed to fetch user info: {e}")
        return None


def run_interactive_login(port: int = 8085, timeout_seconds: int = 120) -> Dict[str, Any]:
    """Starts local loopback HTTP server and launches browser to perform OAuth PKCE login."""
    redirect_uri = f"http://localhost:{port}/oauth/callback"
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_hex(16)

    auth_url = create_auth_url(
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )

    OAuthCallbackHandler.auth_code = None
    OAuthCallbackHandler.state = None
    OAuthCallbackHandler.error = None

    server = http.server.HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request)
    server_thread.daemon = True
    server_thread.start()

    logger.info(f"Opening browser for Google Antigravity authentication...")
    print(f"\n[AGY Auth] If browser does not open automatically, visit:")
    print(f"{auth_url}\n")
    
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    start_time = time.time()
    while OAuthCallbackHandler.auth_code is None and OAuthCallbackHandler.error is None:
        if time.time() - start_time > timeout_seconds:
            server.server_close()
            raise TimeoutError("Authentication timed out waiting for user callback.")
        time.sleep(0.5)

    server.server_close()

    if OAuthCallbackHandler.error:
        raise RuntimeError(f"Authentication failed: {OAuthCallbackHandler.error}")

    code = OAuthCallbackHandler.auth_code
    if not code:
        raise RuntimeError("No authorization code received.")

    logger.info("Authorization code received. Exchanging for access tokens...")
    tokens = exchange_code_for_tokens(
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )
    return tokens


def run_headless_login(port: int = 8085) -> Dict[str, Any]:
    """Interactive login for headless/remote servers (manual code/URL paste)."""
    redirect_uri = f"http://localhost:{port}/oauth/callback"
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_hex(16)

    auth_url = create_auth_url(
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )

    print("\n" + "=" * 65)
    print("  GOOGLE ANTIGRAVITY (AGY) REMOTE / HEADLESS LOGIN")
    print("=" * 65)
    print("\n1. Open this URL in a browser on your local computer:\n")
    print(f"   {auth_url}\n")
    print("2. Sign in with your Google Account and grant permissions.")
    print("3. After signing in, your browser will redirect to a URL starting with:")
    print(f"   http://localhost:{port}/oauth/callback?code=...\n")
    print("4. Copy the ENTIRE URL (or just the code value) from your browser address bar and paste it below:\n")

    user_input = input("Paste URL or Code here > ").strip()
    if not user_input:
        raise ValueError("No authorization code provided.")

    # Extract code if full URL was pasted
    if "code=" in user_input:
        parsed = urllib.parse.urlparse(user_input)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            code = params["code"][0]
        else:
            # Fallback regex split
            code = user_input.split("code=")[1].split("&")[0]
    else:
        code = user_input

    logger.info("Authorization code received. Exchanging for access tokens...")
    tokens = exchange_code_for_tokens(
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )
    return tokens

