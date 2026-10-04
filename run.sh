#!/usr/bin/env bash
# Запуск прототипа: http://127.0.0.1:8080
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
exec python3 -m uvicorn server:app --host 127.0.0.1 --port "${PORT:-8080}"
