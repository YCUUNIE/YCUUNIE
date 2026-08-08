@echo off
REM Start backend + frontend together (Windows).
cd /d "%~dp0.."

if not exist ".venv" (
  echo Creating Python venv...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r backend\requirements.txt

if not exist "frontend\node_modules" (
  echo Installing frontend deps...
  cmd /c npm --prefix frontend install
)

echo Starting backend on http://127.0.0.1:8000 ...
start "ai-world-backend" cmd /c "python -m backend.app.main"

echo Starting frontend on http://127.0.0.1:5173 ...
cmd /c npm --prefix frontend run dev
