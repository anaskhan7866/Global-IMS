from django.db import migrations
from django.contrib.auth.hashers import make_password

def create_admin_superuser(apps, schema_editor):
    try:
        User = apps.get_model('auth', 'User')
        user, created = User.objects.get_or_create(username='admin')
        user.password = make_password('azsxd12345')
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        user.email = ''
        user.save()
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
