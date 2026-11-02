# Build Windows desktop executable locally. Never accesses Pi.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venv = Join-Path $PSScriptRoot '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'VENV_CREATION_FAILED' }
}
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'APP_DEPENDENCY_FAILED' }
& $python -m pip install 'pyinstaller>=6.22.3,<7'
if ($LASTEXITCODE -ne 0) { throw 'PYINSTALLER_INSTALL_FAILED' }
& $python -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_TESTS_FAILED' }
& $python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name 'AdventureLandControlCenter' `
    --add-data 'assets;assets' `
    --collect-all PySide6 `
    app.py
if ($LASTEXITCODE -ne 0) { throw 'WINDOWS_BUILD_FAILED' }
$exe = Join-Path $PSScriptRoot 'dist\AdventureLandControlCenter\AdventureLandControlCenter.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'EXE_NOT_FOUND' }
Write-Host ('EXE_BUILT=' + $exe)
Write-Host 'Hinweis: Die EXE ist lokal erstellt und nicht code-signiert.'
