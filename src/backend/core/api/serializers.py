"""Client serializers for the core app."""
from rest_framework import serializers

from core import models


class UserSerializer(serializers.ModelSerializer):
    """Serialize users."""

    class Meta:
        model = models.User
        fields = ["id", "email"]
        read_only_fields = ["id", "email"]
