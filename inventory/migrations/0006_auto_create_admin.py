from django.db import migrations
from django.contrib.auth.hashers import make_password

def create_admin_superuser(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    user, created = User.objects.get_or_create(username='admin')
    user.password = make_password('azsxd12345')
    user.is_superuser = True
    user.is_staff = True
    user.is_active = True
    user.email = ''
    user.save()

def reverse_admin_superuser(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_company_dailycoreboxdispatch'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_admin_superuser, reverse_admin_superuser),
    ]

