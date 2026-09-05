@echo off
cd /d "%~dp0"
if "%DONN_PORT%"=="" set DONN_PORT=3666
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if "%1"=="load" (
  .venv\Scripts\python.exe -m scripts.load_products --source finlife --groups 020000,030300
  .venv\Scripts\python.exe -m scripts.load_products --source datago
  goto :eof
)
.venv\Scripts\python.exe -m uvicorn app.main:app --port %DONN_PORT%
