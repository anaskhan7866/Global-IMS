#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input

# Handle pre-existing PostgreSQL schema on Render / Neon safely
python manage.py migrate contenttypes --fake || true
python manage.py migrate auth --fake || true
python manage.py migrate admin --fake || true
python manage.py migrate sessions --fake || true
python manage.py migrate --fake-initial || python manage.py migrate --fake || true

# Configure requested superuser admin
SUPERUSER_PASS="${DJANGO_SUPERUSER_PASSWORD:-azsxd12345}"
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
u, _ = User.objects.get_or_create(username='admin')
u.set_password('${SUPERUSER_PASS}')
u.is_superuser = True
u.is_staff = True
u.email = ''
u.save()
print('Superuser admin successfully configured.')
"
