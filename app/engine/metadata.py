"""Step 3: read EXIF and guess the photo's source from its file name.

Reads from the untouched original bytes (Step 1), so nothing is lost.
"""

import io
import re
from datetime import datetime

from PIL import Image

from app.engine.loader import LoadedImage
from app.engine.payload import GPS, Metadata

# EXIF tag ids (see the EXIF spec / PIL.ExifTags)
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
MAKE, MODEL, SOFTWARE, DATETIME = 271, 272, 305, 306
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 36867, 36881
FLASH, FOCAL_LENGTH, LENS_MODEL = 37385, 37386, 42036
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4
GPS_ALT_REF, GPS_ALT, GPS_DIRECTION = 5, 6, 17

# File name pattern -> source. Order matters: first match wins.
SOURCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("whatsapp", re.compile(r"^(IMG-\d{8}-WA\d+|WhatsApp Image)", re.I)),
    ("screenshot", re.compile(r"^(screenshot|screen shot)", re.I)),
    ("facebook", re.compile(r"^FB_IMG_", re.I)),
    ("telegram", re.compile(r"^photo_\d{4}-\d{2}-\d{2}", re.I)),
    ("camera", re.compile(r"^(IMG|DSC|DSCN|PXL|MVIMG|DCIM)[_-]?\d", re.I)),
]
# Sources that remove EXIF or never had camera EXIF.
STRIPPING_SOURCES = {"whatsapp", "screenshot", "facebook", "telegram"}


def guess_source(filename: str) -> str:
    for source, pattern in SOURCE_PATTERNS:
        if pattern.search(filename):
            return source
    return "unknown"


def extract_metadata(loaded: LoadedImage) -> Metadata:
    source = guess_source(loaded.path.name)
    exif = Image.open(io.BytesIO(loaded.original_bytes)).getexif()
    exif_ifd = exif.get_ifd(EXIF_IFD)
    gps_ifd = exif.get_ifd(GPS_IFD)

    # Orientation alone (set by many apps) doesn't count as real camera EXIF.
    meaningful = {k for k in exif if k not in (0x0112, EXIF_IFD, GPS_IFD)}
    has_exif = bool(meaningful or exif_ifd or gps_ifd)

    flash = exif_ifd.get(FLASH)
    return Metadata(
        has_exif=has_exif,
        likely_stripped=not has_exif or source in STRIPPING_SOURCES,
        source_guess=source,
        gps=_gps(gps_ifd),
        taken_at=_datetime(
            exif_ifd.get(DATETIME_ORIGINAL) or exif.get(DATETIME),
            exif_ifd.get(OFFSET_TIME_ORIGINAL),
        ),
        device_make=_text(exif.get(MAKE)),
        device_model=_text(exif.get(MODEL)),
        lens=_text(exif_ifd.get(LENS_MODEL)),
        software=_text(exif.get(SOFTWARE)),
        focal_length_mm=_float(exif_ifd.get(FOCAL_LENGTH)),
        flash_fired=bool(int(flash) & 1) if flash is not None else None,  # bit 0 = fired
    )


def _gps(ifd: dict) -> GPS | None:
    lat = _dms_to_decimal(ifd.get(GPS_LAT), ifd.get(GPS_LAT_REF), negative="S")
    lon = _dms_to_decimal(ifd.get(GPS_LON), ifd.get(GPS_LON_REF), negative="W")
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:  # placeholder some apps write, not a real location
        return None
    alt = _float(ifd.get(GPS_ALT))
    if alt is not None and ifd.get(GPS_ALT_REF) in (1, b"\x01"):  # 1 = below sea level
        alt = -alt
    return GPS(lat=lat, lon=lon, altitude_m=alt, direction_deg=_float(ifd.get(GPS_DIRECTION)))


def _dms_to_decimal(dms, ref, negative: str) -> float | None:
    """Degrees/minutes/seconds -> decimal degrees; S and W are negative."""
    try:
        d, m, s = (float(x) for x in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = d + m / 60 + s / 3600
    return -value if _text(ref) == negative else value


def _datetime(value, offset) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    off = _text(offset)
    if off:
        try:
            dt = datetime.strptime(f"{text}{off}", "%Y:%m:%d %H:%M:%S%z")
        except ValueError:
            pass
    return dt


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    value = str(value).strip("\x00 ").strip()
    return value or None


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
