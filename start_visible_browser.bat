@echo off
chcp 65001 >nul
cd /d "%~dp0"
set AE_HEADLESS=0
call start.bat
