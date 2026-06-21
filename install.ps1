# TopDock Windows Installer
# Run in PowerShell: iwr -useb https://raw.githubusercontent.com/yourname/topdock/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  ⚡ TopDock Installer" -ForegroundColor Magenta
Write-Host ""

# check Python
try {
    $pyver = python --version 2>&1
    Write-Host "✔ $pyver found" -ForegroundColor Green
} catch {
    Write-Host "✗ Python not found. Install from: https://python.org" -ForegroundColor Red
    exit 1
}

# check Docker
try {
    docker version | Out-Null
    Write-Host "✔ Docker found" -ForegroundColor Green
} catch {
    Write-Host "✗ Docker not found. Install Docker Desktop: https://docs.docker.com/desktop/windows/" -ForegroundColor Red
    exit 1
}

# install via pipx or pip
if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Host "→ Installing via pipx..." -ForegroundColor Cyan
    pipx install topdock
} else {
    Write-Host "→ Installing via pip..." -ForegroundColor Cyan
    pip install topdock
}

Write-Host ""
Write-Host "✔ Done! Run:  topdock" -ForegroundColor Green
Write-Host "  Note: Use Windows Terminal or WSL2 for best experience." -ForegroundColor Cyan
