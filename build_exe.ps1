param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if ($Clean) {
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
    Remove-Item -Force FastCAD-Component-Reviewer.spec -ErrorAction SilentlyContinue
}

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "PyInstaller not found. Installing..."
    python -m pip install pyinstaller
}

python -m PyInstaller `
    --name "FastCADreviewer" `
    --noconsole `
    --onefile `
    --icon "fastCADreview.ico" `
    --add-data "fastCADreview.ico;." `
    "main.py"

Write-Host "Build complete: dist\FastCADreviewer.exe"
