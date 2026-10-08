# Shared helpers for the Windows setup, start, and USB preparation scripts.
# Messages shown to the person running the scripts are in Portuguese.

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VendorDir = Join-Path $ProjectRoot "vendor"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) { Write-Host "    OK   $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    !!   $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "    ERRO $Message" -ForegroundColor Red }

function Get-PythonVersion([string[]]$Command) {
    # Return "major.minor" when the command runs Python 3.10 or later, else $null.
    # The Microsoft Store alias "python" exits with an error instead of running Python.
    $exe = $Command[0]
    $arguments = @($Command | Select-Object -Skip 1)
    try {
        $output = & $exe @arguments -c "import sys; print('%d.%d' % sys.version_info[:2] if sys.version_info >= (3, 10) else 'old')" 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -eq 0 -and "$output" -match '^\d+\.\d+$') { return "$output" }
    return $null
}

function Find-Python([string]$Version = "") {
    # Return @{Command; Version} for a usable Python, preferring $Version ("3.14") when given.
    $candidates = @()
    if ($Version) {
        $candidates += , @("py", "-$Version")
        $candidates += , @((Join-Path $env:LOCALAPPDATA ("Programs\Python\Python" + $Version.Replace(".", "") + "\python.exe")))
    }
    $candidates += , @("py", "-3")
    $candidates += , @("python")
    $candidates += , @("python3")
    $installed = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python") -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending
    foreach ($directory in $installed) { $candidates += , @((Join-Path $directory.FullName "python.exe")) }

    foreach ($command in $candidates) {
        if ($command[0] -like "*\*" -and -not (Test-Path $command[0])) { continue }
        if ($command[0] -notlike "*\*" -and -not (Get-Command $command[0] -ErrorAction SilentlyContinue)) { continue }
        $found = Get-PythonVersion $command
        if ($found -and (-not $Version -or $found -eq $Version)) {
            return @{ Command = $command; Version = $found }
        }
    }
    return $null
}

function Find-Tesseract {
    # Return the path of tesseract.exe: the USB copy first, then installed copies.
    $candidates = @(
        $env:TESSERACT_CMD,
        (Join-Path $VendorDir "tesseract\tesseract.exe"),
        (Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Tesseract-OCR\tesseract.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Tesseract-OCR\tesseract.exe")
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path -PathType Leaf)) { return (Resolve-Path $path).Path }
    }
    $onPath = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

function Find-Poppler {
    # Return the folder that holds pdftoppm.exe: the USB copy first, then installed copies.
    $candidates = @($env:POPPLER_PATH, (Join-Path $VendorDir "poppler"), "C:\poppler\Library\bin")
    $candidates += Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Directory -Filter "oschwartz10612.Poppler*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Directory -Filter "poppler-*" -ErrorAction SilentlyContinue } |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "Library\bin" }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path (Join-Path $path "pdftoppm.exe"))) { return (Resolve-Path $path).Path }
    }
    $onPath = Get-Command pdftoppm -ErrorAction SilentlyContinue
    if ($onPath) { return (Split-Path $onPath.Source) }
    return $null
}

function Set-ToolEnvironment([string]$Tesseract, [string]$Poppler) {
    # The engine reads these variables first, so tools work even when they are not on PATH.
    if ($Tesseract) { $env:TESSERACT_CMD = $Tesseract }
    if ($Poppler) { $env:POPPLER_PATH = $Poppler }
}

function Test-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}
