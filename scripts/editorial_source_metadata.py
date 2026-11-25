"""Format-independent source metadata for TECH MASTER optics.

Reads camera/lens metadata from RAW and rendered sources without making the
file extension the authority. Missing fields stay missing; optical stages must
HOLD rather than guess a camera/lens identity.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import math

from PIL import Image, ExifTags
import tifffile

try:
    import exifread
except Exception:  # optional fallback; production requirements install it
    exifread = None

try:
    import rawpy
except Exception:
    rawpy = None


def _text(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    value = str(value).replace("\x00", "").strip()
    return value or None


def _float(value):
    if value is None:
        return None
    try:
        if isinstance(value, Fraction):
            value = float(value)
        elif isinstance(value, tuple) and len(value) == 2 and value[1]:
            value = float(value[0]) / float(value[1])
        elif hasattr(value, "num") and hasattr(value, "den") and value.den:
            value = float(value.num) / float(value.den)
        elif hasattr(value, "numerator") and hasattr(value, "denominator") and value.denominator:
            value = float(value.numerator) / float(value.denominator)
        else:
            value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _merge(base, values, source):
    changed = False
    for key, value in values.items():
        if value is None:
            continue
        if key not in base or base[key] in (None, ""):
            base[key] = value
            changed = True
    if changed and source not in base["metadata_sources"]:
        base["metadata_sources"].append(source)


def _pillow(path: Path):
    values = {}
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return values
            try:
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            except Exception:
                exif_ifd = {}
            def get(tag):
                return exif.get(tag, exif_ifd.get(tag))
            values = {
                "make": _text(get(271)),
                "camera": _text(get(272)),
                "lens_make": _text(get(42035)),
                "lens": _text(get(42036)),
                "focal_mm": _float(get(37386)),
                "aperture": _float(get(33437)),
                "focus_distance_m": _float(get(37382)),
                "focal_35mm": _float(get(41989)),
            }
    except Exception:
        pass
    return values


def _tiff(path: Path):
    values = {}
    try:
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            tags = page.tags
            exif_tag = tags.get("ExifTag")
            exif = exif_tag.value if exif_tag is not None and isinstance(exif_tag.value, dict) else {}
            def tag(name, code=None):
                if name in tags:
                    return tags[name].value
                if code is not None:
                    return exif.get(code)
                return None
            values = {
                "make": _text(tag("Make", 271)),
                "camera": _text(tag("Model", 272)),
                "lens_make": _text(exif.get("LensMake", exif.get(42035))),
                "lens": _text(exif.get("LensModel", exif.get(42036))),
                "focal_mm": _float(exif.get("FocalLength", exif.get(37386))),
                "aperture": _float(exif.get("FNumber", exif.get(33437))),
                "focus_distance_m": _float(exif.get("SubjectDistance", exif.get(37382))),
                "focal_35mm": _float(exif.get("FocalLengthIn35mmFilm", exif.get(41989))),
            }
    except Exception:
        pass
    return values


def _exifread(path: Path):
    if exifread is None:
        return {}
    try:
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, details=False, strict=False)
        def get(*names):
            for name in names:
                if name in tags:
                    return tags[name].values[0] if getattr(tags[name], "values", None) else tags[name]
            return None
        return {
            "make": _text(get("Image Make", "EXIF Make")),
            "camera": _text(get("Image Model", "EXIF Model")),
            "lens_make": _text(get("EXIF LensMake")),
            "lens": _text(get("EXIF LensModel")),
            "focal_mm": _float(get("EXIF FocalLength")),
            "aperture": _float(get("EXIF FNumber")),
            "focus_distance_m": _float(get("EXIF SubjectDistance")),
            "focal_35mm": _float(get("EXIF FocalLengthIn35mmFilm")),
        }
    except Exception:
        return {}


def _rawpy(path: Path):
    if rawpy is None:
        return {}
    try:
        with rawpy.imread(str(path)) as raw:
            meta = getattr(raw, "metadata", None)
            if meta is None:
                return {}
            def attr(*names):
                for name in names:
                    value = getattr(meta, name, None)
                    if value not in (None, ""):
                        return value
                return None
            return {
                "make": _text(attr("make", "camera_make")),
                "camera": _text(attr("model", "camera_model")),
                "lens_make": _text(attr("lens_make", "lens_maker")),
                "lens": _text(attr("lens", "lens_model")),
                "focal_mm": _float(attr("focal_len", "focal_length")),
                "aperture": _float(attr("aperture", "fnumber")),
                "focus_distance_m": _float(attr("focus_dist", "focus_distance")),
            }
    except Exception:
        return {}


def extract_source_metadata(path: Path, source_kind: str):
    result = {
        "schema": 1,
        "file_extension": path.suffix.lower().lstrip("."),
        "source_kind": source_kind,
        "make": None,
        "camera": None,
        "lens_make": None,
        "lens": None,
        "focal_mm": None,
        "aperture": None,
        "focus_distance_m": None,
        "focal_35mm": None,
        "metadata_sources": [],
    }

    # Rendered sources: preserve embedded EXIF/TIFF metadata if present.
    _merge(result, _pillow(path), "PIL_EXIF")
    _merge(result, _tiff(path), "TIFF_EXIF")

    # RAW containers vary by manufacturer. ExifRead and LibRaw metadata fill
    # different gaps, so both are evidence sources rather than format rules.
    _merge(result, _exifread(path), "EXIFREAD")
    if source_kind == "CAMERA_RAW":
        _merge(result, _rawpy(path), "LIBRAW_METADATA")

    result["camera_identity_complete"] = bool(result["make"] and result["camera"])
    result["lens_identity_complete"] = bool(result["lens"])
    result["optics_identity_complete"] = bool(
        result["camera_identity_complete"]
        and result["lens_identity_complete"]
        and isinstance(result["focal_mm"], (int, float))
        and result["focal_mm"] > 0
    )
    result["vignetting_metadata_complete"] = bool(
        result["optics_identity_complete"]
        and isinstance(result["aperture"], (int, float))
        and result["aperture"] > 0
    )
    return result
