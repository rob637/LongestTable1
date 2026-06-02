"""Photo uploads — Pillow-optimized to WebP with thumbnails.

Pipeline:
- Auto-orient via EXIF
- Convert to RGB
- Resize: full image long-edge max 1920px, thumb long-edge max 600px
- Encode as WebP (quality 85 full / 80 thumb)
- Store in GCS when GCS_BUCKET is set, else local static/uploads/

Returns (filename, full_url, thumb_url).
"""
import io
import logging
import os
import secrets

log = logging.getLogger(__name__)

FULL_MAX = 1920
THUMB_MAX = 600
FULL_QUALITY = 85
THUMB_QUALITY = 80


def _local_dir():
    d = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _bucket_name():
    return os.environ.get("GCS_BUCKET", "").strip()


def _process(file_storage, ext: str):
    file_storage.stream.seek(0)
    raw = file_storage.stream.read()
    try:
        from PIL import Image, ImageOps
    except Exception:  # noqa: BLE001
        log.warning("Pillow unavailable; storing original bytes for .%s", ext)
        ct = file_storage.mimetype or f"image/{ext}"
        return raw, raw, ext, ct

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        full = img.copy()
        full.thumbnail((FULL_MAX, FULL_MAX), Image.LANCZOS)
        thumb = img.copy()
        thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)

        fbuf = io.BytesIO()
        full.save(fbuf, format="WEBP", quality=FULL_QUALITY, method=6)
        tbuf = io.BytesIO()
        thumb.save(tbuf, format="WEBP", quality=THUMB_QUALITY, method=6)
        return fbuf.getvalue(), tbuf.getvalue(), "webp", "image/webp"
    except Exception as e:  # noqa: BLE001
        log.warning("Pillow processing failed (%s); falling back to original bytes", e)
        ct = file_storage.mimetype or f"image/{ext}"
        return raw, raw, ext, ct


def upload(file_storage, *, ext: str) -> tuple[str, str, str]:
    """Process and store the image. Returns (filename, full_url, thumb_url)."""
    full_bytes, thumb_bytes, out_ext, content_type = _process(file_storage, ext.lower())
    token = secrets.token_hex(8)
    filename = f"{token}.{out_ext}"
    thumb_name = f"{token}_thumb.{out_ext}"

    bucket = _bucket_name()
    if bucket:
        from google.cloud import storage
        client = storage.Client()
        b = client.bucket(bucket)
        b.blob(f"photos/{filename}").upload_from_string(full_bytes, content_type=content_type)
        b.blob(f"photos/{thumb_name}").upload_from_string(thumb_bytes, content_type=content_type)
        base = f"https://storage.googleapis.com/{bucket}/photos"
        return filename, f"{base}/{filename}", f"{base}/{thumb_name}"

    d = _local_dir()
    with open(os.path.join(d, filename), "wb") as f:
        f.write(full_bytes)
    with open(os.path.join(d, thumb_name), "wb") as f:
        f.write(thumb_bytes)
    from flask import url_for
    return (
        filename,
        url_for("static", filename=f"uploads/{filename}"),
        url_for("static", filename=f"uploads/{thumb_name}"),
    )


def delete(filename: str) -> None:
    if not filename:
        return
    base, _, ext = filename.rpartition(".")
    thumb = f"{base}_thumb.{ext}" if base else ""
    bucket = _bucket_name()
    if bucket:
        try:
            from google.cloud import storage
            b = storage.Client().bucket(bucket)
            for name in (filename, thumb):
                if name:
                    try:
                        b.blob(f"photos/{name}").delete()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        return
    d = _local_dir()
    for name in (filename, thumb):
        if not name:
            continue
        try:
            os.remove(os.path.join(d, name))
        except OSError:
            pass
