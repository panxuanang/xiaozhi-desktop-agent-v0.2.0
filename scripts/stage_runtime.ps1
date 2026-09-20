$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $Root 'build\stage'
$Runtime = Join-Path $Stage 'runtime'
$AppStage = Join-Path $Stage 'app'
$Diagnostics = Join-Path $Stage 'diagnostics'
$Cache = Join-Path $Root 'build\cache'
$PythonVersion = '3.12.10'
$PythonZip = Join-Path $Cache "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$GetPip = Join-Path $Cache 'get-pip.py'
$Python = Join-Path $Runtime 'python.exe'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$Label = ''
    )
    if (-not $Label) { $Label = $FilePath }
    & $FilePath @ArgumentList
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$Label failed with exit code $code"
    }
}

function Download-WithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [int]$Attempts = 5
    )
    $parent = Split-Path -Parent $OutFile
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            $tmp = "$OutFile.part"
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            Write-Host "Download attempt $i/$Attempts : $Uri"
            Invoke-WebRequest -Uri $Uri -OutFile $tmp -UseBasicParsing -TimeoutSec 180
            if (-not (Test-Path $tmp) -or (Get-Item $tmp).Length -le 0) {
                throw 'downloaded file is empty'
            }
            Move-Item $tmp $OutFile -Force
            return
        }
        catch {
            Remove-Item "$OutFile.part" -Force -ErrorAction SilentlyContinue
            if ($i -eq $Attempts) { throw }
            Write-Warning "Download failed: $($_.Exception.Message). Retrying in $([Math]::Min(15, $i * 3))s..."
            Start-Sleep -Seconds ([Math]::Min(15, $i * 3))
        }
    }
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

function Ensure-PthLine {
    param(
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory = $true)][string]$Value
    )
    foreach ($line in $Lines) {
        if ($line.Trim() -ieq $Value) { return }
    }
    $Lines.Add($Value)
}

Write-Host '========== Clean staging directory ==========' -ForegroundColor Cyan
Remove-TreeWithRetry -Path $Stage
New-Item -ItemType Directory -Force -Path $Runtime, $AppStage, $Diagnostics, $Cache | Out-Null

Write-Host '========== Stage Python embeddable runtime ==========' -ForegroundColor Cyan
if (-not (Test-Path $PythonZip)) {
    Download-WithRetry -Uri $PythonUrl -OutFile $PythonZip
}
try {
    Expand-Archive -Path $PythonZip -DestinationPath $Runtime -Force
}
catch {
    Write-Warning 'Cached Python archive could not be expanded. Downloading a clean copy once.'
    Remove-Item $PythonZip -Force -ErrorAction SilentlyContinue
    Download-WithRetry -Uri $PythonUrl -OutFile $PythonZip
    Expand-Archive -Path $PythonZip -DestinationPath $Runtime -Force
}

if (-not (Test-Path $Python) -or -not (Test-Path (Join-Path $Runtime 'pythonw.exe'))) {
    throw 'Python embeddable runtime is incomplete (python.exe/pythonw.exe missing)'
}

# IMPORTANT: Python's embeddable distribution ships with python312._pth.
# When that file exists, Python ignores PYTHONPATH and the registry by design.
# Therefore the XiaoZhi source path MUST be listed in this file. Relying on
# $env:PYTHONPATH here was the cause of the v0.1.1 GitHub build failure.
$Pth = Join-Path $Runtime 'python312._pth'
if (-not (Test-Path $Pth)) { throw "Missing $Pth" }
$lines = New-Object 'System.Collections.Generic.List[string]'
foreach ($line in (Get-Content $Pth)) {
    $trim = $line.Trim()
    if ($trim -eq '#import site') {
        $lines.Add('import site')
    }
    elseif ($trim -ne '') {
        $lines.Add($line)
    }
}
Ensure-PthLine -Lines $lines -Value 'Lib\site-packages'
Ensure-PthLine -Lines $lines -Value '..\app\src'
Ensure-PthLine -Lines $lines -Value 'import site'
Set-Content -Path $Pth -Value $lines -Encoding ASCII

Write-Host 'python312._pth:' -ForegroundColor DarkCyan
Get-Content $Pth | ForEach-Object { Write-Host "  $_" }

Write-Host '========== Bootstrap pip ==========' -ForegroundColor Cyan
if (-not (Test-Path $GetPip)) {
    Download-WithRetry -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $GetPip
}
Invoke-Checked -FilePath $Python -ArgumentList @($GetPip, '--disable-pip-version-check', '--timeout', '180') -Label 'get-pip.py'

Write-Host '========== Install pinned runtime dependencies ==========' -ForegroundColor Cyan
Invoke-Checked -FilePath $Python -ArgumentList @(
    '-m', 'pip', 'install',
    '--disable-pip-version-check', '--no-cache-dir',
    '--timeout', '180', '--retries', '8',
    '-r', (Join-Path $Root 'requirements-runtime.txt')
) -Label 'pip install runtime requirements'
Invoke-Checked -FilePath $Python -ArgumentList @('-m', 'pip', 'check') -Label 'pip check'

Write-Host '========== Copy application ==========' -ForegroundColor Cyan
Copy-Item (Join-Path $Root 'app\*') $AppStage -Recurse -Force
Copy-Item (Join-Path $Root 'scripts\selfcheck.py') (Join-Path $Diagnostics 'selfcheck.py') -Force
Copy-Item (Join-Path $Root 'VERSION') (Join-Path $Stage 'VERSION') -Force
Copy-Item (Join-Path $Root 'THIRD_PARTY.md') (Join-Path $Stage 'THIRD_PARTY.md') -Force

$required = @(
    (Join-Path $Stage 'runtime\python.exe'),
    (Join-Path $Stage 'runtime\pythonw.exe'),
    (Join-Path $Stage 'runtime\python312._pth'),
    (Join-Path $Stage 'app\main.py'),
    (Join-Path $Stage 'app\src\xiaozhi_agent\__init__.py'),
    (Join-Path $Stage 'app\src\xiaozhi_agent\runtime.py'),
    (Join-Path $Stage 'diagnostics\selfcheck.py')
)
foreach ($item in $required) {
    if (-not (Test-Path $item)) { throw "Staging verification failed; missing: $item" }
}

Write-Host '========== Verify embedded-Python search path ==========' -ForegroundColor Cyan
# Do NOT use PYTHONPATH. The embeddable runtime intentionally ignores it.
Invoke-Checked -FilePath $Python -ArgumentList @(
    '-c',
    "import pathlib,sys; runtime=pathlib.Path(sys.executable).resolve().parent; expected=(runtime.parent/'app'/'src').resolve(); actual=[pathlib.Path(p).resolve() for p in sys.path if p]; assert expected in actual, f'XiaoZhi app src missing from sys.path: expected={expected} actual={actual}'; print('EMBEDDED_APP_PATH_OK', expected)"
) -Label 'embedded app path check'

Write-Host '========== Compile and verify staged runtime ==========' -ForegroundColor Cyan
Invoke-Checked -FilePath $Python -ArgumentList @('-m', 'compileall', '-q', (Join-Path $AppStage 'src')) -Label 'compileall'
Invoke-Checked -FilePath $Python -ArgumentList @(
    '-c',
    "import requests, PIL, qrcode, Crypto, openpyxl, docx, pptx, pandas, matplotlib, psutil, deepseek_harness; import win32crypt, win32com.client; import xiaozhi_agent; from xiaozhi_agent.runtime import XiaoZhiRuntime; print('RUNTIME_IMPORTS_OK', xiaozhi_agent.__file__)"
) -Label 'runtime import check'
Invoke-Checked -FilePath $Python -ArgumentList @((Join-Path $Diagnostics 'selfcheck.py')) -Label 'application self-check'

$version = (Get-Content (Join-Path $Root 'VERSION') -Raw).Trim()
$buildInfo = @(
    "XiaoZhiVersion=$version",
    "Python=$PythonVersion",
    'Harness=0.1.5rc1',
    "BuiltAtUtc=$([DateTime]::UtcNow.ToString('o'))"
)
Set-Content -Path (Join-Path $Stage 'BUILD_INFO.txt') -Value $buildInfo -Encoding UTF8
& $Python -m pip freeze | Set-Content -Path (Join-Path $Stage 'BUILD_REQUIREMENTS.txt') -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw "pip freeze failed with exit code $LASTEXITCODE" }

Write-Host "Stage ready: $Stage" -ForegroundColor Green
Write-Host 'STAGE_RUNTIME_OK' -ForegroundColor Green
