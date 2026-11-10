import os

from django.db import migrations
from django.contrib.auth.hashers import make_password


def create_admin_superuser(apps, schema_editor):
    """
    Create the initial 'admin' superuser ONLY when DJANGO_SUPERUSER_PASSWORD is provided in
    the environment. No password is hardcoded here: a password committed to the repository
    is known to everyone who can read the code. Without the variable, create the first
    login with:  python manage.py createsuperuser
    An existing 'admin' account is never modified.
    """
    password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '').strip()
    if not password:
        return
    try:
        User = apps.get_model('auth', 'User')
        if User.objects.filter(username='admin').exists():
            return
        User.objects.create(
            username='admin',
            password=make_password(password),
            is_superuser=True,
            is_staff=True,
            is_active=True,
            email='',
        )
    except Exception as e:
        print(f"Notice: superuser creation in migration skipped: {e}")


def reverse_admin_superuser(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_company_dailycoreboxdispatch'),
    ]

    operations = [
        migrations.RunPython(create_admin_superuser, reverse_admin_superuser),
    ]
