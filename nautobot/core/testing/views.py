import re
from typing import Optional, Sequence
from unittest import skipIf
import uuid

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import URLValidator
from django.test import override_settings, tag, TestCase as _TestCase
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape
from django.utils.http import urlencode
from django.utils.text import slugify
from tree_queries.models import TreeNode

from nautobot.core import testing
from nautobot.core.templatetags import helpers
from nautobot.core.testing import mixins
from nautobot.core.utils import lookup
from nautobot.extras import choices as extras_choices, models as extras_models, querysets as extras_querysets
from nautobot.users import models as users_models

__all__ = (
    "TestCase",
    "ModelTestCase",
    "ModelViewTestCase",
    "ViewTestCases",
)


@tag("unit")
@override_settings(PAGINATE_COUNT=65000)
class TestCase(mixins.NautobotTestCaseMixin, _TestCase):
    """Base class for all Nautobot-specific unit tests."""

    def setUp(self):
        """Initialize user and client."""
        super().setUpNautobot()


class ModelTestCase(TestCase):
    """
    Parent class for TestCases which deal with models.
    """

    model = None
    # Optional, list of Relationships populated in setUpTestData for testing with this model
    # Be sure to also create RelationshipAssociations using these Relationships!
    relationships: Optional[Sequence[extras_models.Relationship]] = None
    # Optional, list of CustomFields populated in setUpTestData for testing with this model
    # Be sure to also populate these fields on your test data!
    custom_fields: Optional[Sequence[extras_models.CustomField]] = None

    def _get_queryset(self):
        """
        Return a base queryset suitable for use in test methods.
        """
        return self.model.objects.all()


#
# UI Tests
#


@tag("performance")
class ModelViewTestCase(ModelTestCase):
    """
    Base TestCase for model views. Subclass to test individual views.
    """

    reverse_url_attribute = None
    """
    Name of instance field to pass as a kwarg when looking up URLs for creating/editing/deleting a model instance.

    If unspecified, "pk" and "slug" will be tried, in that order.
    """

    def _get_base_url(self):
        """
        Return the base format string for a view URL for the test.

        Examples: "dcim:device_{}", "plugins:example_app:example_model_{}"

        Override this if needed for testing of views that don't correspond directly to self.model,
        for example the DCIM "interface-connections" and "console-connections" view tests.
        """
        app_name = apps.get_app_config(app_label=self.model._meta.app_label).name
        # AppConfig.name accounts for NautobotApps that are not built at the root of the package
        if app_name in settings.PLUGINS:
            return f"plugins:{self.model._meta.app_label}:{self.model._meta.model_name}_{{}}"
        return f"{self.model._meta.app_label}:{self.model._meta.model_name}_{{}}"

    def _get_url(self, action, instance=None):
        """
        Return the URL string for a specific action and optionally a specific model instance.

        Override this if needed for testing of views whose names don't follow
        the [plugins]:<app_label>:<model_name>_<action> naming convention.
        """
        url_format = self._get_base_url()

        # If no instance was provided, assume we don't need a unique identifier
        if instance is None:
            return reverse(url_format.format(action))

        if self.reverse_url_attribute:
            return reverse(
                url_format.format(action),
                kwargs={self.reverse_url_attribute: getattr(instance, self.reverse_url_attribute)},
            )

        try:
            # Default to using the PK to retrieve the URL for an object
            return reverse(url_format.format(action), kwargs={"pk": instance.pk})
        except NoReverseMatch:
            # Attempt to resolve using slug as the unique identifier if one exists
            if hasattr(self.model, "slug"):
                return reverse(url_format.format(action), kwargs={"slug": instance.slug})
            raise


@tag("unit")
class ViewTestCases:
    """
    We keep any TestCases with test_* methods inside a class to prevent unittest from trying to run them.
    """

    class GetObjectViewTestCase(ModelViewTestCase):
        """
        Retrieve a single instance.
        """

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_get_object_anonymous(self):
            # Make the request as an unauthenticated user
            self.client.logout()
            response = self.client.get(self._get_queryset().first().get_absolute_url())
            self.assertHttpStatus(response, 200)
            response_body = response.content.decode(response.charset)
            self.assertIn(
                "/login/?next=" + self._get_queryset().first().get_absolute_url(), response_body, msg=response_body
            )

            # The "Change Log" tab should appear in the response since we have all exempt permissions
            if issubclass(self.model, extras_models.ChangeLoggedModel):
                response_body = testing.extract_page_body(response.content.decode(response.charset))
                self.assertIn("Change Log", response_body, msg=response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_get_object_without_permission(self):
            instance = self._get_queryset().first()

            # Try GET without permission
            with testing.disable_warnings("django.request"):
                response = self.client.get(instance.get_absolute_url())
                self.assertHttpStatus(response, [403, 404])
                response_body = response.content.decode(response.charset)
                self.assertNotIn("/login/", response_body, msg=response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_get_object_with_permission(self):
            instance = self._get_queryset().first()

            # Add model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["view"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with model-level permission
            response = self.client.get(instance.get_absolute_url())
            self.assertHttpStatus(response, 200)

            response_body = testing.extract_page_body(response.content.decode(response.charset))

            # The object's display name or string representation should appear in the response
            self.assertIn(escape(getattr(instance, "display", str(instance))), response_body, msg=response_body)

            # If any Relationships are defined, they should appear in the response
            if self.relationships is not None:
                for relationship in self.relationships:  # false positive pylint: disable=not-an-iterable
                    content_type = ContentType.objects.get_for_model(instance)
                    if content_type == relationship.source_type:
                        self.assertIn(
                            escape(relationship.get_label(extras_choices.RelationshipSideChoices.SIDE_SOURCE)),
                            response_body,
                            msg=response_body,
                        )
                    if content_type == relationship.destination_type:
                        self.assertIn(
                            escape(relationship.get_label(extras_choices.RelationshipSideChoices.SIDE_DESTINATION)),
                            response_body,
                            msg=response_body,
                        )

            # If any Custom Fields are defined, they should appear in the response
            if self.custom_fields is not None:
                for custom_field in self.custom_fields:  # false positive pylint: disable=not-an-iterable
                    self.assertIn(escape(str(custom_field)), response_body, msg=response_body)
                    if custom_field.type == extras_choices.CustomFieldTypeChoices.TYPE_MULTISELECT:
                        for value in instance.cf.get(custom_field.key):
                            self.assertIn(escape(str(value)), response_body, msg=response_body)
                    else:
                        self.assertIn(
                            escape(str(instance.cf.get(custom_field.key) or "")), response_body, msg=response_body
                        )

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_get_object_with_constrained_permission(self):
            instance1, instance2 = self._get_queryset().all()[:2]

            # Add object-level permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"pk": instance1.pk},
                # To get a different rendering flow than the `test_get_object_with_permission` test above,
                # enable additional permissions for this object so that add/edit/delete buttons are rendered.
                actions=["view", "add", "change", "delete"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET to permitted object
            self.assertHttpStatus(self.client.get(instance1.get_absolute_url()), 200)

            # Try GET to non-permitted object
            self.assertHttpStatus(self.client.get(instance2.get_absolute_url()), 404)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_has_advanced_tab(self):
            instance = self._get_queryset().first()

            # Add model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["view"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            response = self.client.get(instance.get_absolute_url())
            response_body = testing.extract_page_body(response.content.decode(response.charset))
            advanced_tab_href = f"{instance.get_absolute_url()}#advanced"

            self.assertIn(advanced_tab_href, response_body)
            self.assertIn("Advanced", response_body)

    class GetObjectChangelogViewTestCase(ModelViewTestCase):
        """
        View the changelog for an instance.
        """

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_get_object_changelog(self):
            obj = self._get_queryset().first()
            url = self._get_url("changelog", obj)
            response = self.client.get(url)
            self.assertHttpStatus(response, 200)
            response_data = response.content.decode(response.charset)
            if type(obj) not in [extras_models.Contact, extras_models.Team]:
                self.assertInHTML(
                    f'<a href="{obj.get_absolute_url()}#contacts" onclick="switch_tab(this.href)" aria-controls="contacts" role="tab" data-toggle="tab">Contacts</a>',
                    response_data,
                )

    class GetObjectNotesViewTestCase(ModelViewTestCase):
        """
        View the notes for an instance.
        """

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_get_object_notes(self):
            if hasattr(self.model, "notes"):
                obj = self._get_queryset().first()
                url = self._get_url("notes", obj)
                response = self.client.get(url)
                self.assertHttpStatus(response, 200)
                response_data = response.content.decode(response.charset)
                if type(obj) not in [extras_models.Contact, extras_models.Team]:
                    self.assertInHTML(
                        f'<a href="{obj.get_absolute_url()}#contacts" onclick="switch_tab(this.href)" aria-controls="contacts" role="tab" data-toggle="tab">Contacts</a>',
                        response_data,
                    )

    class CreateObjectViewTestCase(ModelViewTestCase):
        """
        Create a single new instance.

        :form_data: Data to be used when creating a new object.
        """

        form_data = {}
        slug_source = None
        slugify_function = staticmethod(slugify)
        slug_test_object = ""

        def test_create_object_without_permission(self):
            # Try GET without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.get(self._get_url("add")), 403)

            # Try POST without permission
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.form_data),
            }
            response = self.client.post(**request)
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(response, 403)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_create_object_with_permission(self):
            initial_count = self._get_queryset().count()

            # Assign unconstrained permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["add"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with model-level permission
            self.assertHttpStatus(self.client.get(self._get_url("add")), 200)

            # Try POST with model-level permission
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.form_data),
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            self.assertEqual(initial_count + 1, self._get_queryset().count())
            # order_by() is no supported by django TreeNode,
            # So we directly retrieve the instance by "slug" or "name".
            if isinstance(self._get_queryset().first(), TreeNode):
                filter_by = self.slug_source if getattr(self, "slug_source", None) else "name"
                instance = self._get_queryset().get(**{filter_by: self.form_data.get(filter_by)})
                self.assertInstanceEqual(instance, self.form_data)
            else:
                if hasattr(self.model, "last_updated"):
                    instance = self._get_queryset().order_by("last_updated").last()
                    self.assertInstanceEqual(instance, self.form_data)
                else:
                    instance = self._get_queryset().last()
                    self.assertInstanceEqual(instance, self.form_data)

            if hasattr(self.model, "to_objectchange"):
                # Verify ObjectChange creation
                objectchanges = lookup.get_changes_for_model(instance)
                self.assertEqual(len(objectchanges), 1)
                # Assert that Created By table row is updated with the user that created the object
                self.assertEqual(objectchanges[0].action, extras_choices.ObjectChangeActionChoices.ACTION_CREATE)
                # Validate if detail view exists
                validate = URLValidator()
                try:
                    detail_url = instance.get_absolute_url()
                    validate(detail_url)
                    response = self.client.get(detail_url)
                    response_body = testing.extract_page_body(response.content.decode(response.charset))
                    advanced_tab_href = f"{detail_url}#advanced"
                    self.assertIn(advanced_tab_href, response_body)
                    self.assertIn("<td>Created By</td>", response_body)
                    self.assertIn("<td>nautobotuser</td>", response_body)
                except (AttributeError, ValidationError):
                    # Instance does not have a valid detail view, do nothing here.
                    pass

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_create_object_with_constrained_permission(self):
            initial_count = self._get_queryset().count()

            # Assign constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"pk": str(uuid.uuid4())},  # Match a non-existent pk (i.e., deny all)
                actions=["add"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with object-level permission
            self.assertHttpStatus(self.client.get(self._get_url("add")), 200)

            # Try to create an object (not permitted)
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.form_data),
            }
            self.assertHttpStatus(self.client.post(**request), 200)
            self.assertEqual(initial_count, self._get_queryset().count())  # Check that no object was created

            # Update the ObjectPermission to allow creation
            obj_perm.constraints = {"pk__isnull": False}
            obj_perm.save()

            # Try to create an object (permitted)
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.form_data),
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            self.assertEqual(initial_count + 1, self._get_queryset().count())
            # order_by() is no supported by django TreeNode,
            # So we directly retrieve the instance by "slug".
            if isinstance(self._get_queryset().first(), TreeNode):
                filter_by = self.slug_source if getattr(self, "slug_source", None) else "name"
                instance = self._get_queryset().get(**{filter_by: self.form_data.get(filter_by)})
                self.assertInstanceEqual(instance, self.form_data)
            else:
                if hasattr(self.model, "last_updated"):
                    self.assertInstanceEqual(self._get_queryset().order_by("last_updated").last(), self.form_data)
                else:
                    self.assertInstanceEqual(self._get_queryset().last(), self.form_data)

        def test_slug_autocreation(self):
            """Test that slug is autocreated through ORM."""
            # This really should go on a models test page, but we don't have test structures for models.
            if getattr(self.model, "slug_source", None) is not None:
                obj = self.model.objects.get(**{self.slug_source: self.slug_test_object})
                expected_slug = self.slugify_function(getattr(obj, self.slug_source))
                self.assertEqual(obj.slug, expected_slug)

        def test_slug_not_modified(self):
            """Ensure save method does not modify slug that is passed in."""
            # This really should go on a models test page, but we don't have test structures for models.
            if getattr(self.model, "slug_source", None) is not None:
                new_slug_source_value = "kwyjibo"

                obj = self.model.objects.get(**{self.slug_source: self.slug_test_object})
                expected_slug = self.slugify_function(getattr(obj, self.slug_source))
                # Update slug source field str
                filter_ = self.slug_source + "__exact"
                self.model.objects.filter(**{filter_: self.slug_test_object}).update(
                    **{self.slug_source: new_slug_source_value}
                )

                obj.refresh_from_db()
                self.assertEqual(getattr(obj, self.slug_source), new_slug_source_value)
                self.assertEqual(obj.slug, expected_slug)

    class EditObjectViewTestCase(ModelViewTestCase):
        """
        Edit a single existing instance.

        :form_data: Data to be used when updating the first existing object.
        """

        form_data = {}

        def test_edit_object_without_permission(self):
            instance = self._get_queryset().first()

            # Try GET without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.get(self._get_url("edit", instance)), [403, 404])

            # Try POST without permission
            request = {
                "path": self._get_url("edit", instance),
                "data": testing.post_data(self.form_data),
            }
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.post(**request), [403, 404])

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_edit_object_with_permission(self):
            instance = self._get_queryset().first()

            # Assign model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["change"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with model-level permission
            self.assertHttpStatus(self.client.get(self._get_url("edit", instance)), 200)

            # Try POST with model-level permission
            request = {
                "path": self._get_url("edit", instance),
                "data": testing.post_data(self.form_data),
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            self.assertInstanceEqual(self._get_queryset().get(pk=instance.pk), self.form_data)

            if hasattr(self.model, "to_objectchange"):
                # Verify ObjectChange creation
                objectchanges = lookup.get_changes_for_model(instance)
                self.assertEqual(len(objectchanges), 1)
                self.assertEqual(objectchanges[0].action, extras_choices.ObjectChangeActionChoices.ACTION_UPDATE)
                # Validate if detail view exists
                validate = URLValidator()
                try:
                    detail_url = instance.get_absolute_url()
                    validate(detail_url)
                    response = self.client.get(detail_url)
                    response_body = testing.extract_page_body(response.content.decode(response.charset))
                    advanced_tab_href = f"{detail_url}#advanced"
                    self.assertIn(advanced_tab_href, response_body)
                    self.assertIn("<td>Last Updated By</td>", response_body)
                    self.assertIn("<td>nautobotuser</td>", response_body)
                except (AttributeError, ValidationError):
                    # Instance does not have a valid detail view, do nothing here.
                    pass

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_edit_object_with_constrained_permission(self):
            instance1, instance2 = self._get_queryset().all()[:2]

            # Assign constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"pk": instance1.pk},
                actions=["change"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with a permitted object
            self.assertHttpStatus(self.client.get(self._get_url("edit", instance1)), 200)

            # Try GET with a non-permitted object
            self.assertHttpStatus(self.client.get(self._get_url("edit", instance2)), 404)

            # Try to edit a permitted object
            request = {
                "path": self._get_url("edit", instance1),
                "data": testing.post_data(self.form_data),
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            self.assertInstanceEqual(self._get_queryset().get(pk=instance1.pk), self.form_data)

            # Try to edit a non-permitted object
            request = {
                "path": self._get_url("edit", instance2),
                "data": testing.post_data(self.form_data),
            }
            self.assertHttpStatus(self.client.post(**request), 404)

    class DeleteObjectViewTestCase(ModelViewTestCase):
        """
        Delete a single instance.
        """

        def get_deletable_object(self):
            """
            Get an instance that can be deleted.

            For some models this may just be any random object, but when we have FKs with `on_delete=models.PROTECT`
            (as is often the case) we need to find or create an instance that doesn't have such entanglements.
            """
            instance = testing.get_deletable_objects(self.model, self._get_queryset()).first()
            if instance is None:
                self.fail("Couldn't find a single deletable object!")
            return instance

        def test_delete_object_without_permission(self):
            instance = self.get_deletable_object()

            # Try GET without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.get(self._get_url("delete", instance)), [403, 404])

            # Try POST without permission
            request = {
                "path": self._get_url("delete", instance),
                "data": testing.post_data({"confirm": True}),
            }
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.post(**request), [403, 404])

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_delete_object_with_permission(self):
            instance = self.get_deletable_object()
            instance_note_pk_list = []
            assigned_object_type = ContentType.objects.get_for_model(self.model)
            if hasattr(self.model, "notes") and isinstance(instance.notes, extras_querysets.NotesQuerySet):
                notes = (
                    extras_models.Note(
                        assigned_object_type=assigned_object_type, assigned_object_id=instance.id, note="hello 1"
                    ),
                    extras_models.Note(
                        assigned_object_type=assigned_object_type, assigned_object_id=instance.id, note="hello 2"
                    ),
                    extras_models.Note(
                        assigned_object_type=assigned_object_type, assigned_object_id=instance.id, note="hello 3"
                    ),
                )
                for note in notes:
                    note.validated_save()
                    instance_note_pk_list.append(note.pk)

            # Assign model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["delete"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(assigned_object_type)

            # Try GET with model-level permission
            self.assertHttpStatus(self.client.get(self._get_url("delete", instance)), 200)

            # Try POST with model-level permission
            request = {
                "path": self._get_url("delete", instance),
                "data": testing.post_data({"confirm": True}),
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            with self.assertRaises(ObjectDoesNotExist):
                self._get_queryset().get(pk=instance.pk)

            if hasattr(self.model, "to_objectchange"):
                # Verify ObjectChange creation
                objectchanges = lookup.get_changes_for_model(instance)
                self.assertEqual(len(objectchanges), 1)
                self.assertEqual(objectchanges[0].action, extras_choices.ObjectChangeActionChoices.ACTION_DELETE)

            if hasattr(self.model, "notes") and isinstance(instance.notes, extras_querysets.NotesQuerySet):
                # Verify Notes deletion
                with self.assertRaises(ObjectDoesNotExist):
                    extras_models.Note.objects.get(assigned_object_id=instance.pk)

                note_objectchanges = extras_models.ObjectChange.objects.filter(
                    changed_object_id__in=instance_note_pk_list
                )
                self.assertEqual(note_objectchanges.count(), 3)
                for object_change in note_objectchanges:
                    self.assertEqual(object_change.action, extras_choices.ObjectChangeActionChoices.ACTION_DELETE)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_delete_object_with_permission_and_xwwwformurlencoded(self):
            instance = self.get_deletable_object()

            # Assign model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["delete"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with model-level permission
            self.assertHttpStatus(self.client.get(self._get_url("delete", instance)), 200)

            # Try POST with model-level permission
            request = {
                "path": self._get_url("delete", instance),
                "data": urlencode({"confirm": True}),
                "content_type": "application/x-www-form-urlencoded",
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            with self.assertRaises(ObjectDoesNotExist):
                self._get_queryset().get(pk=instance.pk)

            if hasattr(self.model, "to_objectchange"):
                # Verify ObjectChange creation
                objectchanges = lookup.get_changes_for_model(instance)
                self.assertEqual(len(objectchanges), 1)
                self.assertEqual(objectchanges[0].action, extras_choices.ObjectChangeActionChoices.ACTION_DELETE)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_delete_object_with_constrained_permission(self):
            instance1 = self.get_deletable_object()
            instance2 = self._get_queryset().exclude(pk=instance1.pk)[0]

            # Assign object-level permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"pk": instance1.pk},
                actions=["delete"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with a permitted object
            self.assertHttpStatus(self.client.get(self._get_url("delete", instance1)), 200)

            # Try GET with a non-permitted object
            self.assertHttpStatus(self.client.get(self._get_url("delete", instance2)), 404)

            # Try to delete a permitted object
            request = {
                "path": self._get_url("delete", instance1),
                "data": testing.post_data({"confirm": True}),
            }
            self.assertHttpStatus(self.client.post(**request), 302)
            with self.assertRaises(ObjectDoesNotExist):
                self._get_queryset().get(pk=instance1.pk)

            # Try to delete a non-permitted object
            # Note that in the case of tree models, deleting instance1 above may have cascade-deleted to instance2,
            # so to be safe, we need to get another object instance that definitely exists:
            instance3 = self._get_queryset().first()
            request = {
                "path": self._get_url("delete", instance3),
                "data": testing.post_data({"confirm": True}),
            }
            self.assertHttpStatus(self.client.post(**request), 404)
            self.assertTrue(self._get_queryset().filter(pk=instance3.pk).exists())

    class ListObjectsViewTestCase(ModelViewTestCase):
        """
        Retrieve multiple instances.
        """

        filterset = None

        def get_filterset(self):
            return self.filterset or lookup.get_filterset_for_model(self.model)

        # Helper methods to be overriden by special cases.
        # See ConsoleConnectionsTestCase, InterfaceConnectionsTestCase and PowerConnectionsTestCase
        def get_list_url(self):
            return reverse(helpers.validated_viewname(self.model, "list"))

        def get_title(self):
            return helpers.bettertitle(self.model._meta.verbose_name_plural)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_list_objects_anonymous(self):
            # Make the request as an unauthenticated user
            self.client.logout()
            response = self.client.get(self._get_url("list"))
            self.assertHttpStatus(response, 200)
            response_body = response.content.decode(response.charset)
            self.assertIn("/login/?next=" + self._get_url("list"), response_body, msg=response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_list_objects_filtered(self):
            instance1, instance2 = self._get_queryset().all()[:2]
            response = self.client.get(f"{self._get_url('list')}?id={instance1.pk}")
            self.assertHttpStatus(response, 200)
            content = testing.extract_page_body(response.content.decode(response.charset))
            # TODO: it'd make test failures more readable if we strip the page headers/footers from the content
            if hasattr(self.model, "name"):
                self.assertRegex(content, r">\s*" + re.escape(escape(instance1.name)) + r"\s*<", msg=content)
                self.assertNotRegex(content, r">\s*" + re.escape(escape(instance2.name)) + r"\s*<", msg=content)
            if instance1.get_absolute_url() in content:
                self.assertNotIn(instance2.get_absolute_url(), content, msg=content)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"], STRICT_FILTERING=True)
        def test_list_objects_unknown_filter_strict_filtering(self):
            """Verify that with STRICT_FILTERING, an unknown filter results in an error message and no matches."""
            response = self.client.get(f"{self._get_url('list')}?ice_cream_flavor=chocolate")
            self.assertHttpStatus(response, 200)
            content = testing.extract_page_body(response.content.decode(response.charset))
            # TODO: it'd make test failures more readable if we strip the page headers/footers from the content
            self.assertIn("Unknown filter field", content, msg=content)
            # There should be no table rows displayed except for the empty results row
            self.assertIn("None", content, msg=content)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"], STRICT_FILTERING=False)
        def test_list_objects_unknown_filter_no_strict_filtering(self):
            """Verify that without STRICT_FILTERING, an unknown filter is ignored."""
            instance1, instance2 = self._get_queryset().all()[:2]
            with self.assertLogs("nautobot.core.filters") as cm:
                response = self.client.get(f"{self._get_url('list')}?ice_cream_flavor=chocolate")
            filterset = self.get_filterset()
            if not filterset:
                self.fail(
                    f"Couldn't find filterset for model {self.model}. The FilterSet class is expected to be in the "
                    "filters module within the application associated with the model and its name is expected to be "
                    f"{self.model.__name__}FilterSet."
                )
            self.assertEqual(
                cm.output,
                [
                    f'WARNING:nautobot.core.filters:{filterset.__name__}: Unknown filter field "ice_cream_flavor"',
                ],
            )
            self.assertHttpStatus(response, 200)
            content = testing.extract_page_body(response.content.decode(response.charset))
            # TODO: it'd make test failures more readable if we strip the page headers/footers from the content
            self.assertNotIn("Unknown filter field", content, msg=content)
            self.assertIn("None", content, msg=content)
            if hasattr(self.model, "name"):
                self.assertRegex(content, r">\s*" + re.escape(escape(instance1.name)) + r"\s*<", msg=content)
                self.assertRegex(content, r">\s*" + re.escape(escape(instance2.name)) + r"\s*<", msg=content)
            if instance1.get_absolute_url() in content:
                self.assertIn(instance2.get_absolute_url(), content, msg=content)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_list_objects_without_permission(self):
            # Try GET without permission
            with testing.disable_warnings("django.request"):
                response = self.client.get(self._get_url("list"))
                self.assertHttpStatus(response, 403)
                response_body = response.content.decode(response.charset)
                self.assertNotIn("/login/", response_body, msg=response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_list_objects_with_permission(self):
            # Add model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["view"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with model-level permission
            response = self.client.get(self._get_url("list"))
            self.assertHttpStatus(response, 200)
            response_body = response.content.decode(response.charset)

            list_url = self.get_list_url()
            title = self.get_title()

            # Check if breadcrumb is rendered correctly
            self.assertIn(
                f'<a href="{list_url}">{title}</a>',
                response_body,
            )

            # Check if import button is absent due to user permissions
            self.assertNotIn(
                reverse("extras:job_run_by_class_path", kwargs={"class_path": "nautobot.core.jobs.ImportObjects"}),
                response_body,
            )

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_list_objects_with_constrained_permission(self):
            instance1, instance2 = self._get_queryset().all()[:2]

            # Add object-level permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"pk": instance1.pk},
                actions=["view", "add"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with object-level permission
            response = self.client.get(self._get_url("list"))
            self.assertHttpStatus(response, 200)
            content = testing.extract_page_body(response.content.decode(response.charset))
            # TODO: it'd make test failures more readable if we strip the page headers/footers from the content
            if hasattr(self.model, "name"):
                self.assertRegex(content, r">\s*" + re.escape(escape(instance1.name)) + r"\s*<", msg=content)
                self.assertNotRegex(content, r">\s*" + re.escape(escape(instance2.name)) + r"\s*<", msg=content)
            elif hasattr(self.model, "get_absolute_url"):
                self.assertIn(instance1.get_absolute_url(), content, msg=content)
                self.assertNotIn(instance2.get_absolute_url(), content, msg=content)

            view = lookup.get_view_for_model(self.model, view_type="List")
            if view and hasattr(view, "action_buttons") and "import" in view.action_buttons:
                # Check if import button is present due to user permissions
                self.assertIn(
                    (
                        reverse(
                            "extras:job_run_by_class_path", kwargs={"class_path": "nautobot.core.jobs.ImportObjects"}
                        )
                        + f"?content_type={ContentType.objects.get_for_model(self.model).pk}"
                    ),
                    content,
                )
            else:
                # Import not supported, no button should be present
                self.assertNotIn(
                    reverse("extras:job_run_by_class_path", kwargs={"class_path": "nautobot.core.jobs.ImportObjects"}),
                    content,
                )

        @skipIf(
            "example_app" not in settings.PLUGINS,
            "example_app not in settings.PLUGINS",
        )
        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_list_view_app_banner(self):
            """
            If example app is installed, check if the app banner is rendered correctly in ObjectListView.
            """
            # Add model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["view"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try GET with model-level permission
            response = self.client.get(self._get_url("list"))
            self.assertHttpStatus(response, 200)
            response_body = response.content.decode(response.charset)

            # Check app banner is rendered correctly
            self.assertIn(
                f"<div>You are viewing a table of {self.model._meta.verbose_name_plural}</div>", response_body
            )

    class CreateMultipleObjectsViewTestCase(ModelViewTestCase):
        """
        Create multiple instances using a single form. Expects the creation of three new instances by default.

        :bulk_create_count: The number of objects expected to be created (default: 3).
        :bulk_create_data: A dictionary of data to be used for bulk object creation.
        """

        bulk_create_count = 3
        bulk_create_data = {}

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_create_multiple_objects_without_permission(self):
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.bulk_create_data),
            }

            # Try POST without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.post(**request), 403)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_create_multiple_objects_with_permission(self):
            initial_count = self._get_queryset().count()
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.bulk_create_data),
            }

            # Assign non-constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                actions=["add"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Bulk create objects
            response = self.client.post(**request)
            self.assertHttpStatus(response, 302)
            self.assertEqual(initial_count + self.bulk_create_count, self._get_queryset().count())
            matching_count = 0
            for instance in self._get_queryset().all():
                try:
                    self.assertInstanceEqual(instance, self.bulk_create_data)
                    matching_count += 1
                except AssertionError:
                    pass
            self.assertEqual(matching_count, self.bulk_create_count)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_create_multiple_objects_with_constrained_permission(self):
            initial_count = self._get_queryset().count()
            request = {
                "path": self._get_url("add"),
                "data": testing.post_data(self.bulk_create_data),
            }

            # Assign constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                actions=["add"],
                constraints={"pk": uuid.uuid4()},  # Match a non-existent pk (i.e., deny all)
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Attempt to make the request with unmet constraints
            self.assertHttpStatus(self.client.post(**request), 200)
            self.assertEqual(self._get_queryset().count(), initial_count)

            # Update the ObjectPermission to allow creation
            obj_perm.constraints = {"pk__isnull": False}  # Set constraint to allow all
            obj_perm.save()

            response = self.client.post(**request)
            self.assertHttpStatus(response, 302)
            self.assertEqual(initial_count + self.bulk_create_count, self._get_queryset().count())

            matching_count = 0
            for instance in self._get_queryset().all():
                try:
                    self.assertInstanceEqual(instance, self.bulk_create_data)
                    matching_count += 1
                except AssertionError:
                    pass
            self.assertEqual(matching_count, self.bulk_create_count)

    class BulkImportObjectsViewTestCase(ModelViewTestCase):  # 3.0 TODO: remove this test mixin, no longer relevant.
        """
        Vestigial test case, to be removed in 3.0.

        This is vestigial since the introduction of the ImportObjects system Job to handle bulk-import of all
        content-types via REST API serializers. The parsing of CSV data by the serializer is exercised by
        APIViewTestCases.CreateObjectViewTestCase.test_recreate_object_csv(), and the basic operation of the Job is
        exercised by nautobot.core.tests.test_jobs.
        """

        csv_data = ()

        def _get_csv_data(self):
            return "\n".join(self.csv_data)

        # Just in case Apps are extending any of these tests and calling super() in them.
        def test_bulk_import_objects_without_permission(self):
            pass

        def test_bulk_import_objects_with_permission(self):
            pass

        def test_bulk_import_objects_with_permission_csv_file(self):
            pass

        def test_bulk_import_objects_with_constrained_permission(self):
            pass

    class BulkEditObjectsViewTestCase(ModelViewTestCase):
        """
        Edit multiple instances.

        :bulk_edit_data: A dictionary of data to be used when bulk editing a set of objects. This data should differ
                         from that used for initial object creation within setUpTestData().
        """

        bulk_edit_data = {}

        def test_bulk_edit_objects_without_permission(self):
            pk_list = list(self._get_queryset().values_list("pk", flat=True)[:3])
            data = {
                "pk": pk_list,
                "_apply": True,  # Form button
            }

            # Try POST without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.post(self._get_url("bulk_edit"), data), 403)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_edit_objects_with_permission(self):
            pk_list = list(self._get_queryset().values_list("pk", flat=True)[:3])
            data = {
                "pk": pk_list,
                "_apply": True,  # Form button
            }

            # Append the form data to the request
            data.update(testing.post_data(self.bulk_edit_data))

            # Assign model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["change"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try POST with model-level permission
            self.assertHttpStatus(self.client.post(self._get_url("bulk_edit"), data), 302)
            for instance in self._get_queryset().filter(pk__in=pk_list):
                self.assertInstanceEqual(instance, self.bulk_edit_data)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_edit_form_contains_all_pks(self):
            # We are testing the intermediary step of bulk_edit with pagination applied.
            # i.e. "_all" passed in the form.
            pk_list = self._get_queryset().values_list("pk", flat=True)
            # We only pass in one pk to test the functionality of "_all"
            # which should grab all instance pks regardless of "pk"
            selected_data = {
                "pk": pk_list[:1],
                "_all": "on",
            }
            # Assign model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["change"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try POST with model-level permission
            response = self.client.post(self._get_url("bulk_edit"), selected_data)
            # Expect a 200 status cause we are only rendering the bulk edit table.
            # after pressing Edit Selected button.
            self.assertHttpStatus(response, 200)
            response_body = testing.extract_page_body(response.content.decode(response.charset))
            # Check if all the pks are passed into the BulkEditForm/BulkUpdateForm
            for pk in pk_list:
                self.assertIn(f'<input type="hidden" name="pk" value="{pk}"', response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_edit_form_contains_all_filtered(self):
            # We are testing the intermediary step of bulk_edit with pagination applied and additional filter.
            # i.e. "_all" passed in the form and filter using query params.
            self.add_permissions(f"{self.model._meta.app_label}.change_{self.model._meta.model_name}")

            pk_iter = iter(self._get_queryset().values_list("pk", flat=True))
            try:
                first_pk = next(pk_iter)
                second_pk = next(pk_iter)
                third_pk = next(pk_iter)
            except StopIteration:
                self.fail(f"Test requires at least three instances of {self.model._meta.model_name} to be defined.")

            post_data = testing.post_data(self.bulk_edit_data)

            # Open bulk update form with first two objects
            selected_data = {
                "pk": third_pk,  # This is ignored when filtering with "_all"
                "_all": "on",
                **post_data,
            }
            query_string = urlencode({"id": (first_pk, second_pk)}, doseq=True)
            response = self.client.post(f"{self._get_url('bulk_edit')}?{query_string}", selected_data)
            # Expect a 200 status cause we are only rendering the bulk edit table after pressing Edit Selected button.
            self.assertHttpStatus(response, 200)
            response_body = testing.extract_page_body(response.content.decode(response.charset))
            # Check if the first and second pk is passed into the form.
            self.assertIn(f'<input type="hidden" name="pk" value="{first_pk}"', response_body)
            self.assertIn(f'<input type="hidden" name="pk" value="{second_pk}"', response_body)
            self.assertIn("Editing 2 ", response_body)
            # Check if the third pk is not passed into the form.
            self.assertNotIn(f'<input type="hidden" name="pk" value="{third_pk}"', response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_edit_objects_with_constrained_permission(self):
            # Select some objects that are *not* already set to match the first value in self.bulk_edit_data or null.
            # We have to exclude null cases because Django filter()/exclude() doesn't like `__in=[None]` as a case.
            attr_name = next(iter(self.bulk_edit_data.keys()))
            objects = (
                self._get_queryset()
                .exclude(**{attr_name: self.bulk_edit_data[attr_name]})
                .exclude(**{f"{attr_name}__isnull": True})
            )[:3]
            self.assertEqual(objects.count(), 3)
            pk_list = list(objects.values_list("pk", flat=True))

            # Define a permission that permits the above objects, but will not permit them after updating them.
            field = self.model._meta.get_field(attr_name)
            values = [field.value_from_object(obj) for obj in objects]

            # Assign constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={f"{attr_name}__in": values},
                actions=["change"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Build form data
            data = {
                "pk": pk_list,
                "_apply": True,  # Form button
            }
            data.update(testing.post_data(self.bulk_edit_data))

            # Attempt to bulk edit permitted objects into a non-permitted state
            response = self.client.post(self._get_url("bulk_edit"), data)
            # 200 because we're sent back to the edit form to try again; if the update were successful it'd be a 302
            self.assertHttpStatus(response, 200)
            # Assert that the objects are NOT updated
            for instance in self._get_queryset().filter(pk__in=pk_list):
                self.assertIn(field.value_from_object(instance), values)
                self.assertNotEqual(field.value_from_object(instance), self.bulk_edit_data[attr_name])

            # Update permission constraints to permit all objects
            obj_perm.constraints = {"pk__gt": 0}
            obj_perm.save()

            # Bulk edit permitted objects and expect a redirect back to the list view
            self.assertHttpStatus(self.client.post(self._get_url("bulk_edit"), data), 302)
            # Assert that the objects were all updated correctly
            for instance in self._get_queryset().filter(pk__in=pk_list):
                self.assertInstanceEqual(instance, self.bulk_edit_data)

    class BulkDeleteObjectsViewTestCase(ModelViewTestCase):
        """
        Delete multiple instances.
        """

        def get_deletable_object_pks(self):
            """
            Get a list of PKs corresponding to objects that can be safely bulk-deleted.

            For some models this may just be any random objects, but when we have FKs with `on_delete=models.PROTECT`
            (as is often the case) we need to find or create an instance that doesn't have such entanglements.
            """
            return testing.get_deletable_objects(self.model, self._get_queryset()).values_list("pk", flat=True)[:3]

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_bulk_delete_objects_without_permission(self):
            pk_list = self.get_deletable_object_pks()
            data = {
                "pk": pk_list,
                "confirm": True,
                "_confirm": True,  # Form button
            }

            # Try POST without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.post(self._get_url("bulk_delete"), data), 403)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_bulk_delete_objects_with_permission(self):
            pk_list = self.get_deletable_object_pks()
            initial_count = self._get_queryset().count()
            data = {
                "pk": pk_list,
                "confirm": True,
                "_confirm": True,  # Form button
            }

            # Assign unconstrained permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["delete"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try POST with model-level permission
            self.assertHttpStatus(self.client.post(self._get_url("bulk_delete"), data), 302)
            self.assertEqual(self._get_queryset().count(), initial_count - len(pk_list))

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_bulk_delete_form_contains_all_pks(self):
            # We are testing the intermediary step of bulk_delete with pagination applied.
            # i.e. "_all" passed in the form.
            pk_list = self._get_queryset().values_list("pk", flat=True)
            # We only pass in one pk to test the functionality of "_all"
            # which should grab all instance pks regardless of "pks".
            selected_data = {
                "pk": pk_list[:1],
                "confirm": True,
                "_all": "on",
            }

            # Assign unconstrained permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["delete"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try POST with the selected data first. Emulating selecting all -> pressing Delete Selected button.
            response = self.client.post(self._get_url("bulk_delete"), selected_data)
            self.assertHttpStatus(response, 200)
            response_body = testing.extract_page_body(response.content.decode(response.charset))
            # Check if all the pks are passed into the BulkDeleteForm/BulkDestroyForm
            for pk in pk_list:
                self.assertIn(f'<input type="hidden" name="pk" value="{pk}"', response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_delete_form_contains_all_filtered(self):
            # We are testing the intermediary step of bulk_delete with pagination applied and additional filter.
            # i.e. "_all" passed in the form and filter using query params.
            self.add_permissions(f"{self.model._meta.app_label}.delete_{self.model._meta.model_name}")

            pk_iter = iter(self._get_queryset().values_list("pk", flat=True))
            try:
                first_pk = next(pk_iter)
                second_pk = next(pk_iter)
                third_pk = next(pk_iter)
            except StopIteration:
                self.fail(f"Test requires at least three instances of {self.model._meta.model_name} to be defined.")

            # Open bulk delete form with first two objects
            selected_data = {
                "pk": third_pk,  # This is ignored when filtering with "_all"
                "_all": "on",
            }
            query_string = urlencode({"id": (first_pk, second_pk)}, doseq=True)
            response = self.client.post(f"{self._get_url('bulk_delete')}?{query_string}", selected_data)
            # Expect a 200 status cause we are only rendering the bulk delete table after pressing Delete Selected button.
            self.assertHttpStatus(response, 200)
            response_body = testing.extract_page_body(response.content.decode(response.charset))
            # Check if the first and second pk is passed into the form.
            self.assertIn(f'<input type="hidden" name="pk" value="{first_pk}"', response_body)
            self.assertIn(f'<input type="hidden" name="pk" value="{second_pk}"', response_body)
            self.assertIn("<strong>Warning:</strong> The following operation will delete 2 ", response_body)
            # Check if the third pk is not passed into the form.
            self.assertNotIn(f'<input type="hidden" name="pk" value="{third_pk}"', response_body)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
        def test_bulk_delete_objects_with_constrained_permission(self):
            pk_list = self.get_deletable_object_pks()
            initial_count = self._get_queryset().count()
            data = {
                "pk": pk_list,
                "confirm": True,
                "_confirm": True,  # Form button
            }

            # Assign constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"pk": str(uuid.uuid4())},  # Match a non-existent pk (i.e., deny all)
                actions=["delete"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Attempt to bulk delete non-permitted objects
            self.assertHttpStatus(self.client.post(self._get_url("bulk_delete"), data), 302)
            self.assertEqual(self._get_queryset().count(), initial_count)

            # Update permission constraints
            obj_perm.constraints = {"pk__isnull": False}  # Match a non-existent pk (i.e., allow all)
            obj_perm.save()

            # Bulk delete permitted objects
            self.assertHttpStatus(self.client.post(self._get_url("bulk_delete"), data), 302)
            self.assertEqual(self._get_queryset().count(), initial_count - len(pk_list))

    class BulkRenameObjectsViewTestCase(ModelViewTestCase):
        """
        Rename multiple instances.
        """

        rename_data = {
            "find": "^(.*)$",
            "replace": "\\1X",  # Append an X to the original value
            "use_regex": True,
        }

        def test_bulk_rename_objects_without_permission(self):
            pk_list = list(self._get_queryset().values_list("pk", flat=True)[:3])
            data = {
                "pk": pk_list,
                "_apply": True,  # Form button
            }
            data.update(self.rename_data)

            # Test GET without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.get(self._get_url("bulk_rename")), 403)

            # Try POST without permission
            with testing.disable_warnings("django.request"):
                self.assertHttpStatus(self.client.post(self._get_url("bulk_rename"), data), 403)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_rename_objects_with_permission(self):
            objects = list(self._get_queryset().all()[:3])
            pk_list = [obj.pk for obj in objects]
            data = {
                "pk": pk_list,
                "_apply": True,  # Form button
            }
            data.update(self.rename_data)

            # Assign model-level permission
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["change"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Try POST with model-level permission
            self.assertHttpStatus(self.client.post(self._get_url("bulk_rename"), data), 302)
            for i, instance in enumerate(self._get_queryset().filter(pk__in=pk_list)):
                self.assertEqual(instance.name, f"{objects[i].name}X")

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_rename_objects_with_constrained_permission(self):
            objects = list(self._get_queryset().all()[:3])
            pk_list = [obj.pk for obj in objects]
            data = {
                "pk": pk_list,
                "_apply": True,  # Form button
            }
            data.update(self.rename_data)

            # Assign constrained permission
            obj_perm = users_models.ObjectPermission(
                name="Test permission",
                constraints={"name__regex": "[^X]$"},
                actions=["change"],
            )
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            # Attempt to bulk edit permitted objects into a non-permitted state
            response = self.client.post(self._get_url("bulk_rename"), data)
            self.assertHttpStatus(response, 200)

            # Update permission constraints
            obj_perm.constraints = {"pk__gt": 0}
            obj_perm.save()

            # Bulk rename permitted objects
            self.assertHttpStatus(self.client.post(self._get_url("bulk_rename"), data), 302)
            for i, instance in enumerate(self._get_queryset().filter(pk__in=pk_list)):
                self.assertEqual(instance.name, f"{objects[i].name}X")

    class PrimaryObjectViewTestCase(
        GetObjectViewTestCase,
        GetObjectChangelogViewTestCase,
        GetObjectNotesViewTestCase,
        CreateObjectViewTestCase,
        EditObjectViewTestCase,
        DeleteObjectViewTestCase,
        ListObjectsViewTestCase,
        BulkEditObjectsViewTestCase,
        BulkDeleteObjectsViewTestCase,
    ):
        """
        TestCase suitable for testing all standard View functions for primary objects
        """

        maxDiff = None

    class OrganizationalObjectViewTestCase(
        GetObjectViewTestCase,
        GetObjectChangelogViewTestCase,
        GetObjectNotesViewTestCase,
        CreateObjectViewTestCase,
        EditObjectViewTestCase,
        DeleteObjectViewTestCase,
        ListObjectsViewTestCase,
        BulkDeleteObjectsViewTestCase,
    ):
        """
        TestCase suitable for all organizational objects
        """

        maxDiff = None

    class DeviceComponentTemplateViewTestCase(
        EditObjectViewTestCase,
        DeleteObjectViewTestCase,
        CreateMultipleObjectsViewTestCase,
        BulkEditObjectsViewTestCase,
        BulkRenameObjectsViewTestCase,
        BulkDeleteObjectsViewTestCase,
    ):
        """
        TestCase suitable for testing device component template models (ConsolePortTemplates, InterfaceTemplates, etc.)
        """

        maxDiff = None

    class DeviceComponentViewTestCase(
        GetObjectViewTestCase,
        GetObjectChangelogViewTestCase,
        GetObjectNotesViewTestCase,
        EditObjectViewTestCase,
        DeleteObjectViewTestCase,
        ListObjectsViewTestCase,
        CreateMultipleObjectsViewTestCase,
        BulkEditObjectsViewTestCase,
        BulkRenameObjectsViewTestCase,
        BulkDeleteObjectsViewTestCase,
    ):
        """
        TestCase suitable for testing device component models (ConsolePorts, Interfaces, etc.)
        """

        maxDiff = None
        bulk_add_data = None
        """Used for bulk-add (distinct from bulk-create) view testing; self.bulk_create_data will be used if unset."""

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_add_component(self):
            """Test bulk-adding this component to devices/virtual-machines."""
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["add"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            initial_count = self._get_queryset().count()

            data = (self.bulk_add_data or self.bulk_create_data).copy()

            # Load the device-bulk-add or virtualmachine-bulk-add form
            if "device" in data:
                url = reverse(f"dcim:device_bulk_add_{self.model._meta.model_name}")
                request = {
                    "path": url,
                    "data": testing.post_data({"pk": data["device"]}),
                }
            else:
                url = reverse(f"virtualization:virtualmachine_bulk_add_{self.model._meta.model_name}")
                request = {
                    "path": url,
                    "data": testing.post_data({"pk": data["virtual_machine"]}),
                }
            self.assertHttpStatus(self.client.post(**request), 200)

            # Post to the device-bulk-add or virtualmachine-bulk-add form to create records
            if "device" in data:
                data["pk"] = data.pop("device")
            else:
                data["pk"] = data.pop("virtual_machine")
            data["_create"] = ""
            request["data"] = testing.post_data(data)
            self.assertHttpStatus(self.client.post(**request), 302)

            updated_count = self._get_queryset().count()
            self.assertEqual(updated_count, initial_count + self.bulk_create_count)

            matching_count = 0
            for instance in self._get_queryset().all():
                try:
                    self.assertInstanceEqual(instance, (self.bulk_add_data or self.bulk_create_data))
                    matching_count += 1
                except AssertionError:
                    pass
            self.assertEqual(matching_count, self.bulk_create_count)

        @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
        def test_bulk_rename(self):
            obj_perm = users_models.ObjectPermission(name="Test permission", actions=["change"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            objects = self.selected_objects
            pk_list = [obj.pk for obj in objects]
            # Apply button not yet clicked
            data = {"pk": pk_list}
            data.update(self.rename_data)
            verbose_name_plural = self.model._meta.verbose_name_plural

            with self.subTest("Assert device name in HTML"):
                response = self.client.post(self._get_url("bulk_rename"), data)
                message = (
                    f"Renaming {len(objects)} {helpers.bettertitle(verbose_name_plural)} "
                    f"on {self.selected_objects_parent_name}"
                )
                self.assertInHTML(message, response.content.decode(response.charset))

            with self.subTest("Assert update successfully"):
                data["_apply"] = True  # Form Apply button
                response = self.client.post(self._get_url("bulk_rename"), data)
                self.assertHttpStatus(response, 302)
                queryset = self._get_queryset().filter(pk__in=pk_list)
                for instance in objects:
                    self.assertEqual(queryset.get(pk=instance.pk).name, f"{instance.name}X")

            with self.subTest("Assert if no valid objects selected return with error"):
                for values in ([], [str(uuid.uuid4())]):
                    data["pk"] = values
                    response = self.client.post(self._get_url("bulk_rename"), data, follow=True)
                    expected_message = f"No valid {verbose_name_plural} were selected."
                    self.assertIn(expected_message, response.content.decode(response.charset))
