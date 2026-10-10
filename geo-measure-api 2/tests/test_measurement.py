"""Unit tests for the measurement logic (no HTTP, no DB)."""

import pytest
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiPoint, Point, Polygon, box

from app.models import MeasurementStatus
from app.services.measurement import choose_local_crs, measure_geometry, utm_epsg_for

WGS84 = CRS.from_epsg(4326)
UTM43N = CRS.from_epsg(32643)
GEOD = Geod(ellps="WGS84")


def to_crs(geom, src, dst):
    t = Transformer.from_crs(src, dst, always_xy=True)
    return shapely.transform(geom, lambda x, y: t.transform(x, y), interleaved=False)


def square_wgs84(size_m=100.0):
    """A size x size metre square near Bengaluru, expressed in lon/lat."""
    return to_crs(box(776_000, 1_430_000, 776_000 + size_m, 1_430_000 + size_m), UTM43N, WGS84)


@pytest.mark.parametrize(
    "lon,lat,expected",
    [(77.59, 12.97, 32643), (-0.1, 51.5, 32630), (151.2, -33.9, 32756), (-180, 0, 32601), (180, 0, 32660)],
)
def test_utm_zone(lon, lat, expected):
    assert utm_epsg_for(lon, lat) == expected


def test_polygon_area_from_lonlat_is_in_square_metres():
    r = measure_geometry(square_wgs84(100), WGS84)
    assert r.status == MeasurementStatus.MEASURED
    assert r.area_m2 == pytest.approx(10_000, rel=1e-6)
    assert r.perimeter_m == pytest.approx(400, rel=1e-6)
    assert r.projected_crs == "EPSG:32643"


def test_naive_degree_area_would_be_wrong():
    """The problem we are solving: area in 'square degrees' is meaningless."""
    assert square_wgs84(100).area < 1e-5


def test_linestring_length():
    line = to_crs(LineString([(776_000, 1_430_000), (777_000, 1_430_000)]), UTM43N, WGS84)
    r = measure_geometry(line, WGS84)
    assert r.length_m == pytest.approx(1000, rel=1e-6)
    assert r.area_m2 is None


def test_web_mercator_input_is_not_distorted():
    sq_3857 = to_crs(square_wgs84(100), WGS84, CRS.from_epsg(3857))
    assert sq_3857.area > 10_400  # measuring directly in 3857 would overstate by ~5% here
    r = measure_geometry(sq_3857, CRS.from_epsg(3857))
    assert r.area_m2 == pytest.approx(10_000, rel=1e-6)


def test_southern_hemisphere_uses_south_utm_zone():
    poly = box(151.20, -33.87, 151.21, -33.86)  # Sydney
    r = measure_geometry(poly, WGS84)
    assert r.projected_crs == "EPSG:32756"
    geodesic_area = abs(GEOD.geometry_area_perimeter(poly)[0])
    assert r.area_m2 == pytest.approx(geodesic_area, rel=1e-3)


def test_very_wide_polygon_uses_equal_area_projection():
    poly = box(70, 10, 85, 20)  # ~15 degrees wide, spans 3 UTM zones
    assert "+proj=laea" in choose_local_crs(poly, for_area=True).srs
    r = measure_geometry(poly, WGS84)
    assert "+proj=laea" in r.projected_crs
    # Reference: ellipsoidal (geodesic) area of the same, densified, outline.
    geodesic_area = abs(GEOD.geometry_area_perimeter(shapely.segmentize(poly, 0.01))[0])
    assert r.area_m2 == pytest.approx(geodesic_area, rel=1e-5)


def test_very_long_line_matches_geodesic_length():
    line = LineString([(70, 15), (90, 25)])  # ~2,300 km, spans several UTM zones
    r = measure_geometry(line, WGS84)
    assert "+proj=aeqd" in r.projected_crs
    geodesic = GEOD.geometry_length(shapely.segmentize(line, 0.01))
    assert r.length_m == pytest.approx(geodesic, rel=5e-3)


def test_point_needs_no_measurement():
    r = measure_geometry(Point(77.5, 12.9), WGS84)
    assert r.status == MeasurementStatus.NOT_APPLICABLE
    assert r.area_m2 is None and r.length_m is None


def test_multipoint_needs_no_measurement():
    r = measure_geometry(MultiPoint([(77.5, 12.9), (77.6, 12.9)]), WGS84)
    assert r.status == MeasurementStatus.NOT_APPLICABLE


def test_geometry_collection_is_unsupported_not_a_crash():
    gc = GeometryCollection([Point(77.5, 12.9), LineString([(77.5, 12.9), (77.6, 12.9)])])
    r = measure_geometry(gc, WGS84)
    assert r.status == MeasurementStatus.UNSUPPORTED
    assert "GeometryCollection" in r.note


def test_empty_and_missing_geometry_are_skipped():
    assert measure_geometry(None, WGS84).status == MeasurementStatus.SKIPPED
    assert measure_geometry(Polygon(), WGS84).status == MeasurementStatus.SKIPPED


def test_invalid_bowtie_polygon_is_repaired():
    # Self-intersecting "bow tie" over a 100 m square: two triangles of 2,500 m2 each.
    x, y = 776_000, 1_430_000
    bowtie = Polygon([(x, y), (x + 100, y + 100), (x + 100, y), (x, y + 100)])
    assert not bowtie.is_valid
    r = measure_geometry(to_crs(bowtie, UTM43N, WGS84), WGS84)
    assert r.status == MeasurementStatus.MEASURED
    assert r.area_m2 == pytest.approx(5_000, rel=1e-4)
    assert "repaired" in r.note
