#!/bin/sh
set -e

echo "Waiting for database..."
python - <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
for _ in range(60):
    try:
        with create_engine(url).connect() as conn:
            conn.execute(text("SELECT 1"))
        break
    except Exception:
        time.sleep(1)
else:
    print("Database did not become ready", file=sys.stderr)
    sys.exit(1)
print("Database is ready")
PY

echo "Running migrations..."
alembic upgrade head

echo "Seeding demo data and Markdown knowledge..."
python -m scripts.seed_demo

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
