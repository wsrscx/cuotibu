$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> Generating icon"
python scripts/make_icon.py

Write-Host "==> Running tests"
python -m unittest discover -s tests -v

Write-Host "==> Building single-file executable"
python -m PyInstaller --clean --noconfirm app.spec

$exe = Join-Path (Get-Location) "dist\Cuotibu.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Build finished without dist\Cuotibu.exe"
}

$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built: $exe ($size MB)"
Write-Host "Run it from any writable folder. Data will be created in a 'data' folder next to the exe."
