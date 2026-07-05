# Build a standalone Windows app: dist\BridgeMCSimulator.exe
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"

python -m pip install --upgrade pyinstaller PySide6 anthropic "git+https://github.com/anntzer/redeal"

# QtWebEngine ships a Chromium helper process + resources/locales, so build a
# one-folder app (not --onefile): onefile must unpack ~150 MB on every launch
# and the sandboxed render process is unreliable from a temp dir.
python -m PyInstaller --noconfirm --onedir --windowed --name BridgeMCSimulator `
    --collect-all redeal --collect-all anthropic --collect-all PySide6 `
    --collect-data bridge_mc bridge_sim_gui.py

Write-Host "`nBuilt: dist\BridgeMCSimulator\BridgeMCSimulator.exe" -ForegroundColor Green
