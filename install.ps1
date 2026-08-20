# ==============================================================================
# Hermes Agent - Google Antigravity (AGY) Auth Adapter Windows Server Installer
# ==============================================================================

$ErrorActionPreference = "Stop"

$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$HOME\.hermes" }
$PluginDir = "$HermesHome\plugins\agy-auth-adapter"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Installing AGY Auth Adapter for Hermes Agent on Windows Server  " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 1. Create target directories
New-Item -ItemType Directory -Force -Path "$HermesHome\plugins" | Out-Null
New-Item -ItemType Directory -Force -Path "$HermesHome\logs" | Out-Null

# 2. Copy files
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if ($ScriptDir -ne $PluginDir) {
    Write-Host "[*] Copying plugin to: $PluginDir" -ForegroundColor Yellow
    Copy-Item -Recurse -Force $ScriptDir $PluginDir
}

# 3. Install Python dependencies
Write-Host "[*] Installing Python dependencies..." -ForegroundColor Yellow
pip install -q pyyaml keyring

# 4. Configure Hermes
Write-Host "[*] Configuring Hermes config.yaml..." -ForegroundColor Yellow
python -m agy_auth_adapter.cli setup --start-daemon

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Installation Complete!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next step: Authenticate using one of these methods:"
Write-Host "  1. Headless login:     python -m agy_auth_adapter.cli login --headless"
Write-Host "  2. Import local token: python -m agy_auth_adapter.cli import-token '<TOKEN_JSON>'"
Write-Host ""
