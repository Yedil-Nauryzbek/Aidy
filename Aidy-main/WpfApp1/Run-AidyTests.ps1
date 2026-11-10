$ErrorActionPreference = 'Stop'
$previousMode = $env:AIDY_INTERPRETATION_MODE
Push-Location $PSScriptRoot
try {
    # Legacy test fixtures must never consume the user's live cloud settings.
    $env:AIDY_INTERPRETATION_MODE = 'original'
    & '.\.venv\Scripts\python.exe' -X utf8 -m unittest discover -s PythonCore/aidy/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Aidy tests failed.' }
} finally {
    $env:AIDY_INTERPRETATION_MODE = $previousMode
    Pop-Location
}
