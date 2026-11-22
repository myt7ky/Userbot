@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m userhunter
echo.
echo Бот остановлен.
pause
