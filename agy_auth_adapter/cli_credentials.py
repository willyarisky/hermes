"""Discovery of credentials already stored by the Antigravity ('agy') CLI.

The adapter should not ask for a token that the machine already has. When the
official CLI is installed and logged in, its credential file is the source of
truth — this module locates that file across the layouts the CLI has used
(``~/.agy``, ``~/.antigravity``, ``~/.gemini/antigravity-cli`` and the platform
config directories), so 'hermes agy' can authenticate with no token pasted and
no OAuth client registered.

Only files that look like credential stores are read, only inside the CLI's own
configuration directories, and nothing is written or sent anywhere.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agy_auth_adapter.utils import normalise_token_expiry

logger = logging.getLogger("agy_auth_adapter.cli_credentials")

# Directories the Antigravity / Gemini CLI keeps its configuration in.
_APP_DIR_NAMES = (
    "agy",
    "antigravity",
    "antigravity-cli",
    "google-antigravity",
    "gemini",
)

# File names that hold OAuth credentials in one layout or another.
_CREDENTIAL_FILE_NAMES = (
    "oauth_creds.json",
    "oauth_credentials.json",
    "antigravity_oauth.json",
    "credentials.json",
    "creds.json",
    "auth.json",
    "token.json",
    "tokens.json",
    "session.json",
)

_MAX_DEPTH = 3
_MAX_FILE_BYTES = 1024 * 1024


def candidate_roots() -> List[Path]:
    """Configuration directories that may hold the CLI's credential store."""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        # No home directory resolvable (bare service environments, cleared env).
        return []

    # An explicit AGY_CLI_HOME is authoritative: when set, only that tree is
    # searched, so pointing at a non-standard install cannot pick up a stale
    # credential from a default location as well.
    explicit = os.environ.get("AGY_CLI_HOME", "").strip()
    if explicit:
        root = Path(explicit)
        return [root] if root.is_dir() else []

    roots: List[Path] = []
    bases = [home, home / ".config", home / ".local" / "share"]

    appdata = os.environ.get("APPDATA")
    if appdata:
        bases.append(Path(appdata))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        bases.append(Path(local_appdata))

    mac_support = home / "Library" / "Application Support"
    if mac_support.exists():
        bases.append(mac_support)

    for base in bases:
        for name in _APP_DIR_NAMES:
            roots.append(base / name)
            roots.append(base / f".{name}")

    seen: set = set()
    unique: List[Path] = []
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        if root.exists() and root.is_dir():
            unique.append(root)
    return unique


def _extract_credential(data: Any) -> Optional[Dict[str, Any]]:
    """Pulls an access token out of a parsed credential file, if there is one.

    Handles both a flat object and one nesting the credential under a key such
    as 'credentials', 'oauth', 'tokens', or an account e-mail address.
    """
    if not isinstance(data, dict):
        return None

    token = data.get("access_token") or data.get("accessToken")
    if isinstance(token, str) and token:
        return {
            "access_token": token,
            "refresh_token": data.get("refresh_token") or data.get("refreshToken"),
            "expires_at": normalise_token_expiry(data),
            "user_email": data.get("user_email") or data.get("email"),
            "user_name": data.get("user_name") or data.get("name"),
            # Some layouts store the client the token was issued to next to it;
            # when present it is what the refresh call needs.
            "client_id": data.get("client_id") or data.get("clientId"),
            "client_secret": data.get("client_secret") or data.get("clientSecret"),
        }

    for value in data.values():
        nested = _extract_credential(value)
        if nested:
            return nested
    return None


def _credential_files(root: Path) -> List[Path]:
    """Credential-looking files under *root*, no deeper than _MAX_DEPTH."""
    found: List[Path] = []
    root_depth = len(root.parts)
    for path in root.rglob("*.json"):
        if len(path.parts) - root_depth > _MAX_DEPTH:
            continue
        if path.name.lower() not in _CREDENTIAL_FILE_NAMES:
            continue
        found.append(path)
    return found


def discover_cli_credentials() -> List[Dict[str, Any]]:
    """Returns every credential store found, freshest usable one first.

    Each entry carries 'path', 'access_token', 'refresh_token', 'expires_at' and
    'expired'. Unexpired credentials sort ahead of expired ones, and within each
    group the later expiry wins.
    """
    import time

    results: List[Dict[str, Any]] = []
    for root in candidate_roots():
        try:
            files = _credential_files(root)
        except OSError as e:
            logger.debug(f"Could not scan {root}: {e}")
            continue

        for path in files:
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                logger.debug(f"Skipping {path}: {e}")
                continue

            cred = _extract_credential(data)
            if not cred:
                continue

            expires_at = cred["expires_at"]
            cred["path"] = path
            cred["expired"] = bool(expires_at and time.time() >= (expires_at - 60))
            results.append(cred)

    results.sort(key=lambda c: (c["expired"], -(c["expires_at"] or 0)))
    return results


def load_best_cli_credential() -> Optional[Dict[str, Any]]:
    """Returns the most usable CLI credential, or None when there is none."""
    for cred in discover_cli_credentials():
        if not cred["expired"]:
            return cred
        if cred["refresh_token"]:
            # Expired but renewable — still worth handing to the refresh path.
            return cred
    return None
