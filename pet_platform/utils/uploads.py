"""Saving user-uploaded files. Existing files in uploads/ keep their old names."""
import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename


def file_extension(filename):
    """Lower-case extension without the dot, or '' if there is none."""
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''


def make_upload_name(original_filename, prefix):
    """`<prefix>_<8 hex chars>[_<safe stem>][.<ext>]`.

    secure_filename() drops non-ASCII characters (e.g. Chinese file names), so the
    stem may come out empty; the uuid part keeps the name unique either way.
    """
    ext = file_extension(original_filename)
    stem = original_filename.rsplit('.', 1)[0] if ext else original_filename
    safe_stem = secure_filename(stem)
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    if safe_stem:
        name += f"_{safe_stem}"
    if ext:
        name += f".{ext}"
    return name


def save_upload(file, prefix):
    """Save a werkzeug FileStorage into UPLOAD_FOLDER and return the stored filename."""
    filename = make_upload_name(file.filename, prefix)
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
    return filename
