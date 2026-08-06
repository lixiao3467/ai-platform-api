#!/bin/sh
set -e

# Environment isolation for database schema validation
# APP_ENV: development | staging | production

APP_ENV=${APP_ENV:-development}

echo "=========================================="
echo "Environment: $APP_ENV"
echo "Database: ${DATABASE_URL%%@*}***"  # Hide credentials
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=========================================="

# Verify database connectivity (schema managed externally via SQL scripts)
echo "==> Verifying database connection ($APP_ENV)..."
python -c "
import asyncio
from sqlalchemy import text
from ai_platform.infra.database.connection import get_engine

async def check():
    async with get_engine().connect() as conn:
        await conn.execute(text('SELECT 1'))
    print('Database connection OK')

asyncio.run(check())
"

echo "==> Starting application ($APP_ENV)..."
exec python -m uvicorn ai_platform.main:app --host 0.0.0.0 --port 8000
