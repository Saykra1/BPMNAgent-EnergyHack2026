#!/usr/bin/env bash
# Запуск прототипа: http://localhost:8000
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
cd backend && exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
