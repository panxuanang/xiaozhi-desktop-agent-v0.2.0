$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Target = Join-Path $Root 'app\assets\skills'
$Temp = Join-Path $Root 'build\office-skill-fetch'

New-Item -ItemType Directory -Force -Path $Temp | Out-Null

Write-Host 'Trying to fetch official @deepseek-ai/dsh-skill-office@0.1.6-alpha.2 ...'
Push-Location $Temp
try {
    $tgz = npm pack '@deepseek-ai/dsh-skill-office@0.1.6-alpha.2' --silent
    if (-not $tgz) { throw 'npm pack returned no archive name' }
    $Archive = Join-Path $Temp ($tgz | Select-Object -Last 1)
    $Extract = Join-Path $Temp 'extract'
    New-Item -ItemType Directory -Force -Path $Extract | Out-Null
    tar -xf $Archive -C $Extract

    $docx = Get-ChildItem $Extract -Filter SKILL.md -Recurse | Where-Object { $_.FullName -match 'office-docx' } | Select-Object -First 1
    $xlsx = Get-ChildItem $Extract -Filter SKILL.md -Recurse | Where-Object { $_.FullName -match 'office-xlsx' } | Select-Object -First 1
    $pptx = Get-ChildItem $Extract -Filter SKILL.md -Recurse | Where-Object { $_.FullName -match 'office-pptx' } | Select-Object -First 1
    $checker = Get-ChildItem $Extract -Filter check_office.py -Recurse | Select-Object -First 1

    if ($docx) { Copy-Item $docx.FullName (Join-Path $Target 'office-docx\SKILL.md') -Force }
    if ($xlsx) { Copy-Item $xlsx.FullName (Join-Path $Target 'office-xlsx\SKILL.md') -Force }
    if ($pptx) { Copy-Item $pptx.FullName (Join-Path $Target 'office-pptx\SKILL.md') -Force }
    if ($checker) { Copy-Item $checker.FullName (Join-Path $Target 'scripts\check_office.py') -Force }

    $license = Get-ChildItem $Extract -Filter LICENSE* -Recurse | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $Target 'OFFICIAL_SKILL_LICENSE.txt') -Force }

    Write-Host 'Official Office Skills fetched. Fallback files were replaced where available.'
}
catch {
    Write-Warning "Official Office Skill fetch failed; keeping bundled fallback skill guidance. $($_.Exception.Message)"
}
finally {
    Pop-Location
}
