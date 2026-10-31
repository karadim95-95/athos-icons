"""Image helpers: shrink phone photos, fix their rotation and make thumbnails."""

import os
import uuid
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps

FULL_SIZE = 1800
THUMB_SIZE = 640


@deconstructible
class upload_to:
    """Store uploads under a random name so customers' file names never leak."""

    def __init__(self, folder):
        self.folder = folder

    def __call__(self, instance, filename):
        ext = os.path.splitext(filename)[1].lower() or ".jpg"
        return f"{self.folder}/{uuid.uuid4().hex}{ext}"

    def __eq__(self, other):
        return isinstance(other, upload_to) and other.folder == self.folder


def validate_image_size(file):
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    if file.size > limit:
        raise ValidationError(_("Each image must be smaller than %(mb)s MB.") % {"mb": settings.MAX_UPLOAD_MB})


def _render(img, max_px):
    img = img.copy()
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1])
        else:
            background.paste(img.convert("RGB"))
        img = background
    out = BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
    return ContentFile(out.getvalue())


def process_image_field(instance, field_name, thumb_field_name=None):
    """
    Resize a freshly uploaded image in place (and optionally build a thumbnail).

    Only runs for new uploads, so editing a product later never re-compresses
    the same photo over and over.
    """
    field = getattr(instance, field_name)
    if not field or getattr(field, "_committed", True):
        return
    field.open()
    with Image.open(field) as img:
        img = ImageOps.exif_transpose(img)
        base = uuid.uuid4().hex
        field.save(f"{base}.jpg", _render(img, FULL_SIZE), save=False)
        if thumb_field_name:
            getattr(instance, thumb_field_name).save(f"{base}_t.jpg", _render(img, THUMB_SIZE), save=False)


def delete_files(*fields):
    for f in fields:
        if f and f.name:
            f.storage.delete(f.name)
