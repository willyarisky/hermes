# ==============================================================================
# Hermes Agent - Google Antigravity (AGY) Auth Adapter Windows Server Installer
#
# Run from a cloned repo:
#   .\install.ps1
# Or straight from GitHub:
#   irm https://raw.githubusercontent.com/willyarisky/hermes/refs/heads/main/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "Stop"

$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$HOME\.hermes" }
$PluginDir = Join-Path $HermesHome "plugins\agy-auth-adapter"
$RepoSlug = if ($env:AGY_REPO_SLUG) { $env:AGY_REPO_SLUG } else { "willyarisky/hermes" }
$RepoBranch = if ($env:AGY_REPO_BRANCH) { $env:AGY_REPO_BRANCH } else { "main" }

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Installing AGY Auth Adapter for Hermes Agent on Windows Server  " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 1. Create target directories
New-Item -ItemType Directory -Force -Path (Join-Path $HermesHome "plugins") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $HermesHome "logs") | Out-Null

# 2. Locate the plugin sources
#    When piped through `irm | iex` there is no script file on disk, so the
#    repository has to be downloaded before anything can be copied.
function Invoke-Quiet {
    # Runs a native command, swallowing every stream, and returns its exit code.
    # Needed because Windows PowerShell 5.1 turns any stderr output from a native
    # executable into a terminating NativeCommandError while $ErrorActionPreference
    # is "Stop" — even when stderr is redirected.
    param([string]$FilePath, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments *> $null
        return $LASTEXITCODE
    } catch {
        return 1
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-PluginCheckout([string]$Dir) {
    if (-not $Dir) { return $false }
    return (Test-Path (Join-Path $Dir "agy_auth_adapter")) -and (Test-Path (Join-Path $Dir "plugin.yaml"))
}

$TempRoot = $null
$SourceDir = $null
if ($PSCommandPath -and (Test-Path $PSCommandPath)) {
    $Candidate = Split-Path -Parent $PSCommandPath
    if (Test-PluginCheckout $Candidate) { $SourceDir = $Candidate }
}

try {
    if (-not $SourceDir) {
        $TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("agy-install-" + [System.Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
        Write-Host "[*] Downloading $RepoSlug ($RepoBranch)..." -ForegroundColor Yellow

        $Zip = Join-Path $TempRoot "plugin.zip"
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://codeload.github.com/$RepoSlug/zip/refs/heads/$RepoBranch" `
            -OutFile $Zip
        Expand-Archive -Path $Zip -DestinationPath $TempRoot -Force
        Remove-Item $Zip -Force

        $SourceDir = Get-ChildItem -Path $TempRoot -Directory |
            Select-Object -First 1 -ExpandProperty FullName

        if (-not (Test-PluginCheckout $SourceDir)) {
            throw "Downloaded archive does not look like the AGY plugin: $SourceDir"
        }
    }

    # 3. Copy the plugin into place (contents, not the directory itself, so that
    #    a re-install updates in place instead of nesting a copy inside the target)
    $SourceFull = (Resolve-Path $SourceDir).Path.TrimEnd('\')
    if ($SourceFull -ne $PluginDir.TrimEnd('\')) {
        if ($PluginDir.TrimEnd('\').StartsWith($SourceFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to copy $SourceFull into itself ($PluginDir). Point HERMES_HOME at a directory outside the source checkout."
        }
        Write-Host "[*] Copying plugin to: $PluginDir" -ForegroundColor Yellow
        New-Item -ItemType Directory -Force -Path $PluginDir | Out-Null
        Get-ChildItem -Path $SourceFull -Force |
            Where-Object { $_.Name -notin @('.git', '__pycache__') } |
            ForEach-Object { Copy-Item -Path $_.FullName -Destination $PluginDir -Recurse -Force }
        Get-ChildItem -Path $PluginDir -Recurse -Force -Directory -Filter "__pycache__" |
            Remove-Item -Recurse -Force
    }
}
finally {
    if ($TempRoot -and (Test-Path $TempRoot)) { Remove-Item $TempRoot -Recurse -Force }
}

# 4. Install Python dependencies
Write-Host "[*] Installing Python dependencies..." -ForegroundColor Yellow
try {
    python -m pip install -q pyyaml keyring
} catch {
    Write-Host "[!] Dependency install failed; install pyyaml and keyring manually." -ForegroundColor Red
}

# 5. Configure Hermes (run from the installed plugin so agy_auth_adapter is importable)
Write-Host "[*] Configuring Hermes config.yaml..." -ForegroundColor Yellow
Push-Location $PluginDir
try {
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$PluginDir;$env:PYTHONPATH" } else { $PluginDir }
    python -m agy_auth_adapter.cli setup --start-daemon
}
finally {
    Pop-Location
}

# 6. Work out which command form is available on this machine
$AgyCmd = "python -m agy_auth_adapter.cli"
if (Get-Command hermes -ErrorAction SilentlyContinue) {
    if ((Invoke-Quiet "hermes" @("agy", "--help")) -eq 0) {
        $AgyCmd = "hermes agy"
    } else {
        Write-Host ""
        Write-Host "[!] 'hermes agy' is not available yet (the plugin must be enabled in" -ForegroundColor Yellow
        Write-Host "    ~/.hermes/config.yaml). Enable it with:" -ForegroundColor Yellow
        Write-Host "      hermes plugins enable agy-auth-adapter" -ForegroundColor Yellow
        Write-Host "    Until then, run the commands below from $PluginDir." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Installation Complete!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next step: Authenticate using ONE of these methods:"
Write-Host ""
Write-Host "Option A (Token login - recommended, no OAuth client needed):"
Write-Host "  $AgyCmd login --token '<ANTIGRAVITY_TOKEN>'"
Write-Host "  # or from the env: `$env:ANTIGRAVITY_TOKEN='<TOKEN>'; $AgyCmd login --token"
Write-Host ""
Write-Host "Option B (Copy token from a machine that is already logged in):"
Write-Host "  That machine: $AgyCmd export-token"
Write-Host "  This machine: $AgyCmd login --token '<PASTE_JSON_HERE>'"
Write-Host ""
Write-Host "Option C (Browser OAuth - requires your own Google OAuth client):"
Write-Host "  `$env:AGY_OAUTH_CLIENT_ID='<id>.apps.googleusercontent.com'"
Write-Host "  `$env:AGY_OAUTH_CLIENT_SECRET='<secret>'"
Write-Host "  $AgyCmd login --headless"
Write-Host ""
