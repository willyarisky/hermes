#!/usr/bin/env bash
# ==============================================================================
# Hermes Agent - Google Antigravity (AGY) Auth Adapter Server Installer
#
# Run from a cloned repo:
#   ./install.sh
# Or straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/willyarisky/hermes/refs/heads/main/install.sh | bash
# ==============================================================================
set -eu

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_DIR/plugins/agy-auth-adapter"
REPO_SLUG="${AGY_REPO_SLUG:-willyarisky/hermes}"
REPO_BRANCH="${AGY_REPO_BRANCH:-main}"

# Pick an interpreter that actually runs: on Windows/msys 'python3' can be a
# Microsoft Store stub that is on PATH but exits with an error when invoked.
pick_python() {
    for candidate in python3 python; do
        if command -v "$candidate" > /dev/null 2>&1 && "$candidate" -c "import sys" > /dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

pick_pip() {
    for candidate in pip3 pip; do
        if command -v "$candidate" > /dev/null 2>&1 && "$candidate" --version > /dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

TMP_DIR=""
cleanup() { if [ -n "$TMP_DIR" ]; then rm -rf "$TMP_DIR"; fi; }
trap cleanup EXIT

echo "================================================================="
echo " Installing AGY Auth Adapter for Hermes Agent on Remote Server   "
echo "================================================================="

# 1. Create target directories
mkdir -p "$HERMES_DIR/plugins"
mkdir -p "$HERMES_DIR/logs"

# 2. Locate the plugin sources
#    When piped through `curl | bash` there is no script file on disk, so the
#    repository has to be downloaded before anything can be copied.
is_plugin_checkout() {
    [ -d "$1/agy_auth_adapter" ] && [ -f "$1/plugin.yaml" ]
}

SOURCE_DIR=""
SELF="${BASH_SOURCE[0]:-$0}"
if [ -f "$SELF" ]; then
    CANDIDATE="$(cd "$(dirname "$SELF")" && pwd)"
    if is_plugin_checkout "$CANDIDATE"; then
        SOURCE_DIR="$CANDIDATE"
    fi
fi

if [ -z "$SOURCE_DIR" ]; then
    TMP_DIR="$(mktemp -d)"
    echo "[*] Downloading $REPO_SLUG ($REPO_BRANCH)..."
    if command -v git > /dev/null 2>&1; then
        git clone --quiet --depth 1 --branch "$REPO_BRANCH" \
            "https://github.com/$REPO_SLUG.git" "$TMP_DIR/repo"
        SOURCE_DIR="$TMP_DIR/repo"
    elif command -v curl > /dev/null 2>&1 && command -v tar > /dev/null 2>&1; then
        curl -fsSL "https://codeload.github.com/$REPO_SLUG/tar.gz/refs/heads/$REPO_BRANCH" \
            | tar -xz -C "$TMP_DIR"
        SOURCE_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    else
        echo "[!] Need either 'git' or 'curl' + 'tar' to download the plugin." >&2
        exit 1
    fi

    if ! is_plugin_checkout "$SOURCE_DIR"; then
        echo "[!] Downloaded archive does not look like the AGY plugin: $SOURCE_DIR" >&2
        exit 1
    fi
fi

# 3. Copy the plugin into place (contents, not the directory itself, so that a
#    re-install updates in place instead of nesting a copy inside the target)
if [ "$SOURCE_DIR" != "$PLUGIN_DIR" ]; then
    case "$PLUGIN_DIR/" in
        "$SOURCE_DIR"/*)
            echo "[!] Refusing to copy $SOURCE_DIR into itself ($PLUGIN_DIR)." >&2
            echo "    Point HERMES_HOME at a directory outside the source checkout." >&2
            exit 1
            ;;
    esac
    echo "[*] Copying plugin to: $PLUGIN_DIR"
    mkdir -p "$PLUGIN_DIR"
    (cd "$SOURCE_DIR" && tar -cf - \
        --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' .) \
        | (cd "$PLUGIN_DIR" && tar -xf -)
fi

# 4. Install Python dependencies
echo "[*] Installing Python dependencies..."
PIP_CMD="$(pick_pip || true)"
if [ -z "$PIP_CMD" ]; then
    echo "[!] Neither pip nor pip3 is usable; skipping dependency install." >&2
fi
if [ -n "$PIP_CMD" ]; then
    "$PIP_CMD" install -q pyyaml keyring \
        || "$PIP_CMD" install -q --break-system-packages pyyaml keyring \
        || echo "[!] Dependency install failed; install pyyaml and keyring manually." >&2
fi

# 5. Configure ~/.hermes/config.yaml (run from the installed plugin so the
#    agy_auth_adapter package is importable)
echo "[*] Configuring Hermes config.yaml..."
PY_CMD="$(pick_python)" || {
    echo "[!] No working Python interpreter found (tried python3, python)." >&2
    exit 1
}
(cd "$PLUGIN_DIR" && PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PY_CMD" -m agy_auth_adapter.cli setup --start-daemon)

# 6. Work out which command form is available on this machine
if command -v hermes > /dev/null 2>&1 && hermes agy --help > /dev/null 2>&1; then
    AGY_CMD="hermes agy"
else
    AGY_CMD="python3 -m agy_auth_adapter.cli"
    echo ""
    echo "[!] 'hermes agy' is not available yet (the plugin must be enabled in"
    echo "    ~/.hermes/config.yaml). Enable it with:"
    echo "      hermes plugins enable agy-auth-adapter"
    echo "    Until then, run the commands below from $PLUGIN_DIR."
fi

echo ""
echo "================================================================="
echo " Installation Complete!"
echo "================================================================="
echo ""
echo "Next step: Authenticate this server using ONE of these methods:"
echo ""
echo "Option A (Token login - recommended, no OAuth client needed):"
echo "  $AGY_CMD login --token '<ANTIGRAVITY_TOKEN>'"
echo "  # or pipe it in:   echo '<TOKEN>' | $AGY_CMD login --token -"
echo "  # or from the env: ANTIGRAVITY_TOKEN='<TOKEN>' $AGY_CMD login --token"
echo ""
echo "Option B (Copy token from a machine that is already logged in):"
echo "  That machine:   $AGY_CMD export-token"
echo "  This server:    $AGY_CMD login --token '<PASTE_JSON_HERE>'"
echo ""
echo "Option C (Browser OAuth - requires your own Google OAuth client):"
echo "  export AGY_OAUTH_CLIENT_ID='<id>.apps.googleusercontent.com'"
echo "  export AGY_OAUTH_CLIENT_SECRET='<secret>'"
echo "  $AGY_CMD login --headless"
echo ""
