# ==============================================================================
# Hermes Agent - Google Antigravity (AGY) Auth Adapter Updater (Windows)
#
# Updates an existing install in place, leaving ~/.hermes/config.yaml and your
# stored credentials untouched.
#
#   .\update.ps1                # update, restarting the daemon if it was running
#   .\update.ps1 -Check         # report the installed and available versions only
#   .\update.ps1 -NoRestart     # update without touching the daemon
#   .\update.ps1 -Branch dev    # update from another branch
# ==============================================================================

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$NoRestart,
    [string]$Branch,
    [string]$PluginDirectory
)

$ErrorActionPreference = "Stop"

$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$HOME\.hermes" }
$PluginDir = if ($PluginDirectory) { $PluginDirectory } else { Join-Path $HermesHome "plugins\agy-auth-adapter" }
$RepoSlug = if ($env:AGY_REPO_SLUG) { $env:AGY_REPO_SLUG } else { "willyarisky/hermes" }
$RepoBranch = if ($Branch) { $Branch } elseif ($env:AGY_REPO_BRANCH) { $env:AGY_REPO_BRANCH } else { "main" }

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

function Get-PluginVersion([string]$ManifestPath) {
    if (-not (Test-Path $ManifestPath)) { return $null }
    foreach ($line in Get-Content $ManifestPath) {
        if ($line -match '^version:\s*(.+)$') { return $Matches[1].Trim() }
    }
    return $null
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Updating AGY Auth Adapter for Hermes Agent                      " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

$Manifest = Join-Path $PluginDir "plugin.yaml"
if (-not (Test-Path $Manifest)) {
    Write-Host "[!] No AGY plugin found at: $PluginDir" -ForegroundColor Red
    Write-Host "    Install it first:" -ForegroundColor Red
    Write-Host "      irm https://raw.githubusercontent.com/$RepoSlug/refs/heads/$RepoBranch/install.ps1 | iex" -ForegroundColor Red
    exit 1
}

$CurrentVersion = Get-PluginVersion $Manifest
$shownVersion = if ($CurrentVersion) { $CurrentVersion } else { "unknown" }
Write-Host "[*] Installed: $shownVersion  ($PluginDir)" -ForegroundColor Yellow

# Remember whether the bridge was running so it can be brought back up.
$DaemonWasRunning = $false
if (-not $NoRestart) {
    Push-Location $PluginDir
    try {
        $env:PYTHONPATH = if ($env:PYTHONPATH) { "$PluginDir;$env:PYTHONPATH" } else { $PluginDir }
        $probe = "import sys; from agy_auth_adapter.daemon import DaemonManager; sys.exit(0 if DaemonManager().status()['running'] else 1)"
        $DaemonWasRunning = ((Invoke-Quiet "python" @("-c", $probe)) -eq 0)
    } catch {
        $DaemonWasRunning = $false
    } finally {
        Pop-Location
    }
}

$TempRoot = $null
$IsGitCheckout = (Test-Path (Join-Path $PluginDir ".git")) -and (Get-Command git -ErrorAction SilentlyContinue)

try {
    # --- Fetch the new sources ---------------------------------------------
    $SourceDir = $null
    if ($IsGitCheckout) {
        & git -C $PluginDir fetch --quiet origin $RepoBranch
        if ($Check) {
            $remoteManifest = & git -C $PluginDir show "origin/${RepoBranch}:plugin.yaml" 2> $null
            $available = ($remoteManifest | Where-Object { $_ -match '^version:\s*(.+)$' } |
                ForEach-Object { $Matches[1].Trim() } | Select-Object -First 1)
            if (-not $available) { $available = "unknown" }
            $behind = & git -C $PluginDir rev-list --count "HEAD..origin/$RepoBranch"
            Write-Host "[*] Available: $available  ($behind commit(s) behind origin/$RepoBranch)" -ForegroundColor Yellow
            exit 0
        }
    } else {
        $TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("agy-update-" + [System.Guid]::NewGuid().ToString("N"))
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

        if (-not (Test-Path (Join-Path $SourceDir "plugin.yaml"))) {
            throw "Downloaded archive does not look like the AGY plugin: $SourceDir"
        }

        if ($Check) {
            $available = Get-PluginVersion (Join-Path $SourceDir "plugin.yaml")
            Write-Host "[*] Available: $available  (branch $RepoBranch)" -ForegroundColor Yellow
            exit 0
        }
    }

    # --- Apply --------------------------------------------------------------
    if ($DaemonWasRunning) {
        Write-Host "[*] Stopping background daemon..." -ForegroundColor Yellow
        Push-Location $PluginDir
        try { $null = Invoke-Quiet "python" @("-m", "agy_auth_adapter.cli", "daemon", "stop") } finally { Pop-Location }
    }

    if ($IsGitCheckout) {
        Write-Host "[*] Pulling latest from origin/$RepoBranch..." -ForegroundColor Yellow
        $mergeExit = Invoke-Quiet "git" @("-C", $PluginDir, "merge", "--ff-only", "origin/$RepoBranch")
        if ($mergeExit -ne 0) {
            throw "Cannot fast-forward $PluginDir (local changes or diverged history). Resolve it there, or re-run the installer to replace the directory."
        }
    } else {
        Write-Host "[*] Replacing plugin files in: $PluginDir" -ForegroundColor Yellow
        Get-ChildItem -Path $SourceDir -Force |
            Where-Object { $_.Name -notin @('.git', '__pycache__') } |
            ForEach-Object { Copy-Item -Path $_.FullName -Destination $PluginDir -Recurse -Force }
    }

    # Drop stale bytecode so renamed/removed modules cannot linger.
    Get-ChildItem -Path $PluginDir -Recurse -Force -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force

    $NewVersion = Get-PluginVersion $Manifest
    if ($CurrentVersion -eq $NewVersion) {
        Write-Host "[*] Version unchanged ($NewVersion) - files refreshed." -ForegroundColor Yellow
    } else {
        Write-Host "[*] Updated $CurrentVersion -> $NewVersion" -ForegroundColor Green
    }
}
finally {
    if ($TempRoot -and (Test-Path $TempRoot)) { Remove-Item $TempRoot -Recurse -Force }
}

# --- Dependencies ----------------------------------------------------------
Write-Host "[*] Refreshing Python dependencies..." -ForegroundColor Yellow
try {
    python -m pip install -q --upgrade pyyaml keyring
} catch {
    Write-Host "[!] Dependency refresh failed; install pyyaml and keyring manually." -ForegroundColor Red
}

# --- Daemon ----------------------------------------------------------------
if ($DaemonWasRunning) {
    Write-Host "[*] Restarting background daemon..." -ForegroundColor Yellow
    Push-Location $PluginDir
    try {
        & python -m agy_auth_adapter.cli daemon start
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Update Complete!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Verify with:"
$AgyCmd = "python -m agy_auth_adapter.cli"
if (Get-Command hermes -ErrorAction SilentlyContinue) {
    if ((Invoke-Quiet "hermes" @("agy", "--help")) -eq 0) { $AgyCmd = "hermes agy" }
}
Write-Host "  $AgyCmd status --verify"
Write-Host ""

exit 0
