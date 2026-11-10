param([Parameter(Mandatory=$true)][ValidateSet('on','off')][string]$State)
$ErrorActionPreference = 'Stop'
$configPath = Join-Path $PSScriptRoot 'ai.local.json'
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$config | Add-Member -MemberType NoteProperty -Name agent_enabled -Value ($State -eq 'on') -Force
$json = $config | ConvertTo-Json
foreach ($path in @($configPath, (Join-Path $PSScriptRoot 'bin/Release/net8.0-windows/ai.local.json'))) {
    if (Test-Path -LiteralPath (Split-Path -Parent $path)) {
        $tempPath = $path + '.agent.tmp'
        [IO.File]::WriteAllText($tempPath, $json)
        Move-Item -LiteralPath $tempPath -Destination $path -Force
    }
}
Write-Output "Aidy general agent: $State. Active sessions reload this setting; API/original selection is preserved."
