from django.db import migrations


def migrate_dlm_software_models_to_core(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    DLMSoftwareVersion = apps.get_model("nautobot_device_lifecycle_mgmt", "SoftwareLCM")
    DLMSoftwareImage = apps.get_model("nautobot_device_lifecycle_mgmt", "SoftwareImageLCM")
    CoreSoftwareVersion = apps.get_model("dcim", "SoftwareVersion")
    CoreSoftwareImage = apps.get_model("dcim", "SoftwareImageFile")
    Device = apps.get_model("dcim", "Device")
    InventoryItem = apps.get_model("dcim", "InventoryItem")
    RelationshipAssociation = apps.get_model("extras", "RelationshipAssociation")
    Status = apps.get_model("extras", "Status")
    Tag = apps.get_model("extras", "Tag")

    # Migrate tag and status content types
    dlm_software_version_ct = ContentType.objects.get_for_model(DLMSoftwareVersion)
    dlm_software_image_ct = ContentType.objects.get_for_model(DLMSoftwareImage)
    core_software_version_ct = ContentType.objects.get_for_model(CoreSoftwareVersion)
    core_software_image_ct = ContentType.objects.get_for_model(CoreSoftwareImage)
    for status in Status.objects.filter(content_types=dlm_software_version_ct):
        status.content_types.add(core_software_version_ct)
    for status in Status.objects.filter(content_types=dlm_software_image_ct):
        status.content_types.add(core_software_image_ct)
    for tag in Tag.objects.filter(content_types=dlm_software_version_ct):
        tag.content_types.add(core_software_version_ct)
    for tag in Tag.objects.filter(content_types=dlm_software_image_ct):
        tag.content_types.add(core_software_image_ct)

    status_active = Status.objects.get(name="Active")

    # Migrate nautobot_device_lifecycle_mgmt.SoftwareLCM instances to dcim.SoftwareVersion
    for dlm_software_version in DLMSoftwareVersion.objects.all():
        core_software_version = CoreSoftwareVersion(
            platform=dlm_software_version.device_platform,
            version=dlm_software_version.version,
            alias=dlm_software_version.alias,
            release_date=dlm_software_version.release_date,
            end_of_support_date=dlm_software_version.end_of_support,
            documentation_url=dlm_software_version.documentation_url,
            long_term_support=dlm_software_version.long_term_support,
            pre_release=dlm_software_version.pre_release,
            status=status_active,
        )
        core_software_version.save()
        core_software_version.refresh_from_db()
        _migrate_tags(
            apps,
            dlm_software_version,
            core_software_version,
            dlm_software_version_ct,
            core_software_version_ct,
        )

        # TODO: Migrate notes
        # TODO: Migrate custom fields
        # TODO: Migrate created and last updated timestamps
        # TODO: Migrate primary keys

        # Migrate "Software on Device" relationships to the Device.software_version foreign key
        for relationship_association in RelationshipAssociation.objects.filter(
            relationship__key="device_soft", source_id=dlm_software_version.id
        ):
            device = Device.objects.get(id=relationship_association.destination_id)
            device.software_version = core_software_version
            device.save()

        # Migrate "Software on InventoryItem" relationships to the InventoryItem.software_version foreign key
        for relationship_association in RelationshipAssociation.objects.filter(
            relationship__key="inventory_item_soft", source_id=dlm_software_version.id
        ):
            inventory_item = InventoryItem.objects.get(id=relationship_association.destination_id)
            inventory_item.software_version = core_software_version
            inventory_item.save()


def _migrate_tags(apps, old_instance, new_instance, old_ct, new_ct):
    TaggedItem = apps.get_model("extras", "TaggedItem")
    for old_tagged_item in TaggedItem.objects.filter(content_type=old_ct, object_id=old_instance.id):
        # DLM forms are using a custom tag field that doesn't enforce content type. Fix that here if necessary.
        if not old_tagged_item.tag.content_types.filter(id=new_ct.id).exists():
            old_tagged_item.tag.content_types.add(new_ct)

        TaggedItem.objects.create(
            content_type=new_ct,
            object_id=new_instance.id,
            tag=old_tagged_item.tag,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("dcim", "0056_softwareimage_m2m_and_device_fk"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(migrate_dlm_software_models_to_core, migrations.RunPython.noop),
        # migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
