import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Files that only their owner may download, through Django (the web server never serves this folder)."""

    @property
    def base_location(self):
        return settings.PRIVATE_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)
