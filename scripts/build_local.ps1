$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

.\scripts\fetch_office_skills.ps1
.\scripts\stage_runtime.ps1

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found: $iscc" }

if (Test-Path .\dist) { Remove-Item .\dist -Recurse -Force }
New-Item -ItemType Directory -Force .\dist | Out-Null
& $iscc .\installer\xiaozhi.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }
if (-not (Test-Path .\dist\XiaoZhiSetup.exe)) { throw 'XiaoZhiSetup.exe was not produced' }

.\scripts\verify_installer.ps1
Write-Host "Built and smoke-tested: $Root\dist\XiaoZhiSetup.exe" -ForegroundColor Green
