#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

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
