"""Area / length measurement with correct CRS handling.

Rule: never measure in degrees. Every geometry is first brought to WGS84
(lon/lat) and then projected into a *local* metric CRS chosen for that
feature, and only then measured.

Projection strategy (per feature):
  1. Feature fits inside a UTM zone's useful width (<= 6 deg wide) and is not
     polar (|lat| <= 84)  -> the WGS84 / UTM zone of its centroid
     (EPSG:326xx north, EPSG:327xx south). Scale error < 0.1 %.
  2. Otherwise (very wide or polar features) -> a custom projection centred on
     the feature: Lambert Azimuthal Equal-Area for polygons (area is preserved
     exactly), Azimuthal Equidistant for lines.

Why reproject to WGS84 first instead of measuring in the file's own projected
CRS? Because a "projected" file may use a CRS that is badly distorted for
measuring (Web Mercator, EPSG:3857, overstates area by 1/cos^2(lat) — about
5 % in Bengaluru, 30 % in Delhi, 4x at 60 deg N). Going through WGS84 makes the
result independent of how the data happened to be stored.

Before projecting, edges are densified to <= 0.01 deg so that long edges keep
their shape after projection (matters only for very large features).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from pyproj import CRS, Transformer
import shapely
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from app.models import MeasurementStatus

WGS84 = CRS.from_epsg(4326)

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
NO_MEASURE_TYPES = {"Point", "MultiPoint"}

UTM_MAX_WIDTH_DEG = 6.0
UTM_MAX_ABS_LAT = 84.0
# Before projecting, long edges are split into <= 0.01 deg (~1 km) pieces so that
# a straight edge in lon/lat stays the same edge after projection. Without this a
# 15-degree-wide polygon is off by ~0.1 %; with it, the error is < 0.0001 %.
DENSIFY_MAX_SEGMENT_DEG = 0.01


@dataclass
class MeasurementResult:
    status: MeasurementStatus
    projected_crs: Optional[str] = None
    area_m2: Optional[float] = None
    perimeter_m: Optional[float] = None
    length_m: Optional[float] = None
    note: Optional[str] = None


def crs_label(crs: Optional[CRS]) -> Optional[str]:
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


@lru_cache(maxsize=256)
def _transformer(src: CRS, dst: CRS) -> Transformer:
    # always_xy: treat coordinates as (x=lon/easting, y=lat/northing) regardless
    # of the CRS's official axis order (EPSG:4326 is officially lat, lon).
    return Transformer.from_crs(src, dst, always_xy=True)


def _reproject(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    if src == dst:
        return geom
    t = _transformer(src, dst)
    # 2D only: heights (e.g. KML altitude) do not affect planimetric area/length.
    return shapely.transform(geom, lambda x, y: t.transform(x, y), interleaved=False)


def utm_epsg_for(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def choose_local_crs(geom_wgs84: BaseGeometry, for_area: bool) -> CRS:
    """Pick a metric CRS appropriate for measuring this (lon/lat) geometry."""
    minx, miny, maxx, maxy = geom_wgs84.bounds
    centre = geom_wgs84.centroid
    lon, lat = centre.x, centre.y

    if (maxx - minx) <= UTM_MAX_WIDTH_DEG and max(abs(miny), abs(maxy)) <= UTM_MAX_ABS_LAT:
        return CRS.from_epsg(utm_epsg_for(lon, lat))

    proj = "laea" if for_area else "aeqd"
    return CRS.from_proj4(
        f"+proj={proj} +lat_0={lat:.8f} +lon_0={lon:.8f} +x_0=0 +y_0=0 "
        "+datum=WGS84 +units=m +no_defs"
    )


def _polygonal_part(geom: BaseGeometry) -> BaseGeometry:
    """make_valid can return a GeometryCollection; keep only the polygon parts."""
    if geom.geom_type in AREA_TYPES:
        return geom
    polys: list[Polygon] = []
    for part in getattr(geom, "geoms", []):
        if isinstance(part, Polygon):
            polys.append(part)
        elif isinstance(part, MultiPolygon):
            polys.extend(part.geoms)
    return MultiPolygon(polys)


def measure_geometry(geom: Optional[BaseGeometry], src_crs: CRS) -> MeasurementResult:
    """Measure one geometry. Never raises for bad/unsupported input; returns a status instead."""
    if geom is None or geom.is_empty:
        return MeasurementResult(MeasurementStatus.SKIPPED, note="Feature has no geometry.")

    gtype = geom.geom_type
    if gtype in NO_MEASURE_TYPES:
        return MeasurementResult(
            MeasurementStatus.NOT_APPLICABLE, note=f"{gtype} has no area or length."
        )
    if gtype not in AREA_TYPES | LENGTH_TYPES:
        return MeasurementResult(
            MeasurementStatus.UNSUPPORTED,
            note=f"Measurement is not supported for geometry type {gtype}.",
        )

    is_area = gtype in AREA_TYPES
    notes: list[str] = []

    if is_area and not geom.is_valid:
        geom = _polygonal_part(make_valid(geom))
        notes.append("Invalid polygon was repaired before measuring.")
        if geom.is_empty:
            return MeasurementResult(
                MeasurementStatus.ERROR, note="Polygon is invalid and could not be repaired."
            )

    geom_wgs84 = shapely.segmentize(_reproject(geom, src_crs, WGS84), DENSIFY_MAX_SEGMENT_DEG)
    local_crs = choose_local_crs(geom_wgs84, for_area=is_area)
    projected = _reproject(geom_wgs84, WGS84, local_crs)

    result = MeasurementResult(
        MeasurementStatus.MEASURED,
        projected_crs=crs_label(local_crs) if local_crs.to_epsg() else local_crs.srs,
        note=" ".join(notes) or None,
    )
    if is_area:
        result.area_m2 = projected.area
        result.perimeter_m = projected.length
    else:
        result.length_m = projected.length
    return result
