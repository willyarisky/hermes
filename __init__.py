"""Hermes plugin entry point for the Google Antigravity (AGY) Auth Adapter.

Hermes loads directory plugins by importing ``<plugin-dir>/__init__.py`` under a
private module name (``hermes_plugins.agy_auth_adapter``), so the ``agy_auth_adapter``
package that lives inside this directory is not importable by its own name yet.
Putting the plugin directory on ``sys.path`` first makes the package importable the
same way it is when installed with pip, and keeps a single implementation for both.
"""

import sys
from pathlib import Path

_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from agy_auth_adapter import (  # noqa: E402
    AGYAuthManager,
    AGYModelProvider,
    AntigravityDashboardAuthProvider,
    DaemonManager,
    __version__,
    register,
)

__all__ = [
    "AGYAuthManager",
    "AGYModelProvider",
    "AntigravityDashboardAuthProvider",
    "DaemonManager",
    "__version__",
    "register",
]
