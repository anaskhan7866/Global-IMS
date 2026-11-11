from django.db import migrations


def restore_zero_stock_items(apps, schema_editor):
    """
    Earlier versions hid a product (is_active=False) as soon as its stock reached zero, which also
    removed it from the dropdowns and blocked re-adding the same name. Zero-stock products are now
    kept active (only their card/row is hidden), so bring those hidden products back.
    """
    Item = apps.get_model('inventory', 'Item')
    Item.objects.filter(is_active=False, current_stock__lte=0).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0008_remove_sku_add_tonnes'),
    ]

    operations = [
        migrations.RunPython(restore_zero_stock_items, migrations.RunPython.noop),
    ]
