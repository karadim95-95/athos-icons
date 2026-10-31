import os

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Files that only their owner may download, through Django (the web server never serves this folder)."""

    @property
    def base_location(self):
        return settings.PRIVATE_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)


class FreshStaticStorage(StaticFilesStorage):
    """While developing: a file's address changes whenever the file does, so the browser never keeps
    showing an old stylesheet or script. (In production the file names carry a hash instead.)"""

    def url(self, name):
        url = super().url(name)
        path = finders.find(name)
        return f"{url}?v={int(os.path.getmtime(path))}" if path else url
