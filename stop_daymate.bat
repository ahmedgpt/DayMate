@echo off
rem Stops the DayMate server, the Telegram bot and n8n. Data (diary, reminders, n8n workflows) is kept.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'app\.main (server|bot)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
docker compose stop
echo DayMate stopped.
