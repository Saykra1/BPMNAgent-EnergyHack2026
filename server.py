"""Точка входа из корня репозитория:  python -m uvicorn server:app --port 8080"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.main import app  # noqa: E402,F401
