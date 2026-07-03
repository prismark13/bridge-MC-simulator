# Build a standalone Windows app: dist\BridgeMCSimulator.exe
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"

python -m pip install --upgrade pyinstaller sv-ttk anthropic "git+https://github.com/anntzer/redeal"

python -m PyInstaller --noconfirm --onefile --windowed --name BridgeMCSimulator `
    --collect-all redeal --collect-all sv_ttk --collect-all anthropic bridge_sim_gui.py

Write-Host "`nBuilt: dist\BridgeMCSimulator.exe" -ForegroundColor Green
