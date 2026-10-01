#!/usr/bin/env bash
# One command: migrate schema, then serve. (Seed demo data separately: python -m scripts.seed)
set -euo pipefail
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
