# Run interactively. Input is masked and never included in shell history.
param([ValidateRange(1,3)][int]$Count = 1)
$ErrorActionPreference = 'Stop'
$credentialDirectory = Join-Path $env:LOCALAPPDATA 'Aidy'
$credentialPath = Join-Path $credentialDirectory 'ai.credentials.local.json'
New-Item -ItemType Directory -Force -Path $credentialDirectory | Out-Null
$values = @()
try {
    for ($index = 1; $index -le $Count; $index++) {
        $secretInput = Read-Host "Google API key $index" -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretInput)
        try {
            $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            if ([string]::IsNullOrWhiteSpace($value)) { throw 'An empty credential was entered.' }
            $values += $value.Trim()
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secretInput.Dispose()
            $value = $null
        }
    }
    [IO.File]::WriteAllText($credentialPath, (@{provider='google'; keys=$values} | ConvertTo-Json))
    Write-Output 'Google credentials saved locally. Restart Aidy to reload them.'
} finally {
    $values = $null
}
