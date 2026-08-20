# Google Antigravity (AGY) Auth Adapter & Provider for Hermes Agent

[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](#)
[![Hermes Plugin](https://img.shields.io/badge/hermes-plugin-purple.svg)](#)

A complete authentication adapter and model provider plugin allowing **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** to authenticate with and utilize **Google Antigravity (AGY)** services and models.

---

## 🌟 Key Features

* 🔐 **Interactive OAuth 2.0 PKCE Login**: Browser-based authentication flow (`hermes agy login`) with automatic token retrieval and secure local storage.
* 🔄 **Multi-Source Credential Resolution**:
  1. Environment variables (`ANTIGRAVITY_TOKEN`, `AGY_AUTH_TOKEN`, `GEMINI_API_KEY`)
  2. Active Hermes profile: `~/.hermes/.antigravity_oauth.json`
  3. Antigravity CLI native cached credentials (`~/.gemini/antigravity-cli/`)
  4. System Keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service)
* ⏳ **Automatic Token Refresh**: Transparent token renewal when credentials expire without requiring manual re-login.
* 🛡️ **Dashboard Auth Gate**: Implements Hermes `DashboardAuthProvider` (`agy`) to secure the Hermes Web Dashboard on non-loopback bindings.
* 🌉 **OpenAI-Compatible Local Bridge**: Built-in HTTP proxy (`hermes agy proxy`) translating OpenAI chat completions and tool calls to Antigravity / Gemini endpoints.
* ⚡ **Streaming & Tool Calling**: Full support for Server-Sent Events (SSE) streaming and function calling.

---

## 📦 Installation

### Option 1: Direct Plugin Directory (Local or Remote Server)

Copy or symlink this directory into your Hermes plugins folder:

```bash
# Linux / macOS (Local or Remote Server)
mkdir -p ~/.hermes/plugins
cp -r . ~/.hermes/plugins/agy-auth-adapter

# Windows PowerShell
New-Item -ItemType Directory -Force -Path "$HOME\.hermes\plugins"
Copy-Item -Recurse . "$HOME\.hermes\plugins\agy-auth-adapter"
```

### Option 2: Automated One-Line Server Installer

Run the included installation script on your remote server:

```bash
# Linux / macOS Server
curl -fsSL https://raw.githubusercontent.com/willyarisky/hermes/refs/heads/main/install.sh | bash
```

```powershell
# Windows Server (PowerShell)
irm https://raw.githubusercontent.com/willyarisky/hermes/refs/heads/main/install.ps1 | iex
```

Both installers download the plugin themselves, so they work when piped straight
from GitHub. From a cloned repo, run `./install.sh` (or `.\install.ps1`) instead and
the local checkout is used. Set `HERMES_HOME` to install somewhere other than
`~/.hermes`.

### Option 3: Pip / Virtual Environment

```bash
pip install -e .
```

---

## 🔑 OAuth Client Setup (Required Before First Login)

No OAuth client credentials ship with this plugin — you supply your own so that
nothing secret is ever committed to this repository.

1. In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials),
   create an OAuth 2.0 Client ID of type **Desktop app**.
2. Add `http://localhost:8085/oauth/callback` as an authorized redirect URI.
3. Provide the credentials to the adapter in one of two ways:

**Environment variables** (recommended for servers, CI and containers):

```bash
export AGY_OAUTH_CLIENT_ID="<id>.apps.googleusercontent.com"
export AGY_OAUTH_CLIENT_SECRET="<secret>"
```

**Local config file** at `~/.hermes/oauth_client.json` (honours `HERMES_HOME`):

```json
{
  "client_id": "<id>.apps.googleusercontent.com",
  "client_secret": "<secret>"
}
```

Keep this file out of version control and restrict its permissions
(`chmod 600 ~/.hermes/oauth_client.json`). If neither source is configured,
`hermes agy login` fails immediately with setup instructions.

---

## 🌐 Remote Server Deployment & Authentication

When running Hermes on a headless VPS / cloud server (AWS, GCP, DigitalOcean, Hetzner, etc.), use one of the following methods to authenticate:

### Method 1: Headless / Manual OAuth (No GUI Required)

On the remote server, run:

```bash
hermes agy login --headless
```

1. Open the provided Google authorization URL in your local computer's browser.
2. Sign in with Google and authorize the app.
3. Your browser will redirect to `http://localhost:8085/oauth/callback?code=...`.
4. Copy the redirected URL from your browser's address bar and paste it into the server prompt.

### Method 2: Export from Local Machine & Import on Server (Fastest)

If you have already logged in on your local machine:

1. **On your local computer:**
   ```bash
   hermes agy export-token
   ```
2. **On your remote server:**
   ```bash
   hermes agy import-token '<PASTE_JSON_TOKEN_HERE>'
   ```

### Method 3: Secure Copy (SCP / Rsync)

Transfer your local authentication file directly to the remote server:

```bash
scp ~/.hermes/.antigravity_oauth.json user@remote-server:~/.hermes/
```

### Method 4: Environment Variables (CI / Docker)

Set the token directly in your server or container environment:

```bash
export ANTIGRAVITY_TOKEN="<YOUR_ACCESS_OR_API_TOKEN>"
```

### Method 5: Run as a Systemd Service (Linux Server)

To ensure the AGY background bridge runs persistently and starts automatically on system boot:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-agy.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-agy.service
```

---

## 🚀 Quick Start

### 1. Authenticate with Antigravity (AGY)

Run the login command to start the browser OAuth flow:

```bash
hermes agy login
```

*(Optional flags: `--no-keychain` to skip system keyring storage, `--port 8085` to customize callback port)*

### 2. Verify Auth Status

Check your active authentication state, user account, and token expiration:

```bash
hermes agy status
```

Output:
```text
--- Antigravity (AGY) Authentication Status ---
Status:        AUTHENTICATED [Active]
User:          developer@gmail.com
Auth Source:   hermes_oauth_file
Token Expiry:  In 58m 20s (Auto-refreshes)
-----------------------------------------------
```

### 3. Configure Hermes Agent

Run the automatic setup command or edit `~/.hermes/config.yaml`:

```bash
hermes agy setup --model google-antigravity/gemini-3.7-flash
```

Or manually configure `~/.hermes/config.yaml`:

```yaml
# ~/.hermes/config.yaml

model:
  provider: agy-proxy
  default: google-antigravity/gemini-3.7-flash

providers:
  agy-proxy:
    base_url: http://127.0.0.1:8080/v1
    api_key: antigravity-local-auth

plugins:
  enabled:
    - agy-auth-adapter
```

### 4. Manage Background Daemon (Like Codex Auth)

You can run the auth adapter and proxy as a detached background service (daemon):

```bash
# Start background daemon
hermes agy daemon start

# Check status and health
hermes agy daemon status

# Stop background daemon
hermes agy daemon stop

# Restart background daemon
hermes agy daemon restart
```

*Alternatively, run `hermes agy proxy --daemon` or let Hermes auto-start the sidecar proxy transparently upon booting.*

---

## 🛠️ CLI Commands Reference

| Command | Description |
| :--- | :--- |
| `hermes agy login` | Launches browser OAuth PKCE authentication |
| `hermes agy logout` | Clears stored session tokens from local profile and keyring |
| `hermes agy status` | Displays active authentication details, token expiry, and daemon health |
| `hermes agy daemon start` | Starts proxy bridge as a detached background daemon |
| `hermes agy daemon stop` | Terminates running background daemon and cleans up PID file |
| `hermes agy daemon restart`| Restarts background daemon |
| `hermes agy daemon status` | Checks background daemon PID and HTTP health status |
| `hermes agy proxy [-d]` | Runs OpenAI-compatible HTTP bridge proxy (foreground or `--daemon`) |
| `hermes agy models` | Lists supported Antigravity models |
| `hermes agy setup [-d]` | Auto-configures `~/.hermes/config.yaml` with AGY settings |

---

## 🧠 Supported Models

* `google-antigravity/gemini-3.7-flash` *(Default fast reasoning model)*
* `google-antigravity/gemini-3.1-pro` *(High-capability deep reasoning model)*
* `google-antigravity/claude-3-7-sonnet` *(Antigravity-routed Claude model)*
* `google-antigravity/gemini-2.5-pro`
* `google-antigravity/gemini-2.5-flash`

---

## 📂 Project Structure

```
hermes/
├── plugin.yaml                    # Hermes Agent plugin manifest
├── pyproject.toml                 # Package setup and entrypoints
├── requirements.txt               # Dependencies
├── config.example.yaml            # Example Hermes agent configuration
├── README.md                      # Documentation
├── agy_auth_adapter/
│   ├── __init__.py                # Plugin registration hook (register(ctx))
│   ├── auth.py                    # AGYAuthManager & token resolution hierarchy
│   ├── oauth.py                   # Google OAuth 2.0 PKCE flow & loopback server
│   ├── dashboard_auth.py          # Hermes DashboardAuthProvider implementation
│   ├── provider.py                # Model provider & OpenAI<->Gemini message translator
│   ├── proxy.py                   # Local OpenAI-compatible HTTP bridge proxy
│   ├── cli.py                     # CLI subcommand handlers
│   └── utils.py                   # PKCE & JSON filesystem utilities
└── tests/
    ├── __init__.py
    ├── test_auth.py               # Unit tests for auth & provider
    └── test_cli_and_proxy.py      # Unit tests for CLI & proxy handler
```

---

## 🧪 Running Tests

Execute the test suite with:

```bash
python -m unittest discover tests
```

---

## 📄 License

MIT License.
