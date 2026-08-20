#!/usr/bin/env bash
# ==============================================================================
# Hermes Agent - Google Antigravity (AGY) Auth Adapter Server Installer
# ==============================================================================
set -e

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_DIR/plugins/agy-auth-adapter"

echo "================================================================="
echo " Installing AGY Auth Adapter for Hermes Agent on Remote Server   "
echo "================================================================="

# 1. Create target directories
mkdir -p "$HERMES_DIR/plugins"
mkdir -p "$HERMES_DIR/logs"

# 2. Copy or install files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SCRIPT_DIR" != "$PLUGIN_DIR" ]; then
    echo "[*] Copying plugin to: $PLUGIN_DIR"
    cp -r "$SCRIPT_DIR" "$PLUGIN_DIR"
fi

# 3. Install Python dependencies
echo "[*] Installing Python dependencies..."
if command -v pip &> /dev/null; then
    pip install -q pyyaml keyring || pip install -q pyyaml --break-system-packages
elif command -v pip3 &> /dev/null; then
    pip3 install -q pyyaml keyring || pip3 install -q pyyaml --break-system-packages
fi

# 4. Configure ~/.hermes/config.yaml
echo "[*] Configuring Hermes config.yaml..."
python3 -m agy_auth_adapter.cli setup --start-daemon || python -m agy_auth_adapter.cli setup --start-daemon

echo ""
echo "================================================================="
echo " Installation Complete!"
echo "================================================================="
echo ""
echo "Next step: Authenticate your remote server using ONE of these methods:"
echo ""
echo "Option A (Headless / Manual OAuth):"
echo "  hermes agy login --headless"
echo ""
echo "Option B (Copy token from your local machine):"
echo "  Local machine:  hermes agy export-token"
echo "  Remote server:  hermes agy import-token '<PASTE_JSON_HERE>'"
echo ""
echo "Option C (SSH Port Forwarding):"
echo "  ssh -L 8085:localhost:8085 user@server"
echo "  hermes agy login"
echo ""
