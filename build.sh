#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input

# Run intelligent database reconciliation and configure superuser
python ensure_db_ready.py
