# Google Antigravity (AGY) Auth Adapter & Provider for Hermes Agent

[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](#)
[![Hermes Plugin](https://img.shields.io/badge/hermes-plugin-purple.svg)](#)

A complete authentication adapter and model provider plugin allowing **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** to authenticate with and utilize **Google Antigravity (AGY)** services and models.

---

## 🌟 Key Features

* 🎟️ **Token Login**: `hermes agy login --token '<TOKEN>'` stores an existing Antigravity token directly — no Google OAuth client to register, ideal for servers and CI.
* 🔐 **Interactive OAuth 2.0 PKCE Login**: Optional browser-based flow (`hermes agy login`) with automatic token retrieval and secure local storage.
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

### Updating

Update an existing install in place — `~/.hermes/config.yaml` and your stored
credentials are left untouched, and the bridge daemon is restarted only if it
was already running:

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/willyarisky/hermes/refs/heads/main/update.sh | bash
# or, from the installed plugin directory:
~/.hermes/plugins/agy-auth-adapter/update.sh
```

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/willyarisky/hermes/refs/heads/main/update.ps1 | iex
# or:  & "$HOME\.hermes\plugins\agy-auth-adapter\update.ps1"
```

Useful flags (both scripts): `--check` / `-Check` reports the installed and
available versions without changing anything, `--no-restart` / `-NoRestart`
leaves the daemon alone, and `--branch <name>` / `-Branch <name>` updates from a
different branch. If the plugin directory is a git checkout, the updater
fast-forwards it instead of overwriting files.

### Option 3: Pip / Virtual Environment

```bash
pip install -e .
```

---

## 🔌 Enabling the Plugin (`hermes agy`)

Hermes plugins are **opt-in**: the `hermes agy` subcommand only exists once
`agy-auth-adapter` is listed under `plugins.enabled` in `~/.hermes/config.yaml`.
The installers do this for you; to do it by hand from the plugin directory:

```bash
cd ~/.hermes/plugins/agy-auth-adapter
python3 -m agy_auth_adapter.cli setup     # writes config.yaml + enables the plugin
# or, using Hermes' own plugin manager:
hermes plugins enable agy-auth-adapter
```

Verify with:

```bash
hermes plugins list | grep agy      # should read "enabled"
hermes agy status
```

**`hermes: error: argument command: invalid choice: 'agy'`** means the plugin is
not enabled (or not installed where Hermes looks). Until it is, every command
also works directly:

```bash
cd ~/.hermes/plugins/agy-auth-adapter
python3 -m agy_auth_adapter.cli login --token '<ANTIGRAVITY_TOKEN>'
python3 -m agy_auth_adapter.cli status
```

Run `HERMES_PLUGINS_DEBUG=1 hermes agy status` to see Hermes' plugin discovery log
if it still does not appear.

---

## 🔑 Logging In

### Token Login (Recommended — no OAuth client required)

If you already have an Antigravity token, hand it to the adapter directly:

```bash
# Pass the token inline (raw token or exported JSON both work)
hermes agy login --token '<ANTIGRAVITY_TOKEN>'

# Pipe it in (keeps the token out of your shell history)
echo '<ANTIGRAVITY_TOKEN>' | hermes agy login --token -

# Take it from the environment, or get prompted for it (hidden input)
ANTIGRAVITY_TOKEN='<ANTIGRAVITY_TOKEN>' hermes agy login --token
hermes agy login --token
```

The credentials are written to `~/.hermes/.antigravity_oauth.json` and mirrored to
the system keyring (pass `--no-keychain` to skip that). Verify with `hermes agy status`.

To move an existing session between machines:

```bash
# On the machine that is already logged in
hermes agy export-token

# On the target machine
hermes agy login --token '<PASTE_JSON_HERE>'
```

### Reusing the `agy` CLI Login (No Token, No OAuth Client)

If the Antigravity (`agy`) CLI is installed and logged in, the adapter uses its
credentials directly — nothing to paste and no OAuth client to register:

```bash
hermes agy detect     # show every CLI credential store found, and which one is active
hermes agy status     # Auth Source reads agy_cli:<file> when one is in use
```

`detect` lists the directories searched (`~/.agy`, `~/.antigravity`,
`~/.gemini/antigravity-cli`, `~/.config/*`, and the platform config dirs on
macOS/Windows), each credential file found, whether it is still valid, and
whether it carries a refresh token. If your CLI keeps its data somewhere else,
point at it and that tree becomes the only one searched:

```bash
export AGY_CLI_HOME=/path/to/agy/config
hermes agy detect
```

Ordering: environment variables → `~/.hermes/.antigravity_oauth.json` → the
Antigravity/Gemini CLI files → discovered `agy` CLI stores → system keyring.
Run `hermes agy logout` to drop a hand-imported token that is shadowing the CLI
session.

**When the CLI session lapses**, run the `agy` CLI once — it renews its own
tokens, and the adapter picks the refreshed file up on the next request. The
adapter can only refresh a CLI credential by itself when the store records the
`client_id`/`client_secret` it was issued to, or when you have configured your
own OAuth client.

### Logging in Without Pasting a Token

Two ways to avoid handling tokens by hand:

**Reuse the Antigravity / Gemini CLI session.** If the official CLI is installed
and logged in on the machine, the adapter picks up its credentials automatically
from `~/.gemini/antigravity-cli/oauth_creds.json` or `~/.gemini/oauth_creds.json`
— no token to copy, and the CLI keeps them refreshed:

```bash
hermes agy logout          # drop any hand-imported token so it stops shadowing
hermes agy status          # Auth Source should read gemini_cli:oauth_creds.json
```

Expiry is read from the CLI's `expiry_date` field, so a stale session is
reported as expired instead of being sent to Google and coming back `401`. When
it lapses, run the CLI once to refresh it (or log in again below).

**Browser OAuth with your own client.** `hermes agy login` runs the PKCE flow and
stores a session with a refresh token, so it renews itself from then on. It needs
an OAuth client once — see [Browser OAuth](#browser-oauth-optional--requires-your-own-google-oauth-client).

### Browser OAuth (Optional — requires your own Google OAuth client)

`hermes agy login` without `--token` runs the OAuth 2.0 PKCE browser flow. No OAuth
client credentials ship with this plugin, so supply your own:

1. In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials),
   create an OAuth 2.0 Client ID of type **Desktop app**.
2. Add `http://localhost:8085/oauth/callback` as an authorized redirect URI.
3. Provide the credentials via environment variables:

   ```bash
   export AGY_OAUTH_CLIENT_ID="<id>.apps.googleusercontent.com"
   export AGY_OAUTH_CLIENT_SECRET="<secret>"
   ```

   …or in `~/.hermes/oauth_client.json` (honours `HERMES_HOME`):

   ```json
   {
     "client_id": "<id>.apps.googleusercontent.com",
     "client_secret": "<secret>"
   }
   ```

   Keep that file out of version control and restrict its permissions
   (`chmod 600 ~/.hermes/oauth_client.json`).

If neither source is configured, the browser flow fails immediately with setup
instructions — token login is unaffected and needs none of this.

---

## 🌐 Remote Server Deployment & Authentication

When running Hermes on a headless VPS / cloud server (AWS, GCP, DigitalOcean, Hetzner, etc.), use one of the following methods to authenticate:

### Method 1: Token Login (Fastest, No GUI or OAuth Client Required)

On the remote server, run:

```bash
hermes agy login --token '<ANTIGRAVITY_TOKEN>'
# or, keeping the token out of shell history:
echo '<ANTIGRAVITY_TOKEN>' | hermes agy login --token -
```

If you are already logged in elsewhere, copy that session over:

1. **On your local computer:**
   ```bash
   hermes agy export-token
   ```
2. **On your remote server:**
   ```bash
   hermes agy login --token '<PASTE_JSON_TOKEN_HERE>'
   ```

`hermes agy import-token '<TOKEN>'` remains available and does the same thing.

### Method 2: Headless / Manual OAuth (Requires Your Own OAuth Client)

With `AGY_OAUTH_CLIENT_ID` / `AGY_OAUTH_CLIENT_SECRET` configured, run:

```bash
hermes agy login --headless
```

1. Open the provided Google authorization URL in your local computer's browser.
2. Sign in with Google and authorize the app.
3. Your browser will redirect to `http://localhost:8085/oauth/callback?code=...`.
4. Copy the redirected URL from your browser's address bar and paste it into the server prompt.

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

Log in with an existing Antigravity token — nothing else to configure:

```bash
hermes agy login --token '<ANTIGRAVITY_TOKEN>'
```

Or run the browser OAuth flow instead (needs your own OAuth client, see [Logging In](#-logging-in)):

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
    base_url: http://127.0.0.1:28080/v1
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
| `hermes agy login --token '<TOKEN>'` | Logs in with an existing Antigravity token (raw or exported JSON; `-` reads stdin, no value uses `ANTIGRAVITY_TOKEN` or prompts) |
| `hermes agy login` | Launches browser OAuth PKCE authentication (requires your own OAuth client) |
| `hermes agy export-token` | Prints the active credentials JSON for transfer to another machine |
| `hermes agy import-token '<TOKEN>'` | Same as `login --token`, kept for compatibility |
| `hermes agy logout` | Clears stored session tokens from local profile and keyring |
| `hermes agy status [--verify]` | Displays active authentication details, token expiry, and daemon health; `--verify` checks the credential against Google |
| `hermes agy daemon start` | Starts proxy bridge as a detached background daemon |
| `hermes agy daemon stop` | Terminates running background daemon and cleans up PID file |
| `hermes agy daemon restart`| Restarts background daemon |
| `hermes agy daemon status` | Checks background daemon PID and HTTP health status |
| `hermes agy proxy [-d]` | Runs OpenAI-compatible HTTP bridge proxy (foreground or `--daemon`) |
| `hermes agy detect` | Shows Antigravity CLI credential stores found on this machine |
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
├── __init__.py                    # Hermes directory-plugin entry point (re-exports register)
├── plugin.yaml                    # Hermes Agent plugin manifest
├── install.sh / install.ps1       # Installers (work piped from GitHub)
├── update.sh / update.ps1         # In-place updaters (--check, --no-restart)
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
│   ├── cli_credentials.py         # Discovery of the 'agy' CLI's own credential store
│   └── utils.py                   # PKCE & JSON filesystem utilities
└── tests/
    ├── __init__.py
    ├── test_auth.py               # Unit tests for auth & provider
    └── test_cli_and_proxy.py      # Unit tests for CLI & proxy handler
```

---

## 🔀 Bridge Port & Daemon

The local OpenAI-compatible bridge listens on **`127.0.0.1:28080`** by default.
It is deliberately not `8080` — that port is usually claimed by a web server or
app runtime, and whatever owns it answers Hermes' model requests with its own
404 page instead of completions. `28080` also sits below the Linux ephemeral
range (32768+), so a transient outbound connection cannot take it either.

Resolution order for the port, highest first:

1. `--port` on the command (`hermes agy daemon start --port 9100`)
2. `AGY_PROXY_PORT` in the environment
3. `providers.agy-proxy.base_url` in `~/.hermes/config.yaml`
4. the packaged default, `28080`

Because an existing `config.yaml` wins over the default, upgrading does **not**
move a running setup by itself — the CLI keeps reporting on whatever endpoint
Hermes is actually calling. To move off a busy port:

```bash
hermes agy setup --port 28080        # rewrites providers.agy-proxy.base_url
hermes agy daemon restart --port 28080
hermes agy daemon status
```

---

## 🩺 Troubleshooting

### `HTTP 404` from the bridge endpoint (HTML error page)

Hermes is reaching *something* on that port, but not the AGY bridge — an HTML 404
means a web server (nginx, Apache, a site, another app) owns the port. Check who
is listening and where the bridge stands:

```bash
hermes agy daemon status          # says "PORT IN USE by another service" on a conflict
ss -tlnp | grep :28080            # or: lsof -i :28080
curl -s http://127.0.0.1:28080/health  # the AGY bridge answers with JSON
```

Then either free the port, or move the bridge and repoint Hermes at it:

```bash
hermes agy daemon start --port 8090
hermes agy setup --port 8090      # rewrites providers.agy-proxy.base_url
hermes agy daemon status --port 8090
```

If nothing is listening at all, the daemon simply is not running — start it with
`hermes agy daemon start` and check `~/.hermes/logs/agy_proxy.log` if it exits.

### `401 Request had invalid authentication credentials` from the bridge

The bridge reached Google, and Google rejected the stored credential. The proxy
now passes this back as a `401` (not a generic `500`), and the message names the
fix. Check the credential first:

```bash
hermes agy status --verify     # calls Google with the stored token
```

Common causes:

* **The token expired.** Google access tokens last about an hour. A token stored
  with `hermes agy login --token` has no refresh token attached, so it cannot be
  renewed automatically — `hermes agy status` labels such a credential
  `NO refresh token`. Fetch a fresh token and log in again, or use the browser
  flow (`hermes agy login`), which stores a refreshable session.
* **It is not an Antigravity token.** An AI Studio API key (`AIza…`) is routed to
  `generativelanguage.googleapis.com` automatically, but an unrelated token
  (a session cookie, a bearer for a different Google product) will be rejected.
* **A stale credential elsewhere wins.** Resolution order is `ANTIGRAVITY_TOKEN` /
  `AGY_AUTH_TOKEN` / `GEMINI_API_KEY` → `~/.hermes/.antigravity_oauth.json` →
  `~/.gemini/antigravity-cli/` → system keyring. `hermes agy status` prints which
  source won under `Auth Source`; unset a stale environment variable if it is
  shadowing the credential you just stored.

### `hermes: error: argument command: invalid choice: 'agy'`

The plugin is not enabled — see [Enabling the Plugin](#-enabling-the-plugin-hermes-agy).

---

## 🧪 Running Tests

Execute the test suite with:

```bash
python -m unittest discover tests
```

---

## 📄 License

MIT License.
