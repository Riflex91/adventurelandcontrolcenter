# Windows PowerShell 5.1: first-run dependency setup + launch.
[CmdletBinding()]
param([switch]$NoInstall)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venv = Join-Path $PSScriptRoot '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Creating isolated Python environment...'
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'VENV_CREATION_FAILED' }
}
if (-not $NoInstall) {
    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'PIP_UPDATE_FAILED' }
    & $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'DEPENDENCY_INSTALL_FAILED' }
}
Write-Host 'Running offline tests...'
& $python -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_TESTS_FAILED' }
Write-Host 'Launching AdventureLand Control Center...'
& $python (Join-Path $PSScriptRoot 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'APP_START_FAILED' }
