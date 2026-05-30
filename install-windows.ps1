Param()

$ErrorActionPreference = "Stop"

$WardRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $WardRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "python was not found on PATH. Install Python 3.11 first."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Warning "ffmpeg was not found on PATH. Install it before running STT."
}

Write-Host "Creating runtime virtual environment..."
python -m venv .venv
$Python = Join-Path $WardRoot ".venv\Scripts\python.exe"

Write-Host "Upgrading pip..."
& $Python -m pip install --upgrade pip

Write-Host "Installing Python dependencies..."
& $Python -m pip install -r requirements.txt

Write-Host "Installing Playwright Chromium for OpenEvidence browser mode"
& $Python -m playwright install chromium

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example"
}

New-Item -ItemType Directory -Force -Path data\incoming_audio, data\output, data\logs, data\case_views, data\obsidian_vault, data\stt_review_queue, data\ward_audio_archive | Out-Null

Write-Host "Install complete."
Write-Host "Edit $WardRoot\.env, then run: .\.venv\Scripts\python.exe ward_cli.py config"
