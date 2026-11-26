$ErrorActionPreference = 'Stop'
$previousMode = $env:AIDY_INTERPRETATION_MODE
$previousMemory = $env:AIDY_MEMORY_FILE
Push-Location $PSScriptRoot
try {
    # Legacy test fixtures must never consume the user's live cloud settings.
    $env:AIDY_INTERPRETATION_MODE = 'original'
    # ...nor read the user's remembered notes (a fresh path, never created by the suite).
    $env:AIDY_MEMORY_FILE = Join-Path $env:TEMP ('aidy-test-memory-' + [guid]::NewGuid() + '.json')
    & '.\.venv\Scripts\python.exe' -X utf8 -m unittest discover -s PythonCore/aidy/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Aidy tests failed.' }
} finally {
    $env:AIDY_INTERPRETATION_MODE = $previousMode
    $env:AIDY_MEMORY_FILE = $previousMemory
    Pop-Location
}
