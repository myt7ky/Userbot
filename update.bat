@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Обновление бота с GitHub
echo   .env, *.session и базу (usernames.db) НЕ трогаю
echo ============================================
echo.
echo Если бот сейчас запущен - закройте его окно (или Ctrl+C), потом нажмите любую клавишу.
pause >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$url = 'https://github.com/myt7ky/Userbot/archive/refs/heads/claude/intelligent-edison-a6xbh3.zip';" ^
  "$zip = Join-Path $env:TEMP 'userbot_update.zip';" ^
  "$tmp = Join-Path $env:TEMP 'userbot_update';" ^
  "if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force };" ^
  "Write-Host 'Скачиваю...';" ^
  "Invoke-WebRequest $url -OutFile $zip -UseBasicParsing;" ^
  "Expand-Archive $zip $tmp -Force;" ^
  "$src = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName;" ^
  "robocopy $src (Get-Location).Path /E /XF .env *.session *.session-journal *.db update.bat /XD .git /NFL /NDL /NJH /NJS /NP | Out-Null;" ^
  "if ($LASTEXITCODE -ge 8) { throw 'не удалось скопировать файлы' };" ^
  "Remove-Item $zip, $tmp -Recurse -Force;" ^
  "exit 0"
if errorlevel 1 (
  echo.
  echo [ОШИБКА] Не получилось обновить. Пришлите скриншот этого окна.
  pause
  exit /b 1
)

echo Обновляю библиотеки...
python -m pip install -q -r requirements.txt
echo.
echo Готово! Бот обновлён. Запускаю...
echo.
python -m userhunter
pause
