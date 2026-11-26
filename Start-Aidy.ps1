$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'bin/Release/net8.0-windows/WpfApp1.exe'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $python)) {
    throw 'Run setup.ps1 first.'
}
$previousPython = $env:AIDY_PYTHON
try {
    $env:AIDY_PYTHON = $python
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -WindowStyle Hidden
} finally {
    $env:AIDY_PYTHON = $previousPython
}
