@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run setup_windows.bat first.
  pause
  exit /b 1
)
set PYTHONUNBUFFERED=1
echo.>> "alibulkfinder_console.log"
echo === START %date% %time% ===>> "alibulkfinder_console.log"
".venv\Scripts\python.exe" app.py >> "alibulkfinder_console.log" 2>&1
echo.
echo Diagnostic logs: alibulkfinder_debug.log and alibulkfinder_console.log
echo Review logs before sharing: they may contain search keywords and server output.
pause
