@echo off
REM Double-click to launch the Bridge MC Simulator GUI (no console window).
REM Finds a windowed Python: PATH pythonw -> py launcher -> per-user install.
setlocal
set "SCRIPT=%~dp0bridge_sim_gui.py"

where pythonw >nul 2>&1 && (start "" pythonw "%SCRIPT%" & goto :eof)
where pyw     >nul 2>&1 && (start "" pyw "%SCRIPT%" & goto :eof)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (
  start "" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" "%SCRIPT%" & goto :eof
)
where python >nul 2>&1 && (start "" python "%SCRIPT%" & goto :eof)

echo Could not find Python. Install it, or run:  python "%SCRIPT%"
pause
