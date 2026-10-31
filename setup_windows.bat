@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error
echo.
echo Setup complete! Run start.bat.
pause
exit /b 0
:error
echo.
echo Setup failed. Check Python 3.10+, network access and GitHub access.
pause
exit /b 1
