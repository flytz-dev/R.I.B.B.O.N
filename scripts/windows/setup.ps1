<#
Install everything RIBBON needs on a Windows PC, without administrator rights when possible.

Uses the copies in vendor\ when the project came from a USB drive prepared with
prepare-usb.bat (Python installer, packages, Tesseract, Poppler); otherwise installs
from the internet. Run it through setup.bat in the project folder.
#>

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $ProjectRoot

Write-Host ""
Write-Host "R.I.B.B.O.N. - instalação" -ForegroundColor White
Write-Host "Pasta do projeto: $ProjectRoot"

$problems = @()

# ---------------------------------------------------------------- Python
Write-Step "Python"
$wheelsDir = Join-Path $VendorDir "wheels"
$installer = Get-ChildItem $VendorDir -Filter "python-*-amd64.exe" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1

# Packages on the USB drive only fit the Python version they were downloaded for.
$wantedVersion = ""
if ($installer -and $installer.Name -match '^python-(\d+\.\d+)') { $wantedVersion = $Matches[1] }

$python = $null
if ($wantedVersion) { $python = Find-Python $wantedVersion }
if (-not $python -and $installer) {
    Write-Host "    Instalando $($installer.Name) só para este usuário (não precisa de administrador)..."
    Start-Process $installer.FullName -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=0 Shortcuts=0" -Wait
    $python = Find-Python $wantedVersion
}
if (-not $python) { $python = Find-Python }
if (-not $python -and (Test-Winget)) {
    Write-Host "    Python não encontrado. Tentando instalar com o winget..."
    winget install --id Python.Python.3.13 --exact --scope user
    $python = Find-Python
}
if (-not $python) {
    Write-Fail "Python 3.10 ou mais novo não foi encontrado."
    Write-Host "    Instale pelo site python.org (Downloads > Windows installer 64-bit)."
    Write-Host "    Na instalação, marque 'Add python.exe to PATH' e não marque 'for all users'."
    Write-Host "    Depois rode o setup.bat de novo."
    exit 1
}
Write-Ok "Python $($python.Version) ($($python.Command -join ' '))"

# ---------------------------------------------------------------- Virtual environment
Write-Step "Ambiente virtual (.venv)"
$venvWorks = (Test-Path $VenvPython) -and (Get-PythonVersion @($VenvPython))
if ($venvWorks) {
    Write-Ok "Já existe e funciona."
} else {
    if (Test-Path (Join-Path $ProjectRoot ".venv")) {
        Write-Warn "A .venv existente não funciona neste PC (provavelmente veio de outro computador). Recriando..."
    }
    $exe = $python.Command[0]
    $arguments = @($python.Command | Select-Object -Skip 1) + @("-m", "venv", "--clear", ".venv")
    & $exe @arguments
    if (-not (Test-Path $VenvPython)) {
        Write-Fail "Não foi possível criar a .venv."
        exit 1
    }
    Write-Ok "Criada."
}

# ---------------------------------------------------------------- Packages
Write-Step "Bibliotecas Python (requirements.txt)"
$installed = $false
if (Test-Path $wheelsDir) {
    Write-Host "    Instalando a partir do pendrive (sem internet)..."
    & $VenvPython -m pip install --disable-pip-version-check --no-index --find-links $wheelsDir -r requirements.txt
    $installed = ($LASTEXITCODE -eq 0)
    if (-not $installed) { Write-Warn "Os pacotes do pendrive não servem para este Python. Tentando pela internet..." }
}
if (-not $installed) {
    & $VenvPython -m pip install --disable-pip-version-check -r requirements.txt
    $installed = ($LASTEXITCODE -eq 0)
}
if (-not $installed) {
    Write-Fail "Não foi possível instalar as bibliotecas. Verifique a internet ou use um pendrive preparado com prepare-usb.bat."
    exit 1
}
Write-Ok "Instaladas."

# ---------------------------------------------------------------- Tesseract
Write-Step "Tesseract OCR"
$tesseract = Find-Tesseract
if (-not $tesseract -and (Test-Winget)) {
    Write-Host "    Tesseract não encontrado. Tentando instalar com o winget (pode pedir administrador)..."
    winget install --id UB-Mannheim.TesseractOCR --exact
    $tesseract = Find-Tesseract
}
if ($tesseract) {
    Write-Ok $tesseract
} else {
    Write-Fail "Tesseract não encontrado."
    Write-Host "    Leve a pasta vendor\tesseract no pendrive (prepare-usb.bat), ou instale:"
    Write-Host "    https://github.com/UB-Mannheim/tesseract/wiki"
    $problems += "Tesseract"
}

# ---------------------------------------------------------------- Poppler
Write-Step "Poppler"
$poppler = Find-Poppler
if (-not $poppler -and (Test-Winget)) {
    Write-Host "    Poppler não encontrado. Tentando instalar com o winget (não precisa de administrador)..."
    winget install --id oschwartz10612.Poppler --exact
    $poppler = Find-Poppler
}
if ($poppler) {
    Write-Ok $poppler
} else {
    Write-Fail "Poppler não encontrado."
    Write-Host "    Leve a pasta vendor\poppler no pendrive (prepare-usb.bat), ou baixe e extraia:"
    Write-Host "    https://github.com/oschwartz10612/poppler-windows/releases"
    $problems += "Poppler"
}

# ---------------------------------------------------------------- Real OCR test
if ($problems.Count -eq 0) {
    Write-Step "Teste rápido: lendo uma guia sintética"
    Set-ToolEnvironment $tesseract $poppler
    & $VenvPython -m tools.check_install
    if ($LASTEXITCODE -eq 0) { Write-Ok "O OCR funciona neste PC." } else { $problems += "teste do OCR" }
}

# ---------------------------------------------------------------- Summary
Write-Host ""
if ($problems.Count -gt 0) {
    Write-Host "Faltou: $($problems -join ', '). Resolva os itens acima e rode o setup.bat de novo." -ForegroundColor Red
    exit 1
}

Write-Host "Tudo pronto!" -ForegroundColor Green
Write-Host ""
Write-Host "Para abrir o servidor, dê dois cliques em start-server.bat" -ForegroundColor White
Write-Host "(ele abre o navegador sozinho em http://127.0.0.1:8000)."
Write-Host ""
Write-Host "Ou, no PowerShell, dentro da pasta do projeto:"
Write-Host "    `$env:TESSERACT_CMD = `"$tesseract`""
Write-Host "    `$env:POPPLER_PATH = `"$poppler`""
Write-Host "    .\.venv\Scripts\python.exe -m uvicorn api.main:app"
Write-Host "e abra http://127.0.0.1:8000 no navegador. Para parar o servidor: Ctrl+C."
exit 0
