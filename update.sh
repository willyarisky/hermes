#!/usr/bin/env bash
# ==============================================================================
# Hermes Agent - Google Antigravity (AGY) Auth Adapter Updater
#
# Updates an existing install in place, leaving ~/.hermes/config.yaml and your
# stored credentials untouched.
#
#   ./update.sh                 # update, restarting the daemon if it was running
#   ./update.sh --check         # report the installed and available versions only
#   ./update.sh --no-restart    # update without touching the daemon
#   ./update.sh --branch dev    # update from another branch
# ==============================================================================
set -eu

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_DIR/plugins/agy-auth-adapter"
REPO_SLUG="${AGY_REPO_SLUG:-willyarisky/hermes}"
REPO_BRANCH="${AGY_REPO_BRANCH:-main}"

CHECK_ONLY=0
RESTART=1

while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --no-restart) RESTART=0 ;;
        --branch) shift; REPO_BRANCH="${1:-$REPO_BRANCH}" ;;
        --plugin-dir) shift; PLUGIN_DIR="${1:-$PLUGIN_DIR}" ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "[!] Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

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
echo " Updating AGY Auth Adapter for Hermes Agent                      "
echo "================================================================="

if [ ! -f "$PLUGIN_DIR/plugin.yaml" ]; then
    echo "[!] No AGY plugin found at: $PLUGIN_DIR" >&2
    echo "    Install it first:" >&2
    echo "      curl -fsSL https://raw.githubusercontent.com/$REPO_SLUG/refs/heads/$REPO_BRANCH/install.sh | bash" >&2
    exit 1
fi

read_version() {  # read_version <plugin.yaml path>
    sed -n 's/^version:[[:space:]]*//p' "$1" | head -n 1
}

CURRENT_VERSION="$(read_version "$PLUGIN_DIR/plugin.yaml")"
echo "[*] Installed: ${CURRENT_VERSION:-unknown}  ($PLUGIN_DIR)"

PY_CMD="$(pick_python)" || {
    echo "[!] No working Python interpreter found (tried python3, python)." >&2
    exit 1
}

# Remember whether the bridge was running so it can be brought back up.
DAEMON_WAS_RUNNING=0
if [ "$RESTART" -eq 1 ]; then
    if (cd "$PLUGIN_DIR" && PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PY_CMD" -c \
        "import sys; from agy_auth_adapter.daemon import DaemonManager; sys.exit(0 if DaemonManager().status()['running'] else 1)" \
        > /dev/null 2>&1); then
        DAEMON_WAS_RUNNING=1
    fi
fi

# --- Fetch the new sources -------------------------------------------------
if [ -d "$PLUGIN_DIR/.git" ] && command -v git > /dev/null 2>&1; then
    SOURCE_MODE="git"
    if [ "$CHECK_ONLY" -eq 1 ]; then
        git -C "$PLUGIN_DIR" fetch --quiet origin "$REPO_BRANCH"
        AVAILABLE_VERSION="$(git -C "$PLUGIN_DIR" show "origin/$REPO_BRANCH:plugin.yaml" 2>/dev/null \
            | sed -n 's/^version:[[:space:]]*//p' | head -n 1)"
        BEHIND="$(git -C "$PLUGIN_DIR" rev-list --count "HEAD..origin/$REPO_BRANCH" 2>/dev/null || echo "?")"
        echo "[*] Available: ${AVAILABLE_VERSION:-unknown}  ($BEHIND commit(s) behind origin/$REPO_BRANCH)"
        exit 0
    fi
else
    SOURCE_MODE="download"
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
        echo "[!] Need either 'git' or 'curl' + 'tar' to download the update." >&2
        exit 1
    fi

    if [ ! -f "$SOURCE_DIR/plugin.yaml" ] || [ ! -d "$SOURCE_DIR/agy_auth_adapter" ]; then
        echo "[!] Downloaded archive does not look like the AGY plugin: $SOURCE_DIR" >&2
        exit 1
    fi

    if [ "$CHECK_ONLY" -eq 1 ]; then
        echo "[*] Available: $(read_version "$SOURCE_DIR/plugin.yaml")  (branch $REPO_BRANCH)"
        exit 0
    fi
fi

# --- Apply -----------------------------------------------------------------
if [ "$RESTART" -eq 1 ] && [ "$DAEMON_WAS_RUNNING" -eq 1 ]; then
    echo "[*] Stopping background daemon..."
    (cd "$PLUGIN_DIR" && PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        "$PY_CMD" -m agy_auth_adapter.cli daemon stop > /dev/null 2>&1) || true
fi

if [ "$SOURCE_MODE" = "git" ]; then
    echo "[*] Pulling latest from origin/$REPO_BRANCH..."
    git -C "$PLUGIN_DIR" fetch --quiet origin "$REPO_BRANCH"
    if ! git -C "$PLUGIN_DIR" merge --ff-only "origin/$REPO_BRANCH" > /dev/null 2>&1; then
        echo "[!] Cannot fast-forward $PLUGIN_DIR (local changes or diverged history)." >&2
        echo "    Resolve it there, or re-run the installer to replace the directory." >&2
        exit 1
    fi
else
    echo "[*] Replacing plugin files in: $PLUGIN_DIR"
    (cd "$SOURCE_DIR" && tar -cf - \
        --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' .) \
        | (cd "$PLUGIN_DIR" && tar -xf -)
fi

# Drop stale bytecode so renamed/removed modules cannot linger.
find "$PLUGIN_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

NEW_VERSION="$(read_version "$PLUGIN_DIR/plugin.yaml")"
if [ "$CURRENT_VERSION" = "$NEW_VERSION" ]; then
    echo "[*] Version unchanged (${NEW_VERSION:-unknown}) — files refreshed."
else
    echo "[*] Updated ${CURRENT_VERSION:-unknown} -> ${NEW_VERSION:-unknown}"
fi

# --- Dependencies ----------------------------------------------------------
PIP_CMD="$(pick_pip || true)"
if [ -n "$PIP_CMD" ]; then
    echo "[*] Refreshing Python dependencies..."
    "$PIP_CMD" install -q --upgrade pyyaml keyring \
        || "$PIP_CMD" install -q --upgrade --break-system-packages pyyaml keyring \
        || echo "[!] Dependency refresh failed; install pyyaml and keyring manually." >&2
fi

# --- Daemon ----------------------------------------------------------------
if [ "$RESTART" -eq 1 ] && [ "$DAEMON_WAS_RUNNING" -eq 1 ]; then
    echo "[*] Restarting background daemon..."
    (cd "$PLUGIN_DIR" && PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        "$PY_CMD" -m agy_auth_adapter.cli daemon start) || \
        echo "[!] Daemon did not come back up — check: $HERMES_DIR/logs/agy_proxy.log" >&2
fi

echo ""
echo "================================================================="
echo " Update Complete!"
echo "================================================================="
echo ""
echo "Verify with:"
if command -v hermes > /dev/null 2>&1 && hermes agy --help > /dev/null 2>&1; then
    echo "  hermes agy status --verify"
else
    echo "  cd $PLUGIN_DIR && $PY_CMD -m agy_auth_adapter.cli status --verify"
fi
echo ""
