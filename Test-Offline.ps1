[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& python -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_TESTS_FAILED' }
Write-Host 'ALCC_OFFLINE_TESTS=PASS'
