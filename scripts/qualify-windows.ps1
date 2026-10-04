param(
    [switch]$UseCurrentPython,
    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$NoLaunch,
    [string]$Venv = ".venv-qualification"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DemoRoot = Join-Path $RepoRoot "examples\data_platform_demo"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Resolve-Python {
    if ($UseCurrentPython) {
        $python = Get-Command python -ErrorAction Stop
        return $python.Source
    }

    $venvRoot = Join-Path $RepoRoot $Venv
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"

    if (-not (Test-Path $venvPython)) {
        $py = Get-Command py -ErrorAction Stop
        Write-Host "Creating Python 3.12 qualification environment at $venvRoot"
        & $py.Source -3.12 -m venv $venvRoot
        Assert-LastExitCode "Create virtual environment"
    }

    return $venvPython
}

function Resolve-Spyder {
    param([string]$PythonPath)

    if ($UseCurrentPython) {
        $spyder = Get-Command spyder -ErrorAction Stop
        return $spyder.Source
    }

    $scripts = Split-Path -Parent $PythonPath
    $spyderExe = Join-Path $scripts "spyder.exe"
    if (-not (Test-Path $spyderExe)) {
        throw "Spyder executable not found at $spyderExe. Run without -SkipInstall."
    }
    return $spyderExe
}

Push-Location $RepoRoot
try {
    $PythonPath = Resolve-Python

    if (-not $SkipInstall) {
        Write-Host "Updating pip..."
        & $PythonPath -m pip install -U pip
        Assert-LastExitCode "Update pip"

        Write-Host "Installing FastAPI Studio with dev and Spyder extras..."
        & $PythonPath -m pip install -e ".[dev,spyder]"
        Assert-LastExitCode "Install FastAPI Studio"
    }

    Write-Host "Checking dependency health..."
    & $PythonPath -m pip check
    Assert-LastExitCode "pip check"

    Write-Host "Qualification environment:"
    $EnvironmentInfo = & $PythonPath -c "import platform; from importlib.metadata import version; print('os=' + platform.platform()); print('python=' + platform.python_version()); print('spyder=' + version('spyder')); print('fastapi=' + version('fastapi')); print('fastapi-spyder=' + version('fastapi-spyder'))"
    Assert-LastExitCode "Read environment versions"
    $EnvironmentInfo | ForEach-Object { Write-Host "  $_" }

    Write-Host "Verifying Spyder external-plugin discovery..."
    & $PythonPath -c "from spyder.app.find_plugins import find_external_plugins; plugins=find_external_plugins(); assert plugins.get('fastapi_studio') is not None, plugins; print('plugin_discovery=PASS')"
    Assert-LastExitCode "Spyder plugin discovery"

    Write-Host "Verifying Spyder debug/editor compatibility contract..."
    & $PythonPath -c "from spyder_fastapi.spyder.compat import spyder_contract_issues, variable_explorer_available; issues=spyder_contract_issues(); assert not issues, issues; assert variable_explorer_available(), 'Variable Explorer unavailable'; print('spyder_debug_contract=PASS'); print('variable_explorer=PASS')"
    Assert-LastExitCode "Spyder debug/editor compatibility contract"

    Write-Host "Verifying FastAPI debug runtime..."
    & $PythonPath -c "import uvicorn; print('uvicorn=' + uvicorn.__version__); print('debug_runtime=PASS')"
    Assert-LastExitCode "FastAPI debug runtime"

    if (-not $SkipTests) {
        Write-Host "Running the realistic demo tests..."
        & $PythonPath -m pytest -q "examples\data_platform_demo\tests"
        Assert-LastExitCode "Demo tests"
    }

    if ($NoLaunch) {
        Write-Host "Qualification preflight PASS. Spyder launch skipped."
        exit 0
    }

    $SpyderPath = Resolve-Spyder -PythonPath $PythonPath
    $EvidenceDir = Join-Path $RepoRoot "qualification-local"
    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null

    $Timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    $Head = "unknown"
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -ne $git) {
        $candidateHead = (& $git.Source rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $candidateHead) {
            $Head = $candidateHead.Trim()
        }
    }

    $EvidencePath = Join-Path $EvidenceDir "windows-manual.md"
    $EvidenceLines = @(
        "# FastAPI Studio Windows manual qualification",
        "",
        "- Started: $Timestamp",
        "- Commit: $Head",
        "- Project: examples/data_platform_demo",
        "",
        "## Automated preflight",
        "",
        "- PASS - dependency health (pip check)",
        "- PASS - Spyder external plugin discovery",
        "- PASS - Spyder debug/editor compatibility contract",
        "- PASS - native Variable Explorer availability",
        "- PASS - Uvicorn debug runtime",
        $(if ($SkipTests) { "- SKIPPED - demo tests" } else { "- PASS - demo tests" }),
        "",
        "## Human-only checks",
        "",
        "| # | Check | Result | Notes |",
        "|---|---|---|---|",
        "| 1 | Spyder dock visible, dockable, close/reopen stable | PENDING | |",
        "| 2 | Real Discover -> app:app -> Inspect shows six routes | PENDING | |",
        "| 3 | Route/dependency/model/test double-click opens correct source | PENDING | |",
        "| 4 | Diagram Route/Global/Impact pan/zoom/Fit usable | PENDING | |",
        "| 5 | Debug server hits a real handler breakpoint and Variable Explorer is usable | PENDING | |",
        "| 6 | Telemetry is readable; observed DB node drills into the exact matching trace | PENDING | |",
        "| 7 | 422/404/500 leave the plugin responsive and metrics correctly classified | PENDING | |",
        "",
        "Allowed results: PASS / FAIL / BLOCKED / UNCERTAIN.",
        "",
        "Do not call the Windows UX qualification complete while any row is PENDING or UNCERTAIN."
    )
    Set-Content -Path $EvidencePath -Value $EvidenceLines -Encoding utf8

    Write-Host ""
    Write-Host "Automated preflight PASS."
    Write-Host "Manual evidence sheet: $EvidencePath"
    Write-Host ""
    Write-Host "Human-only qualification:"
    Write-Host "  1. Verify the FastAPI Studio dock can close/reopen."
    Write-Host "  2. Discover -> app:app -> Inspect and confirm six routes."
    Write-Host "  3. Double-click route, get_tenant_context, model and linked test."
    Write-Host "  4. Exercise Diagram Route/Global/Impact pan, zoom and Fit."
    Write-Host "  5. Start debug server and hit a real route-handler breakpoint."
    Write-Host "  6. Run load.py; inspect Telemetry and Diagram -> DB trace drilldown."
    Write-Host "  7. Exercise 422, 404 and 500 and confirm the plugin stays responsive."
    Write-Host ""
    Write-Host "Launching Spyder with data_platform_demo as the working directory..."

    Start-Process -FilePath $SpyderPath -WorkingDirectory $DemoRoot
}
finally {
    Pop-Location
}
