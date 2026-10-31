@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run setup_windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u diagnostic_runner.py
set "APP_EXIT_CODE=%ERRORLEVEL%"
echo.
echo Diagnostic logs: alibulkfinder_debug.log and alibulkfinder_console.log
echo Each log: max 5 MiB + 3 rotated copies (up to ~20 MiB per log).
echo Review logs before sharing: they may contain search keywords and server output.
pause
exit /b %APP_EXIT_CODE%
