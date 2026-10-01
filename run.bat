@echo off
rem Запуск прототипа под Windows: http://127.0.0.1:8080
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8080
