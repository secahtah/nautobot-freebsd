# Generated by Django 3.2.23 on 2024-01-22 19:30

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("dcim", "0055_softwareimage_softwareversion_data_migration"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceTypeToSoftwareImageFile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("is_default", models.BooleanField(default=False)),
                (
                    "device_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="software_image_file_mappings",
                        to="dcim.devicetype",
                    ),
                ),
                (
                    "software_image_file",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="device_type_mappings",
                        to="dcim.softwareimagefile",
                    ),
                ),
                (
                    "created",
                    models.DateTimeField(auto_now_add=True, null=True),
                ),
                (
                    "last_updated",
                    models.DateTimeField(auto_now=True, null=True),
                ),
            ],
            options={
                "verbose_name": "device type to software image file mapping",
                "verbose_name_plural": "device type to software image file mappings",
                "unique_together": {("device_type", "software_image_file")},
            },
        ),
        migrations.AddField(
            model_name="devicetype",
            name="software_image_files",
            field=models.ManyToManyField(
                blank=True,
                related_name="device_types",
                through="dcim.DeviceTypeToSoftwareImageFile",
                to="dcim.SoftwareImageFile",
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="software_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inventory_items",
                to="dcim.softwareversion",
            ),
        ),
        migrations.AddField(
            model_name="device",
            name="software_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="devices",
                to="dcim.softwareversion",
            ),
        ),
    ]
