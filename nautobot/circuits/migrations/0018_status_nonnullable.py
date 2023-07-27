# Generated by Django 3.2.18 on 2023-05-19 18:05

from django.db import migrations
import django.db.models.deletion
import nautobot.extras.models.statuses


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0084_rename_computed_field_slug_to_key"),
        ("circuits", "0017_fixup_null_statuses"),
    ]

    operations = [
        migrations.AlterField(
            model_name="circuit",
            name="status",
            field=nautobot.extras.models.statuses.StatusField(
                on_delete=django.db.models.deletion.PROTECT, related_name="circuits", to="extras.status"
            ),
        ),
    ]