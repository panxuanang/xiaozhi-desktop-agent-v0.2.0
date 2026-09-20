$ErrorActionPreference = 'Stop'

$InstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\XiaoZhiAssistant'
$PythonW = Join-Path $InstallRoot 'runtime\pythonw.exe'
$Main = Join-Path $InstallRoot 'app\main.py'
$Target = Join-Path $InstallRoot 'app\src\xiaozhi_agent\secrets_store.py'

if (-not (Test-Path $Target)) { throw "Installed XiaoZhi file not found: $Target" }
if (-not (Test-Path $PythonW)) { throw "Installed XiaoZhi pythonw.exe not found: $PythonW" }

Write-Host 'Stopping installed XiaoZhi process...' -ForegroundColor Cyan
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $PythonW } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

$Backup = "$Target.v012.bak"
Copy-Item $Target $Backup -Force
$content = Get-Content $Target -Raw -Encoding UTF8
$old = 'protected = win32crypt.CryptProtectData(raw, "XiaoZhiAssistant", None, None, None, 0)[1]'
$new = 'protected = win32crypt.CryptProtectData(raw, "XiaoZhiAssistant", None, None, None, 0)'
if ($content.Contains($old)) {
    $content = $content.Replace($old, $new)
    Set-Content -Path $Target -Value $content -Encoding UTF8
    Write-Host 'Installed v0.1.2 DPAPI line patched.' -ForegroundColor Green
} elseif ($content.Contains($new)) {
    Write-Host 'DPAPI line is already patched.' -ForegroundColor Yellow
} else {
    throw 'Expected v0.1.2 DPAPI line was not found; refusing to patch an unknown file.'
}

Write-Host 'Starting XiaoZhi...' -ForegroundColor Cyan
Start-Process -FilePath $PythonW -ArgumentList @("`"$Main`"") -WorkingDirectory $InstallRoot
Write-Host 'HOTFIX_OK - reopen the local console and save the API Key again.' -ForegroundColor Green
