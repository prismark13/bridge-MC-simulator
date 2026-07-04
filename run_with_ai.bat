@echo off
REM Double-click launcher: loads your Anthropic API key from apikey.txt (kept
REM local, git-ignored) and starts the app with the AI "Explain" button enabled.
REM
REM SETUP (once): open apikey.txt in this folder and replace the placeholder with
REM your key (the single line that starts with sk-ant-). Save. Then double-click me.
setlocal
set "KEYFILE=%~dp0apikey.txt"
set "ANTHROPIC_API_KEY="
if exist "%KEYFILE%" set /p ANTHROPIC_API_KEY=<"%KEYFILE%"

echo %ANTHROPIC_API_KEY% | findstr /b "sk-ant-" >nul
if errorlevel 1 (
  echo.
  echo   No valid key found in apikey.txt -- the AI "Explain" button will be OFF.
  echo   Paste your key ^(starts with sk-ant-^) as the only line in apikey.txt.
  echo   The simulator itself still works fully without a key.
  echo.
  set "ANTHROPIC_API_KEY="
) else (
  echo   API key loaded -- AI "Explain" is enabled.
)

start "" "%~dp0dist\BridgeMCSimulator.exe"
endlocal
