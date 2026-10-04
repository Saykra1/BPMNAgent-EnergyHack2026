@echo off
rem Запуск прототипа под Windows: http://127.0.0.1:8080
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8080
