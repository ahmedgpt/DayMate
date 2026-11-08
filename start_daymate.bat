@echo off
rem Starts DayMate: n8n (Docker), the MCP server + REST API, and the Telegram bot.
rem Close the two DayMate windows (or run stop_daymate.bat) to stop.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo .venv not found in %cd% & pause & exit /b 1)
if not exist ".env" (echo .env not found. Copy .env.example to .env and fill it in. & pause & exit /b 1)

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says "Engine running", then run this again.
  pause & exit /b 1
)
echo Starting n8n...
docker compose up -d

echo Starting DayMate server...
start "DayMate server" cmd /k ".venv\Scripts\python.exe -m app.main server"
timeout /t 5 /nobreak >nul

echo Starting Telegram bot...
start "DayMate bot" cmd /k ".venv\Scripts\python.exe -m app.main bot"

echo.
echo DayMate is running.  n8n: http://localhost:5678   Server health: http://127.0.0.1:8000/health
