# ═══════════════════════════════════════════════════════════════════
# Sovereign Agentic OS — Comprehensive Setup Wizard (Windows)
# Idempotent: safe to re-run at any time
# ═══════════════════════════════════════════════════════════════════
#Requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet('hearth', 'forge', 'sovereign')]
    [string]$DeploymentTier = 'hearth',

    [switch]$SkipDocker,
    [switch]$SkipOllama,
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$REPO_ROOT = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$REPO_ROOT\pyproject.toml")) {
    $REPO_ROOT = $PSScriptRoot  # Script is in repo root
}

$banner = @"
═══════════════════════════════════════════════════════════════
  SOVEREIGN AGENTIC OS — SETUP WIZARD
  Target Tier: $DeploymentTier
═══════════════════════════════════════════════════════════════
"@
Write-Host $banner -ForegroundColor Cyan

$checks = @{
    Total = 0; Passed = 0; Skipped = 0; Failed = 0
}

function Write-Check {
    param(
        [string]$Name,
        [string]$Status,  # OK, SKIP, FAIL, INSTALL
        [string]$Detail = ''
    )
    $checks.Total++
    $icon = switch ($Status) {
        'OK'      { $checks.Passed++; '✅' }
        'SKIP'    { $checks.Skipped++; '⏭️' }
        'FAIL'    { $checks.Failed++; '❌' }
        'INSTALL' { $checks.Passed++; '📦' }
        default   { '❓' }
    }
    $suffix = if ($Detail) { " — $Detail" } else { '' }
    Write-Host "  [$icon] $Name$suffix"
}

# ───────────────────────────────────────────────────────────────
# Step 1: WSL2 Detection (Windows only)
# ───────────────────────────────────────────────────────────────
Write-Host "`n[1/9] Checking WSL2..." -ForegroundColor Yellow

$wslInstalled = $false
try {
    $wslOutput = wsl --status 2>&1
    if ($LASTEXITCODE -eq 0) {
        $wslInstalled = $true
        Write-Check 'WSL2' 'OK' 'Installed and configured'
    }
}
catch {}

if (-not $wslInstalled) {
    # Check if WSL feature is at least enabled
    $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    if ($wslFeature -and $wslFeature.State -eq 'Enabled') {
        Write-Check 'WSL2' 'OK' 'Feature enabled (may need distro install)'
    }
    else {
        Write-Check 'WSL2' 'SKIP' 'Not required for local dev, needed for Docker on some systems'
        Write-Host "    To enable WSL2: wsl --install" -ForegroundColor DarkGray
        Write-Host "    Docker Desktop can also run without WSL2 using Hyper-V backend" -ForegroundColor DarkGray
    }
}

# ───────────────────────────────────────────────────────────────
# Step 2: Python + uv
# ───────────────────────────────────────────────────────────────
Write-Host "`n[2/9] Checking Python and uv..." -ForegroundColor Yellow

$pythonOk = $false
try {
    $pyVer = python --version 2>&1
    if ($pyVer -match '3\.(1[0-9]|[2-9][0-9])') {
        $pythonOk = $true
        Write-Check 'Python' 'OK' $pyVer.ToString().Trim()
    }
    else {
        Write-Check 'Python' 'FAIL' "Need 3.10+, found: $pyVer"
    }
}
catch {
    Write-Check 'Python' 'FAIL' 'Not found in PATH'
    Write-Host "    Install from: https://www.python.org/downloads/" -ForegroundColor DarkGray
}

$uvOk = $false
try {
    $uvVer = uv --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $uvOk = $true
        Write-Check 'uv' 'OK' $uvVer.ToString().Trim()
    }
}
catch {}

if (-not $uvOk) {
    if ($NonInteractive) {
        Write-Check 'uv' 'FAIL' 'Not found — run: irm https://astral.sh/uv/install.ps1 | iex'
    }
    else {
        Write-Host "    uv not found. Installing..." -ForegroundColor DarkYellow
        try {
            Invoke-Expression (Invoke-RestMethod https://astral.sh/uv/install.ps1)
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                        [System.Environment]::GetEnvironmentVariable('Path', 'User')
            Write-Check 'uv' 'INSTALL' 'Auto-installed'
            $uvOk = $true
        }
        catch {
            Write-Check 'uv' 'FAIL' "Auto-install failed: $_"
        }
    }
}

# ───────────────────────────────────────────────────────────────
# Step 3: Docker Desktop
# ───────────────────────────────────────────────────────────────
Write-Host "`n[3/9] Checking Docker..." -ForegroundColor Yellow

if ($SkipDocker) {
    Write-Check 'Docker' 'SKIP' 'Skipped via -SkipDocker flag'
}
else {
    $dockerOk = $false
    try {
        $dockerVer = docker --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Check 'Docker CLI' 'OK' $dockerVer.ToString().Trim()
            # Check daemon running
            $dockerInfo = docker info 2>&1
            if ($LASTEXITCODE -eq 0) {
                $dockerOk = $true
                Write-Check 'Docker Daemon' 'OK' 'Running'
            }
            else {
                Write-Check 'Docker Daemon' 'FAIL' 'Not running — start Docker Desktop'
            }
        }
    }
    catch {
        Write-Check 'Docker' 'FAIL' 'Not installed'
        Write-Host "    Install Docker Desktop: https://www.docker.com/products/docker-desktop/" -ForegroundColor DarkGray
        Write-Host "    After install, start Docker Desktop and re-run this script" -ForegroundColor DarkGray
    }

    # Check Docker Compose
    if ($dockerOk) {
        try {
            $composeVer = docker compose version 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Check 'Docker Compose' 'OK' $composeVer.ToString().Trim()
            }
        }
        catch {
            Write-Check 'Docker Compose' 'FAIL' 'docker compose not available'
        }
    }
}

# ───────────────────────────────────────────────────────────────
# Step 4: Ollama
# ───────────────────────────────────────────────────────────────
Write-Host "`n[4/9] Checking Ollama..." -ForegroundColor Yellow

if ($SkipOllama) {
    Write-Check 'Ollama' 'SKIP' 'Skipped via -SkipOllama flag (cloud-only mode)'
}
else {
    $ollamaOk = $false
    try {
        $ollamaVer = ollama --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $ollamaOk = $true
            Write-Check 'Ollama CLI' 'OK' $ollamaVer.ToString().Trim()
        }
    }
    catch {}

    if (-not $ollamaOk) {
        # Check if we can install via winget
        $wingetAvailable = $false
        try {
            winget --version 2>&1 | Out-Null
            $wingetAvailable = ($LASTEXITCODE -eq 0)
        }
        catch {}

        if ($wingetAvailable -and -not $NonInteractive) {
            Write-Host "    Ollama not found. Install via winget? (Y/n): " -ForegroundColor DarkYellow -NoNewline
            $response = Read-Host
            if ($response -ne 'n' -and $response -ne 'N') {
                try {
                    winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements
                    Write-Check 'Ollama' 'INSTALL' 'Installed via winget'
                    $ollamaOk = $true
                }
                catch {
                    Write-Check 'Ollama' 'FAIL' "Install failed: $_"
                }
            }
            else {
                Write-Check 'Ollama' 'SKIP' 'User declined install'
            }
        }
        else {
            Write-Check 'Ollama' 'FAIL' 'Not found — install from https://ollama.com/download'
        }
    }

    # Check if Ollama is serving
    if ($ollamaOk) {
        try {
            $ollamaHealth = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 3 -ErrorAction SilentlyContinue
            $modelCount = ($ollamaHealth.models | Measure-Object).Count
            Write-Check 'Ollama Service' 'OK' "$modelCount models loaded"
        }
        catch {
            Write-Check 'Ollama Service' 'SKIP' 'Not running (start with: ollama serve)'
        }
    }
}

# ───────────────────────────────────────────────────────────────
# Step 5: GPU / VRAM Detection
# ───────────────────────────────────────────────────────────────
Write-Host "`n[5/9] Detecting GPU and VRAM..." -ForegroundColor Yellow

$detectedTier = 'hearth'
try {
    # Try nvidia-smi first
    $nvidiaSmi = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>&1
    if ($LASTEXITCODE -eq 0 -and $nvidiaSmi) {
        $gpuInfo = $nvidiaSmi.ToString().Trim()
        $vramMB = 0
        if ($gpuInfo -match ',\s*(\d+)') {
            $vramMB = [int]$Matches[1]
        }
        $vramGB = [math]::Round($vramMB / 1024, 1)

        if ($vramGB -ge 24) {
            $detectedTier = 'sovereign'
            Write-Check 'GPU' 'OK' "$gpuInfo — ${vramGB}GB VRAM → sovereign tier"
        }
        elseif ($vramGB -ge 8) {
            $detectedTier = 'forge'
            Write-Check 'GPU' 'OK' "$gpuInfo — ${vramGB}GB VRAM → forge tier"
        }
        else {
            Write-Check 'GPU' 'OK' "$gpuInfo — ${vramGB}GB VRAM → hearth tier"
        }
    }
    else {
        throw 'No NVIDIA GPU'
    }
}
catch {
    # Check for any GPU via WMI
    try {
        $gpus = Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue
        $gpu = $gpus | Where-Object { $_.AdapterRAM -gt 0 } | Select-Object -First 1
        if ($gpu) {
            $vramGB = [math]::Round($gpu.AdapterRAM / 1GB, 1)
            Write-Check 'GPU' 'OK' "$($gpu.Name) — ${vramGB}GB (WMI) → hearth tier"
        }
        else {
            Write-Check 'GPU' 'SKIP' 'No dedicated GPU detected — CPU/cloud mode'
        }
    }
    catch {
        Write-Check 'GPU' 'SKIP' 'Unable to detect GPU'
    }
}

# Override tier if detected one is higher than requested
if ($DeploymentTier -eq 'hearth' -and $detectedTier -ne 'hearth') {
    Write-Host "    Suggestion: Your GPU supports '$detectedTier' tier" -ForegroundColor DarkYellow
}

# ───────────────────────────────────────────────────────────────
# Step 6: .env file creation from .env.example
# ───────────────────────────────────────────────────────────────
Write-Host "`n[6/9] Checking .env configuration..." -ForegroundColor Yellow

$envFile = Join-Path $REPO_ROOT '.env'
$envExample = Join-Path $REPO_ROOT '.env.example'

if (Test-Path $envFile) {
    Write-Check '.env' 'OK' 'Already exists — preserving existing config'
}
elseif (Test-Path $envExample) {
    Write-Host "    Creating .env from .env.example..." -ForegroundColor DarkYellow
    $envContent = Get-Content $envExample -Raw
    # Apply tier override
    $envContent = $envContent -replace 'DEPLOYMENT_TIER=\w+', "DEPLOYMENT_TIER=$DeploymentTier"
    # Set correct localhost URLs for local dev (not Docker service names)
    $envContent = $envContent -replace 'OLLAMA_HOST=http://ollama-matrix:11434', 'OLLAMA_HOST=http://localhost:11434'
    $envContent = $envContent -replace 'REDIS_URL=redis://redis-broker:6379/0', 'REDIS_URL=redis://localhost:6379/0'
    $envContent = $envContent -replace 'BASE_DIR=/app', "BASE_DIR=$REPO_ROOT"
    $envContent | Set-Content $envFile -NoNewline
    Write-Check '.env' 'INSTALL' "Created with tier=$DeploymentTier, localhost endpoints"
}
else {
    Write-Check '.env' 'FAIL' '.env.example not found — cannot create .env'
}

# ───────────────────────────────────────────────────────────────
# Step 7: Dependency sync
# ───────────────────────────────────────────────────────────────
Write-Host "`n[7/9] Syncing dependencies..." -ForegroundColor Yellow

if ($uvOk) {
    Push-Location $REPO_ROOT
    try {
        Write-Host "    Running uv sync --all-extras..." -ForegroundColor DarkGray
        uv sync --all-extras
        if ($LASTEXITCODE -eq 0) {
            Write-Check 'Dependencies' 'OK' 'All packages synced'
        }
        else {
            Write-Check 'Dependencies' 'FAIL' "uv sync exited with code $LASTEXITCODE"
        }

        # Generate requirements.txt for Docker builds
        Write-Host "    Compiling requirements.txt..." -ForegroundColor DarkGray
        uv pip compile pyproject.toml -o requirements.txt 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Check 'requirements.txt' 'OK' 'Generated for Docker builds'
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Check 'Dependencies' 'SKIP' 'uv not available'
}

# Pull Redis image if Docker is available
if (-not $SkipDocker -and $dockerOk) {
    Write-Host "    Pulling redis:7-alpine..." -ForegroundColor DarkGray
    docker pull redis:7-alpine 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Check 'Redis Image' 'OK' 'redis:7-alpine pulled'
    }
    else {
        Write-Check 'Redis Image' 'SKIP' 'Pull failed (offline?)'
    }
}

# ───────────────────────────────────────────────────────────────
# Step 8: Project structure validation
# ───────────────────────────────────────────────────────────────
Write-Host "`n[8/9] Validating project structure..." -ForegroundColor Yellow

$requiredFiles = @(
    'pyproject.toml',
    'config/settings.json',
    'governance/ALIGN_LEDGER.yaml',
    'hlf/hlfc.py',
    'agents/core/main.py',
    'agents/core/db.py',
    'security/seccomp.json',
    'config/redis.conf',
    'docker-compose.yml',
    'Dockerfile.base'
)

$missingFiles = @()
foreach ($f in $requiredFiles) {
    $fullPath = Join-Path $REPO_ROOT $f
    if (-not (Test-Path $fullPath)) {
        $missingFiles += $f
    }
}

if ($missingFiles.Count -eq 0) {
    Write-Check 'Project Structure' 'OK' "$($requiredFiles.Count) critical files verified"
}
else {
    Write-Check 'Project Structure' 'FAIL' "Missing: $($missingFiles -join ', ')"
}

# ───────────────────────────────────────────────────────────────
# Step 9: Run test suite
# ───────────────────────────────────────────────────────────────
Write-Host "`n[9/9] Running verification tests..." -ForegroundColor Yellow

if ($uvOk) {
    Push-Location $REPO_ROOT
    try {
        $testResult = uv run python -m pytest tests/test_installation.py -v --tb=short 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Check 'Installation Tests' 'OK' 'All install verification tests passed'
        }
        else {
            Write-Check 'Installation Tests' 'FAIL' "Some tests failed — review output above"
            $testResult | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Check 'Installation Tests' 'SKIP' 'uv not available'
}

# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  SETUP SUMMARY" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Total checks: $($checks.Total)"
Write-Host "  ✅ Passed:    $($checks.Passed)" -ForegroundColor Green
Write-Host "  ⏭️ Skipped:    $($checks.Skipped)" -ForegroundColor Yellow
Write-Host "  ❌ Failed:    $($checks.Failed)" -ForegroundColor Red

if ($checks.Failed -eq 0) {
    Write-Host ""
    Write-Host "  🎯 Setup complete! You can now run:" -ForegroundColor Green
    Write-Host "     .\run.bat     — Start the full system" -ForegroundColor DarkGray
    Write-Host "     .\run.bat 3   — Start MCP server only" -ForegroundColor DarkGray
}
else {
    Write-Host ""
    Write-Host "  ⚠️  Some checks failed. Fix the issues above and re-run:" -ForegroundColor Yellow
    Write-Host "     powershell -ExecutionPolicy Bypass -File setup.ps1" -ForegroundColor DarkGray
}

Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
