Param()

$ErrorActionPreference = "Stop"

$WardRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $WardRoot

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created $WardRoot\.env from .env.example"
}

& "$WardRoot\install-windows.ps1"

Write-Host "Checking ward configuration..."
& "$WardRoot\.venv\Scripts\python.exe" "$WardRoot\ward_cli.py" config

Write-Host "Deploy bootstrap complete."
