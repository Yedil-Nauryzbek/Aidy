param(
    [string]$DesktopDirectory = [Environment]::GetFolderPath('Desktop'),
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$projectDirectory = $projectRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectDirectory 'WpfApp1.csproj'))) {
    $projectDirectory = Join-Path $projectRoot 'WpfApp1'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectDirectory 'WpfApp1.csproj'))) {
    throw "Aidy project not found under: $projectRoot"
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw 'The Windows .NET Framework C# compiler was not found.'
}
if (-not (Test-Path -LiteralPath $DesktopDirectory -PathType Container)) {
    throw "Desktop directory does not exist: $DesktopDirectory"
}

# Build a small Windows executable with the project path embedded in it.
# All generated files stay in the project's ignored obj directory.
$staging = Join-Path $projectDirectory 'obj\DesktopLauncher'
New-Item -ItemType Directory -Path $staging -Force | Out-Null
$rootResource = Join-Path $staging 'project-root.txt'
[IO.File]::WriteAllText($rootResource, $projectRoot, [Text.UTF8Encoding]::new($false))

# Package the existing logo as a multi-resolution Windows icon.
$iconPath = Join-Path $staging 'Aidy.ico'
$existingIcon = Join-Path $projectDirectory 'Assets\logo.ico'
if (Test-Path -LiteralPath $existingIcon) {
    Copy-Item -LiteralPath $existingIcon -Destination $iconPath -Force
} else {
Add-Type -AssemblyName System.Drawing
$logoPath = Join-Path $projectDirectory 'logo.png'
if (-not (Test-Path -LiteralPath $logoPath)) { $logoPath = Join-Path $projectDirectory 'Assets\logo.png' }
$logo = [Drawing.Image]::FromFile($logoPath)
try {
    $frames = @(foreach ($size in @(16, 32, 48, 256)) {
        $bitmap = [Drawing.Bitmap]::new($size, $size)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $stream = [IO.MemoryStream]::new()
        try {
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.DrawImage($logo, 0, 0, $size, $size)
            $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
            [PSCustomObject]@{ Size = $size; Bytes = $stream.ToArray() }
        } finally {
            $stream.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    })
    $writer = [IO.BinaryWriter]::new([IO.File]::Create($iconPath))
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]$frames.Count)
        $offset = 6 + 16 * $frames.Count
        foreach ($frame in $frames) {
            $dimension = if ($frame.Size -eq 256) { 0 } else { $frame.Size }
            $writer.Write([byte]$dimension)
            $writer.Write([byte]$dimension)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$frame.Bytes.Length)
            $writer.Write([uint32]$offset)
            $offset += $frame.Bytes.Length
        }
        foreach ($frame in $frames) { $writer.Write([byte[]]$frame.Bytes) }
    } finally { $writer.Dispose() }
} finally { $logo.Dispose() }
}

$launcher = Join-Path $staging 'Aidy.exe'
& $compiler /nologo /target:winexe /optimize+ /codepage:65001 `
    /reference:System.dll /reference:System.Core.dll `
    /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
    "/win32icon:$iconPath" "/resource:$rootResource,Aidy.ProjectRoot" `
    "/out:$launcher" (Join-Path $PSScriptRoot 'AidyLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Aidy desktop launcher compilation failed.' }

$destination = Join-Path $DesktopDirectory 'Aidy.exe'
Copy-Item -LiteralPath $launcher -Destination $destination -Force
Write-Host "Created: $destination"
Write-Host "Linked project: $projectRoot"
Write-Host 'Each launch builds the latest project changes before opening Aidy.'
