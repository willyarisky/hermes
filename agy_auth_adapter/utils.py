"""Utility helpers for AGY Auth Adapter."""

import base64
import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("agy_auth_adapter")


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
