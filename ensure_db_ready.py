import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core_erp.settings')
django.setup()

from django.db import connection
from django.core.management import call_command
from django.contrib.auth import get_user_model

def ensure_tables():
    print("Checking database tables...")
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public';
            """)
            existing_tables = set(row[0] for row in cursor.fetchall())
        else:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = set(row[0] for row in cursor.fetchall())

    print(f"Total existing tables found: {len(existing_tables)}")

    # 1. Handle contenttypes (faked because legacy 'name' column was dropped)
    if 'django_content_type' in existing_tables:
        print("django_content_type table exists. Ensuring contenttypes migration is recorded.")
        try:
            call_command('migrate', 'contenttypes', '--fake')
        except Exception as e:
            print(f"contenttypes fake notice: {e}")

    # 2. Handle auth
    if 'auth_user' in existing_tables:
        print("auth_user table exists. Ensuring auth migration is recorded.")
        try:
            call_command('migrate', 'auth', '--fake')
        except Exception as e:
            print(f"auth fake notice: {e}")

    # 3. Handle admin
    if 'django_admin_log' in existing_tables:
        print("django_admin_log table exists. Ensuring admin migration is recorded.")
        try:
            call_command('migrate', 'admin', '--fake')
        except Exception as e:
            print(f"admin fake notice: {e}")

    # 4. Handle sessions - CRITICAL FOR LOGIN!
    if 'django_session' not in existing_tables:
        print("WARNING: django_session table is MISSING! Creating django_session now...")
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute("DELETE FROM django_migrations WHERE app = 'sessions';")
        call_command('migrate', 'sessions')
        print("django_session table created successfully!")
    else:
        print("django_session table exists.")
        try:
            call_command('migrate', 'sessions', '--fake')
        except Exception:
            pass

    # 5. Run remaining app migrations
    print("Running migrations for inventory and transactions...")
    try:
        call_command('migrate', '--fake-initial')
    except Exception as e:
        print(f"migrate --fake-initial notice: {e}, falling back to selective fake")
        try:
            call_command('migrate', '--fake')
        except Exception as e2:
            print(f"migrate --fake notice: {e2}")

    # 6. Configure admin superuser
    superuser_password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'azsxd12345')
    User = get_user_model()
    admin_user, created = User.objects.get_or_create(username='admin')
    admin_user.set_password(superuser_password)
    admin_user.is_superuser = True
    admin_user.is_staff = True
    admin_user.is_active = True
    admin_user.email = ''
    admin_user.save()
    print(f"Superuser 'admin' successfully {'created' if created else 'updated'} with password '{superuser_password}'.")

if __name__ == '__main__':
    ensure_tables()
