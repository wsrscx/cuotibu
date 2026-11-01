$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> Generating icon"
python scripts/make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed" }

Write-Host "==> Running tests"
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed; build aborted" }

Write-Host "==> Building single-file executable"
python -m PyInstaller --clean --noconfirm app.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path (Get-Location) "dist\Cuotibu.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Build finished without dist\Cuotibu.exe"
}

$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built: $exe ($size MB)"
Write-Host "Run it from any writable folder. Data will be created in a 'data' folder next to the exe."
