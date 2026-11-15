#!/bin/sh
# تشغيل بأمر واحد: يجهّز البيئة والداتابيز ثم يشغّل التطبيق.
set -e
cd "$(dirname "$0")"

[ -d .venv ] || "${PYTHON:-python3}" -m venv .venv
. .venv/bin/activate
python -m pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi
set -a
. ./.env
set +a

pg_isready -q || { echo "PostgreSQL is not running. Start it and try again."; exit 1; }
DB_NAME="${DATABASE_URL##*/}"
if ! psql -lqt | cut -d '|' -f 1 | grep -qw "$DB_NAME"; then
  createdb "$DB_NAME"
fi

export FLASK_APP=app
flask db upgrade
flask seed
exec gunicorn -b "127.0.0.1:${PORT:-8000}" -w 2 --timeout 30 --access-logfile - "app:create_app()"