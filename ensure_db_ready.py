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

    # CASE 1: Fresh / Empty Database
    # When no tables or only django_migrations exists, run standard migrations cleanly
    if 'django_migrations' not in existing_tables and len(existing_tables) <= 1:
        print("Fresh database detected. Applying all migrations from scratch...")
        call_command('migrate')
    else:
        # CASE 2: Pre-existing database schema (e.g. legacy or imported tables)
        print("Existing database schema detected. Reconciling migrations safely...")

        # If contenttypes table exists, fake it to prevent 'name' column alteration failure
        if 'django_content_type' in existing_tables:
            try:
                call_command('migrate', 'contenttypes', '--fake')
            except Exception as e:
                print(f"contenttypes fake notice: {e}")

        # If auth_user already exists, fake auth
        if 'auth_user' in existing_tables:
            try:
                call_command('migrate', 'auth', '--fake')
            except Exception as e:
                print(f"auth fake notice: {e}")

        # If django_admin_log already exists, fake admin
        if 'django_admin_log' in existing_tables:
            try:
                call_command('migrate', 'admin', '--fake')
            except Exception as e:
                print(f"admin fake notice: {e}")

        # Ensure django_session table exists
        if 'django_session' not in existing_tables:
            print("Creating missing django_session...")
            if 'django_migrations' in existing_tables:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("DELETE FROM django_migrations WHERE app = 'sessions';")
                except Exception:
                    pass
            call_command('migrate', 'sessions')
        else:
            try:
                call_command('migrate', 'sessions', '--fake')
            except Exception:
                pass

        # Apply all remaining migrations with --fake-initial
        try:
            call_command('migrate', '--fake-initial')
        except Exception as e:
            print(f"migrate --fake-initial notice: {e}")
            try:
                call_command('migrate', '--fake')
            except Exception:
                pass

    # Ensure superuser admin is created/updated
    print("Configuring superuser admin...")
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
