@echo off
rem Double-click or run from anywhere. Runs the live Groq check from the project folder.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Could not find .venv in %cd%
  echo Create it with:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
if not exist ".env" (
  echo No .env file found in %cd%. Copy .env.example to .env and set GROQ_API_KEY.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\check_groq.py %*
pause
