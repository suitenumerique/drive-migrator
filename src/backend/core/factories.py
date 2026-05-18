# ruff: noqa: S311
"""
Core application factories
"""
from django.conf import settings
from django.contrib.auth.hashers import make_password

import factory.fuzzy
from faker import Faker

from core import models


class UserFactory(factory.django.DjangoModelFactory):
    """A factory to random users for testing purposes."""

    class Meta:
        model = models.User

    sub = factory.Sequence(lambda n: f"user{n!s}")
    email = factory.Faker("email")
    language = factory.fuzzy.FuzzyChoice([lang[0] for lang in settings.LANGUAGES])
    password = make_password("password")
    oidc_access_token = ""
    oidc_refresh_token = ""
    oidc_token_expires_at = None
    resana_access_token = ""
    resana_refresh_token = ""
    resana_token_expires_at = None


class WorkspaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Workspace

    title = factory.Faker("bs")
    source_id = factory.Faker("uuid4")
    source_type = "osmose"
