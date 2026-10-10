"""Reading uploaded geospatial files into GeoDataFrames.

Two input formats are supported:
  * a .zip archive containing exactly one ESRI Shapefile (.shp + .shx + .dbf, .prj recommended)
  * a .kml file (all layers / folders are read)
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path, PurePosixPath

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS

from app.config import settings
from app.exceptions import GeoFileError, UnsupportedFileType
from app.models import FileType

logger = logging.getLogger(__name__)

WGS84 = CRS.from_epsg(4326)
SHAPEFILE_REQUIRED = (".shp", ".shx", ".dbf")

EXTENSION_TO_TYPE = {".zip": FileType.SHAPEFILE, ".kml": FileType.KML}


def detect_file_type(filename: str) -> FileType:
    suffix = Path(filename or "").suffix.lower()
    try:
        return EXTENSION_TO_TYPE[suffix]
    except KeyError:
        raise UnsupportedFileType(
            f"Unsupported file type '{suffix or '(none)'}'. "
            "Upload a .zip containing a Shapefile, or a .kml file."
        ) from None


# --------------------------------------------------------------------------- #
# Shapefile (.zip)
# --------------------------------------------------------------------------- #
def _shapefile_members(zf: zipfile.ZipFile) -> tuple[str, list[str]]:
    """Validate archive contents and return (shp member name, all member names).

    Ignores macOS metadata (__MACOSX/, ._ files) and directories.
    """
    names = [
        n for n in zf.namelist()
        if not n.endswith("/")
        and not n.startswith("__MACOSX/")
        and not PurePosixPath(n).name.startswith("._")
    ]
    shp_files = [n for n in names if n.lower().endswith(".shp")]
    if not shp_files:
        raise GeoFileError("The zip archive does not contain a .shp file.")
    if len(shp_files) > 1:
        raise GeoFileError(
            f"The zip archive contains {len(shp_files)} shapefiles; upload one shapefile per archive."
        )

    shp = shp_files[0]
    stem = shp[: -len(".shp")].lower()
    present = {n.lower() for n in names}
    missing = [ext for ext in SHAPEFILE_REQUIRED if f"{stem}{ext}" not in present]
    if missing:
        raise GeoFileError(
            f"Shapefile '{PurePosixPath(shp).name}' is missing required component(s): {', '.join(missing)}."
        )
    return shp, names


def validate_zip(path: Path) -> None:
    """Cheap structural check run at upload time, so obvious mistakes fail fast (HTTP 400)."""
    try:
        with zipfile.ZipFile(path) as zf:
            _shapefile_members(zf)
            total = sum(info.file_size for info in zf.infolist())
            if total > settings.max_extracted_bytes:
                raise GeoFileError("The zip archive is too large when extracted.")
    except zipfile.BadZipFile:
        raise GeoFileError("The uploaded file is not a valid zip archive.") from None


def _safe_extract(zf: zipfile.ZipFile, members: list[str], dest: Path) -> None:
    """Extract members, refusing any path that escapes `dest` (zip-slip protection)."""
    dest = dest.resolve()
    for name in members:
        target = (dest / name).resolve()
        if not target.is_relative_to(dest):
            raise GeoFileError(f"Unsafe path in zip archive: {name!r}")
        zf.extract(name, dest)


def read_shapefile_zip(path: Path, workdir: Path) -> gpd.GeoDataFrame:
    try:
        with zipfile.ZipFile(path) as zf:
            shp, members = _shapefile_members(zf)
            _safe_extract(zf, members, workdir)
    except zipfile.BadZipFile:
        raise GeoFileError("The uploaded file is not a valid zip archive.") from None

    shp_path = workdir / shp
    try:
        gdf = gpd.read_file(shp_path, engine="pyogrio")
    except Exception as exc:  # pyogrio raises several error types for corrupt data
        logger.warning("Failed to read shapefile %s: %s", shp_path, exc)
        raise GeoFileError(f"Could not read the shapefile: {exc}") from exc

    gdf["__layer__"] = PurePosixPath(shp).stem
    return _ensure_crs(gdf, has_prj=shp_path.with_suffix(".prj").exists()
                       or shp_path.with_suffix(".PRJ").exists())


# --------------------------------------------------------------------------- #
# KML
# --------------------------------------------------------------------------- #
def read_kml(path: Path) -> gpd.GeoDataFrame:
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:
        raise GeoFileError(f"Could not read the KML file: {exc}") from exc

    frames = []
    for layer_name, _geom_type in layers:
        try:
            layer = gpd.read_file(path, layer=layer_name, engine="pyogrio")
        except Exception as exc:
            raise GeoFileError(f"Could not read KML layer '{layer_name}': {exc}") from exc
        if len(layer):
            layer["__layer__"] = layer_name
            frames.append(layer)

    if not frames:
        return gpd.GeoDataFrame({"__layer__": []}, geometry=[], crs=WGS84)

    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    # KML is defined as WGS84 lon/lat by the OGC spec.
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    return gdf


# --------------------------------------------------------------------------- #
# CRS
# --------------------------------------------------------------------------- #
def _looks_geographic(gdf: gpd.GeoDataFrame) -> bool:
    if gdf.empty or gdf.geometry.isna().all():
        return True
    minx, miny, maxx, maxy = gdf.total_bounds
    return -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90


def _ensure_crs(gdf: gpd.GeoDataFrame, has_prj: bool) -> gpd.GeoDataFrame:
    if gdf.crs is not None:
        return gdf
    # No .prj: assume WGS84 only if the coordinates are plausibly lon/lat;
    # otherwise refuse, because guessing a projected CRS would give wrong numbers.
    if _looks_geographic(gdf):
        logger.info("Shapefile has no CRS (prj=%s); assuming EPSG:4326", has_prj)
        return gdf.set_crs(WGS84)
    raise GeoFileError(
        "The shapefile has no .prj file and its coordinates are not longitude/latitude, "
        "so its coordinate reference system cannot be determined. Include the .prj file."
    )


def read_geofile(path: Path, file_type: FileType, workdir: Path) -> gpd.GeoDataFrame:
    if file_type == FileType.SHAPEFILE:
        return read_shapefile_zip(path, workdir)
    if file_type == FileType.KML:
        return read_kml(path)
    raise UnsupportedFileType(f"Unsupported file type: {file_type}")  # pragma: no cover
