# Generated by Django 3.2.16 on 2022-11-19 23:15

from collections import namedtuple
from django.db import migrations, models

from nautobot.core.choices import ColorChoices
from nautobot.ipam.choices import IPAddressRoleChoices

# Role Models that would be integrated into this Role model are referred to as RelatedRoleModels.
# For Example: DeviceRole, RackRole e.t.c.
# while implemented_by refers to the model that actually implements the Related Model.
# e.g role = models.ForeignKey(to=DeviceRole, ...)
RelatedRoleModel = namedtuple("RelatedRoleModel", ["model", "implemented_by"])

# Using this, Choices is made to resemble a Role Queryset.
ChoicesQuerySet = namedtuple("ChoicesQuerySet", ["name", "slug", "description", "color"])

color_map = {
    "default": ColorChoices.COLOR_GREY,
    "primary": ColorChoices.COLOR_BLUE,
    "warning": ColorChoices.COLOR_AMBER,
    "success": ColorChoices.COLOR_GREEN,
}


def create_equivalent_roles_of_virtualmachine_device_role(apps):
    """Create equivalent roles for the VirtualMachine DeviceRole."""
    DeviceRole = apps.get_model("dcim", "DeviceRole")
    roles_to_create = DeviceRole.objects.filter(vm_role=True)
    bulk_create_roles(apps, roles_to_create, ["virtualization.virtualmachine"])


def create_equivalent_roles_of_related_role_model(apps):
    """Create equivalent roles for the related role model."""
    related_role_models = (
        RelatedRoleModel("dcim.DeviceRole", ["dcim.device"]),
        RelatedRoleModel("dcim.RackRole", ["dcim.rack"]),
        RelatedRoleModel("ipam.Role", ["ipam.prefix", "ipam.vlan"]),
    )

    for related_role_model in related_role_models:
        app_name, model_class = related_role_model.model.split(".")
        related_role_model_class = apps.get_model(app_name, model_class)

        roles_to_create = related_role_model_class.objects.all()
        bulk_create_roles(apps, roles_to_create, related_role_model.implemented_by)


def create_equivalent_roles_of_ipaddress_role_choices(apps):
    """Create equivalent roles for the IPAddressRoleChoices."""
    roles_to_create = []

    for value, label in IPAddressRoleChoices.CHOICES:
        color = IPAddressRoleChoices.CSS_CLASSES[value]
        choiceset = ChoicesQuerySet(name=label, slug=value, color=color_map[color], description="")
        roles_to_create.append(choiceset)
    bulk_create_roles(apps, roles_to_create, ["ipam.ipaddress"])


def bulk_create_roles(apps, roles_to_create, content_types):
    """Bulk create role and set its contenttypes"""
    Role = apps.get_model("extras", "Role")
    ContentType = apps.get_model("contenttypes", "ContentType")
    ObjectChange = apps.get_model("extras", "ObjectChange")

    old_role_ct = None
    new_role_ct = None

    if hasattr(roles_to_create, "model"):
        old_role_ct = ContentType.objects.get_for_model(roles_to_create.model)
        # No sense fetching this if we don't have an old role content type
        new_role_ct = ContentType.objects.get_for_model(Role)

    # Exclude roles whose names are already exists from bulk create.
    existing_roles = Role.objects.values_list("name", flat=True)
    roles_instances = [
        (
            data,
            Role(
                name=data.name,
                slug=data.slug,
                description=data.description,
                color=getattr(data, "color", color_map["default"]),
                weight=getattr(data, "weight", None),
            ),
        )
        for data in roles_to_create
        if data.name not in existing_roles
    ]
    if roles_instances:
        for old_role, new_role in roles_instances:
            # TODO: We need to handle new role collisions
            new_role.save()
            if old_role_ct:
                # Move over existing object change records to the new role we created
                ObjectChange.objects.filter(changed_object_type=old_role_ct, changed_object_id=old_role.pk).update(
                    changed_object_type=new_role_ct, changed_object_id=new_role.pk
                )

    # This is for all of the change records for roles which no longer exist
    if old_role_ct:
        # TODO: Should we null out the changed_object_id?
        ObjectChange.objects.filter(changed_object_type=old_role_ct).update(changed_object_type=new_role_ct)

    roles = Role.objects.filter(name__in=[roles.name for roles in roles_to_create])

    # Add content_types to the roles
    filter_ct_by = models.Q()
    for app_and_model in content_types:
        app_label, model_name = app_and_model.split(".")
        filter_ct_by |= models.Q(app_label=app_label, model=model_name)

    content_types = ContentType.objects.filter(filter_ct_by)
    for role in roles:
        role.content_types.add(*[content_type.id for content_type in content_types])


def populate_roles_from_related_app_roles(apps, schema_editor):
    """Populate Role models using records from other related role models or choices from different apps."""
    create_equivalent_roles_of_related_role_model(apps)
    create_equivalent_roles_of_virtualmachine_device_role(apps)
    create_equivalent_roles_of_ipaddress_role_choices(apps)


def clear_populated_roles(apps, schema_editor):
    Role = apps.get_model("extras", "Role")
    Role.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("extras", "0058_role_and_alter_status"),
    ]

    operations = [
        migrations.RunPython(populate_roles_from_related_app_roles, clear_populated_roles),
    ]