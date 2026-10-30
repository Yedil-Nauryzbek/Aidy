param([string]$Python = "python", [switch]$SkipModelDownload)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    & $Python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,13), 'Use Python 3.10-3.12 x64'"
    if ($LASTEXITCODE -ne 0) { throw "Unsupported Python interpreter." }
    $venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create Python environment." }
    }
    & $venvPython -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency check failed." }
    if (-not $SkipModelDownload) {
        & $venvPython -c "import sys; sys.path.insert(0, 'PythonCore'); from aidy.speech import SpeechRecognizer; model=SpeechRecognizer(); model.close()"
        if ($LASTEXITCODE -ne 0) { throw "Model download or initialization failed." }
    }
    if (-not $SkipModelDownload) {
        & $venvPython scripts/prepare_voice_model.py
        if ($LASTEXITCODE -ne 0) { throw "Voice ID model preparation failed." }
    }
    dotnet build WpfApp1.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw "WPF build failed." }
    Write-Host 'Ready. Start bin/Release/net8.0-windows/WpfApp1.exe'
} finally {
    Pop-Location
}
