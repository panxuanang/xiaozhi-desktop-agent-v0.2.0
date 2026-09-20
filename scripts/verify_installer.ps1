$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$Installer = Join-Path $Root 'dist\XiaoZhiSetup.exe'
$TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$SmokeRoot = Join-Path $TempRoot 'xiaozhi-installer-smoke'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunValueName = 'XiaoZhiAssistant'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$Label = ''
    )
    if (-not $Label) { $Label = $FilePath }
    & $FilePath @ArgumentList
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$Label failed with exit code $code" }
}

function Remove-TreeWithRetry {
    param([Parameter(Mandatory = $true)][string]$Path, [int]$Attempts = 5)
    if (-not (Test-Path $Path)) { return }
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            Remove-Item $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($i -eq $Attempts) { throw }
            Start-Sleep -Seconds $i
        }
    }
}

function Get-RunValue {
    try {
        return (Get-ItemProperty -Path $RunKey -Name $RunValueName -ErrorAction Stop).$RunValueName
    }
    catch {
        return $null
    }
}

if (-not (Test-Path $Installer)) { throw "Installer missing: $Installer" }
if ((Get-Item $Installer).Length -lt 10MB) { throw 'Installer is unexpectedly small; packaging likely failed' }

# Preserve any pre-existing value on the CI account, although GitHub-hosted runners normally have none.
$PreviousRunValue = Get-RunValue
$HadPreviousRunValue = $null -ne $PreviousRunValue

Remove-TreeWithRetry -Path $SmokeRoot
New-Item -ItemType Directory -Force -Path $SmokeRoot | Out-Null

try {
    Write-Host '========== Silent-install smoke test (startup ENABLED) ==========' -ForegroundColor Cyan
    $dirArg = '/DIR="' + $SmokeRoot + '"'
    $logArg = '/LOG="' + (Join-Path $SmokeRoot 'setup-smoke.log') + '"'
    $proc = Start-Process -FilePath $Installer -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TASKS="startup"', $dirArg, $logArg
    ) -PassThru -Wait
    if ($proc.ExitCode -ne 0) { throw "Installer smoke install failed with exit code $($proc.ExitCode)" }

    $Python = Join-Path $SmokeRoot 'runtime\python.exe'
    $Pth = Join-Path $SmokeRoot 'runtime\python312._pth'
    $SelfCheck = Join-Path $SmokeRoot 'diagnostics\selfcheck.py'
    $AppMain = Join-Path $SmokeRoot 'app\main.py'
    $AppPackage = Join-Path $SmokeRoot 'app\src\xiaozhi_agent\__init__.py'
    foreach ($item in @($Python, $Pth, $SelfCheck, $AppMain, $AppPackage)) {
        if (-not (Test-Path $item)) { throw "Installed file missing: $item" }
    }

    Write-Host '========== Verify per-user autostart registration ==========' -ForegroundColor Cyan
    $runValue = Get-RunValue
    if (-not $runValue) { throw "Startup Run value was not created: $RunKey -> $RunValueName" }
    $expectedPython = Join-Path $SmokeRoot 'runtime\pythonw.exe'
    $expectedMain = Join-Path $SmokeRoot 'app\main.py'
    if ($runValue -notlike "*$expectedPython*") { throw "Startup Run value does not reference installed pythonw.exe: $runValue" }
    if ($runValue -notlike "*$expectedMain*") { throw "Startup Run value does not reference installed main.py: $runValue" }
    if ($runValue -notlike '*--background*') { throw "Startup Run value is missing --background: $runValue" }
    Write-Host "STARTUP_REGISTRY_OK $runValue" -ForegroundColor Green

    Write-Host '========== Verify installed embedded-Python search path ==========' -ForegroundColor Cyan
    Invoke-Checked -FilePath $Python -ArgumentList @(
        '-c',
        "import pathlib,sys; runtime=pathlib.Path(sys.executable).resolve().parent; expected=(runtime.parent/'app'/'src').resolve(); actual=[pathlib.Path(p).resolve() for p in sys.path if p]; assert expected in actual, f'installed XiaoZhi app src missing from sys.path: expected={expected} actual={actual}'; print('INSTALLED_APP_PATH_OK', expected)"
    ) -Label 'installed app path check'

    Write-Host '========== Verify installed imports and application self-check ==========' -ForegroundColor Cyan
    Invoke-Checked -FilePath $Python -ArgumentList @(
        '-c',
        "import requests, Crypto, win32crypt, win32com.client, deepseek_harness, xiaozhi_agent; from xiaozhi_agent.runtime import XiaoZhiRuntime; print('INSTALLED_RUNTIME_IMPORTS_OK', xiaozhi_agent.__file__)"
    ) -Label 'installed runtime import check'
    Invoke-Checked -FilePath $Python -ArgumentList @($SelfCheck) -Label 'installed application self-check'

    Write-Host 'INSTALLER_SMOKE_OK' -ForegroundColor Green
}
finally {
    # Best-effort uninstall. After uninstall, verify our HKCU Run value is removed.
    $Uninstaller = Join-Path $SmokeRoot 'unins000.exe'
    if (Test-Path $Uninstaller) {
        try {
            Start-Process -FilePath $Uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait | Out-Null
        }
        catch {
            Write-Warning "Smoke-test uninstall cleanup failed: $($_.Exception.Message)"
        }
    }

    if ($HadPreviousRunValue) {
        New-Item -Path $RunKey -Force | Out-Null
        New-ItemProperty -Path $RunKey -Name $RunValueName -Value $PreviousRunValue -PropertyType String -Force | Out-Null
    }
    else {
        $leftover = Get-RunValue
        if ($leftover) {
            # Clean the runner even when failing, but fail the smoke test because the uninstaller should remove it.
            Remove-ItemProperty -Path $RunKey -Name $RunValueName -ErrorAction SilentlyContinue
            throw "Installer/uninstaller left XiaoZhiAssistant in HKCU Run: $leftover"
        }
        Write-Host 'STARTUP_REGISTRY_CLEANUP_OK' -ForegroundColor Green
    }
}
