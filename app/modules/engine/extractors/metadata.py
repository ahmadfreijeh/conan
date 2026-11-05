"""Step 3: read EXIF and guess the photo's source from its file name.

Reads from the untouched original bytes (Step 1), so nothing is lost.
"""

import io
from datetime import datetime

from PIL import Image

from app.modules.engine.constants import (
    DATETIME,
    DATETIME_ORIGINAL,
    EXIF_IFD,
    FLASH,
    FOCAL_LENGTH,
    GPS_ALT,
    GPS_ALT_REF,
    GPS_DIRECTION,
    GPS_IFD,
    GPS_LAT,
    GPS_LAT_REF,
    GPS_LON,
    GPS_LON_REF,
    LENS_MODEL,
    MAKE,
    MODEL,
    OFFSET_TIME_ORIGINAL,
    ORIENTATION,
    SOFTWARE,
    SOURCE_PATTERNS,
    STRIPPING_SOURCES,
)
from app.modules.engine.helpers import to_float, to_text
from app.modules.engine.schemas import GPS, LoadedImage, Metadata


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
    meaningful = {k for k in exif if k not in (ORIENTATION, EXIF_IFD, GPS_IFD)}
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
        device_make=to_text(exif.get(MAKE)),
        device_model=to_text(exif.get(MODEL)),
        lens=to_text(exif_ifd.get(LENS_MODEL)),
        software=to_text(exif.get(SOFTWARE)),
        focal_length_mm=to_float(exif_ifd.get(FOCAL_LENGTH)),
        flash_fired=bool(int(flash) & 1) if flash is not None else None,  # bit 0 = fired
    )


def _gps(ifd: dict) -> GPS | None:
    lat = _dms_to_decimal(ifd.get(GPS_LAT), ifd.get(GPS_LAT_REF), negative="S")
    lon = _dms_to_decimal(ifd.get(GPS_LON), ifd.get(GPS_LON_REF), negative="W")
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:  # placeholder some apps write, not a real location
        return None
    alt = to_float(ifd.get(GPS_ALT))
    if alt is not None and ifd.get(GPS_ALT_REF) in (1, b"\x01"):  # 1 = below sea level
        alt = -alt
    return GPS(lat=lat, lon=lon, altitude_m=alt, direction_deg=to_float(ifd.get(GPS_DIRECTION)))


def _dms_to_decimal(dms, ref, negative: str) -> float | None:
    """Degrees/minutes/seconds -> decimal degrees; S and W are negative."""
    try:
        d, m, s = (float(x) for x in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = d + m / 60 + s / 3600
    return -value if to_text(ref) == negative else value


def _datetime(value, offset) -> datetime | None:
    text = to_text(value)
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    off = to_text(offset)
    if off:
        try:
            dt = datetime.strptime(f"{text}{off}", "%Y:%m:%d %H:%M:%S%z")
        except ValueError:
            pass
    return dt
