<#
Copy RIBBON and everything it needs to a USB drive or folder, to run it on another
Windows PC without internet or administrator rights. Run it at home, where the project
already works, through prepare-usb.bat:
    prepare-usb.bat E:\RIBBON
The copy holds the project files tracked by Git, plus vendor\ with the Python installer,
the Python packages, Tesseract, and Poppler. Audit history (pyconfer.db) is never copied:
it may contain taxpayer data.
#>
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [switch]$SkipPython
)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $ProjectRoot
$target = [System.IO.Path]::GetFullPath($Destination)
if ($target.TrimEnd("\").StartsWith($ProjectRoot.TrimEnd("\"), [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Fail "Escolha uma pasta fora do projeto (por exemplo E:\RIBBON)."
    exit 1
}
if (-not (Test-Path $VenvPython)) {
    Write-Fail "Rode o setup.bat neste PC primeiro: o pendrive é montado a partir de uma instalação que funciona."
    exit 1
}
Write-Host ""
Write-Host "R.I.B.B.O.N. - preparando cópia portátil em $target" -ForegroundColor White
New-Item -ItemType Directory -Force $target | Out-Null
$targetVendor = Join-Path $target "vendor"
New-Item -ItemType Directory -Force $targetVendor | Out-Null
$problems = @()
# ---------------------------------------------------------------- Project files
Write-Step "Arquivos do projeto"
$files = @()
# Committed files plus new ones not yet committed; .gitignore keeps out .venv, PDFs, and history.
if (Get-Command git -ErrorAction SilentlyContinue) { $files = @(git ls-files --cached --others --exclude-standard) }
if ($files.Count -eq 0) {
    Write-Fail "Não foi possível listar os arquivos com o Git."
    exit 1
}
foreach ($file in $files) {
    $source = Join-Path $ProjectRoot $file
    if (-not (Test-Path $source -PathType Leaf)) { continue }
    $copy = Join-Path $target $file
    New-Item -ItemType Directory -Force (Split-Path $copy) | Out-Null
    Copy-Item $source $copy -Force
}
Write-Ok "$($files.Count) arquivos copiados (sem .venv, PDFs e histórico)."
# ---------------------------------------------------------------- Tesseract
Write-Step "Tesseract"
$tesseract = Find-Tesseract
if ($tesseract) {
    $tesseractTarget = Join-Path $targetVendor "tesseract"
    New-Item -ItemType Directory -Force $tesseractTarget | Out-Null
    Copy-Item (Join-Path (Split-Path $tesseract) "*") $tesseractTarget -Recurse -Force
    Write-Ok "Copiado de $(Split-Path $tesseract)"
} else {
    Write-Fail "Tesseract não encontrado neste PC."
    $problems += "Tesseract"
}
# ---------------------------------------------------------------- Poppler
Write-Step "Poppler"
$poppler = Find-Poppler
if ($poppler) {
    $popplerTarget = Join-Path $targetVendor "poppler"
    New-Item -ItemType Directory -Force $popplerTarget | Out-Null
    Copy-Item (Join-Path $poppler "*") $popplerTarget -Recurse -Force
    Write-Ok "Copiado de $poppler"
} else {
    Write-Fail "Poppler não encontrado neste PC."
    $problems += "Poppler"
}
# ---------------------------------------------------------------- Python packages
Write-Step "Bibliotecas Python (para instalar sem internet)"
& $VenvPython -m pip download --disable-pip-version-check -r requirements.txt -d (Join-Path $targetVendor "wheels")
if ($LASTEXITCODE -eq 0) { Write-Ok "Baixadas." } else { Write-Fail "Falha ao baixar as bibliotecas."; $problems += "bibliotecas" }
# ---------------------------------------------------------------- Python installer
if (-not $SkipPython) {
    Write-Step "Instalador do Python"
    # The packages above only fit this Python version, so the drive carries the same one.
    $version = (& $VenvPython -c "import platform; print(platform.python_version())").Trim()
    $name = "python-$version-amd64.exe"
    $url = "https://www.python.org/ftp/python/$version/$name"
    try {
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest $url -OutFile (Join-Path $targetVendor $name) -UseBasicParsing
        Write-Ok "$name baixado de python.org."
    } catch {
        Write-Warn "Não foi possível baixar $url"
        Write-Host "    Baixe o 'Windows installer (64-bit)' do Python $version em python.org e salve em $targetVendor"
    }
}
# ---------------------------------------------------------------- Summary
$size = (Get-ChildItem $target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ""
if ($problems.Count -gt 0) {
    Write-Host "Cópia incompleta. Faltou: $($problems -join ', ')." -ForegroundColor Red
    exit 1
}
Write-Host ("Pronto: {0:N0} MB em {1}" -f $size, $target) -ForegroundColor Green
Write-Host ""
Write-Host "No outro PC:"
Write-Host "  1. Copie a pasta para o computador (rodar direto do pendrive também funciona, só que mais devagar)."
Write-Host "  2. Dê dois cliques em setup.bat (uma vez)."
Write-Host "  3. Dê dois cliques em start-server.bat para abrir o R.I.B.B.O.N."
exit 0
