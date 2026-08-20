"""Utility helpers for AGY Auth Adapter."""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("agy_auth_adapter")

# Default port for the local OpenAI-compatible bridge. Deliberately not 8080:
# that port is commonly taken by web servers and app runtimes, and whatever owns
# it answers Hermes' model requests with its own 404 instead of completions.
# 28080 sits below the Linux ephemeral range (32768+) so transient outbound
# connections cannot claim it either. Override with AGY_PROXY_PORT.
DEFAULT_PROXY_PORT = 28080
DEFAULT_PROXY_HOST = "127.0.0.1"


def get_configured_proxy_port() -> Optional[int]:
    """Reads the bridge port Hermes is actually configured to call.

    Looks at ``providers.agy-proxy.base_url`` in ``~/.hermes/config.yaml`` so the
    CLI reports on the same endpoint Hermes uses, even when that config predates
    a change of the default port. Returns None when unset or unreadable.
    """
    config_yaml = get_hermes_home() / "config.yaml"
    try:
        raw = config_yaml.read_text(encoding="utf-8")
    except OSError:
        return None

    base_url = ""
    try:
        import yaml

        cfg = yaml.safe_load(raw) or {}
        base_url = (
            cfg.get("providers", {}).get("agy-proxy", {}).get("base_url", "") or ""
        )
    except Exception:
        # PyYAML missing or the file is malformed — fall back to a plain scan.
        match = re.search(r"base_url:\s*(\S+)", raw)
        base_url = match.group(1) if match else ""

    match = re.search(r":(\d{1,5})(?:/|$)", str(base_url))
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


def get_default_proxy_port() -> int:
    """Returns the bridge port: AGY_PROXY_PORT, then config.yaml, then the default."""
    raw = os.environ.get("AGY_PROXY_PORT", "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
            logger.warning("AGY_PROXY_PORT=%s is out of range; ignoring", raw)
        except ValueError:
            logger.warning("AGY_PROXY_PORT=%s is not a number; ignoring", raw)

    return get_configured_proxy_port() or DEFAULT_PROXY_PORT


def normalise_token_expiry(data: Dict[str, Any]) -> Optional[float]:
    """Returns an absolute expiry in epoch seconds from any known field name.

    CLI credential stores differ: the Antigravity/Gemini CLI writes 'expiry_date'
    in epoch MILLIseconds, our own files use 'expires_at' in seconds, and raw
    OAuth responses carry a relative 'expires_in'. A missing expiry reads as
    "unknown", never as "never expires".
    """
    expires_at = data.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        return float(expires_at)

    expiry_date = data.get("expiry_date") or data.get("expiryDate") or data.get("expiry")
    if isinstance(expiry_date, (int, float)) and expiry_date > 0:
        # Milliseconds if it is far beyond any plausible epoch-seconds value.
        return float(expiry_date) / 1000.0 if expiry_date > 1e11 else float(expiry_date)

    expires_in = data.get("expires_in") or data.get("expiresIn")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        return time.time() + float(expires_in)

    return None


def find_free_port(preferred: Optional[int] = None, host: Optional[str] = None) -> int:
    """Returns the first port nothing is listening on, starting at *preferred*.

    Used to suggest a concrete alternative when the configured bridge port is
    already taken. Scans a small window and falls back to *preferred* if every
    candidate is busy.
    """
    import socket

    target_host = host or DEFAULT_PROXY_HOST
    start = preferred or DEFAULT_PROXY_PORT
    for candidate in range(start, min(start + 20, 65536)):
        try:
            with socket.create_connection((target_host, candidate), timeout=0.4):
                continue  # someone is listening — try the next one
        except OSError:
            return candidate
    return start


def setup_logger(verbose: bool = False) -> logging.Logger:
    """Configures and returns the adapter logger."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="[%(asctime)s] [%(levelname)s] [AGY-Auth] %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )
    return logger


def generate_pkce_pair() -> tuple[str, str]:
    """Generates a PKCE code_verifier and code_challenge (S256)."""
    # 43-128 chars URL safe
    code_verifier = secrets.token_urlsafe(64)[:96]
    hashed = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = (
        base64.urlsafe_b64encode(hashed).decode("utf-8").rstrip("=")
    )
    return code_verifier, code_challenge


def get_hermes_home() -> Path:
    """Returns the Hermes home directory (typically ~/.hermes)."""
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".hermes").resolve()


def get_gemini_home() -> Path:
    """Returns the Gemini/Antigravity CLI home directory (typically ~/.gemini)."""
    env_home = os.environ.get("GEMINI_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".gemini").resolve()


def safe_read_json(file_path: Path) -> Optional[Dict[str, Any]]:
    """Safely reads a JSON file and returns the dictionary, or None if error."""
    try:
        if file_path.exists() and file_path.is_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"Failed to read JSON from {file_path}: {e}")
    return None


def safe_write_json(file_path: Path, data: Dict[str, Any], mode: int = 0o600) -> bool:
    """Safely writes a dictionary to a JSON file with secure permissions."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = file_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
        # Set file permissions if supported (e.g. Unix)
        if os.name != "nt":
            try:
                os.chmod(temp_file, mode)
            except Exception:
                pass

        temp_file.replace(file_path)
        return True
    except Exception as e:
        logger.error(f"Failed to write JSON to {file_path}: {e}")
        return False
