@echo off
setlocal
cd /d "%~dp0"

echo Opening dedicated ConnectPilot browser profile for one-time LinkedIn sign-in...
echo Sign into LinkedIn once, wait for your feed to load, and then close that browser window.

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0cli.py" browser-login
    goto :eof
)

set "PROFILE_DIR=%~dp0edge_profile"
set "EDGE_EXE="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_EXE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE_EXE if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "EDGE_EXE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE_EXE if exist "%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe" set "EDGE_EXE=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"

if not defined EDGE_EXE (
    echo [ERROR] Could not auto-detect Microsoft Edge. Configure EDGE_BINARY_PATH in .env or run: python cli.py browser-login
    exit /b 1
)

start "" "%EDGE_EXE%" --user-data-dir="%PROFILE_DIR%" --profile-directory=Default "https://www.linkedin.com/login"
