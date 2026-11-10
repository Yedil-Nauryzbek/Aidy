param([Parameter(Mandatory=$true)][ValidateSet('original','api')][string]$Mode)
$ErrorActionPreference = 'Stop'
$sourceConfig = Join-Path $PSScriptRoot 'ai.local.json'
$config = if (Test-Path -LiteralPath $sourceConfig) {
    Get-Content -Raw -LiteralPath $sourceConfig | ConvertFrom-Json
} else {
    Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'ai.example.json') | ConvertFrom-Json
}
$config.mode = $Mode
$json = $config | ConvertTo-Json
$paths = @($sourceConfig, (Join-Path $PSScriptRoot 'bin/Release/net8.0-windows/ai.local.json'))
foreach ($path in $paths) {
    if (Test-Path -LiteralPath (Split-Path -Parent $path)) {
        $temp = $path + '.tmp'
        [IO.File]::WriteAllText($temp, $json)
        Move-Item -LiteralPath $temp -Destination $path -Force
    }
}
Write-Output "Aidy interpretation: $Mode. Active sessions reload this setting."
