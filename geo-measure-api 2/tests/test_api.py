"""End-to-end tests through the HTTP API (upload -> info -> measurements)."""

import io
import zipfile

import pytest

from tests.conftest import upload


def _upload_ok(client, path):
    r = upload(client, path)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] in ("PENDING", "PROCESSING", "COMPLETED")
    # TestClient runs background tasks before returning, so processing is done now.
    info = client.get(f"/api/files/{body['id']}/").json()
    return info


def _measurements(client, file_id, **params):
    r = client.get(f"/api/files/{file_id}/measurements/", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# happy paths
# --------------------------------------------------------------------------- #
def test_shapefile_in_wgs84(client, samples):
    info = _upload_ok(client, samples / "farm_plots_wgs84.zip")
    assert info["status"] == "COMPLETED"
    assert info["file_type"] == "SHAPEFILE"
    assert info["feature_count"] == 3
    assert info["crs"] == "EPSG:4326"

    data = _measurements(client, info["id"])
    areas = [f["area_m2"] for f in data["results"]]
    assert areas == pytest.approx([10_000, 10_000, 60_000], rel=1e-6)
    assert data["summary"]["total_area_m2"] == pytest.approx(80_000, rel=1e-6)
    first = data["results"][0]
    assert first["geometry_type"] == "Polygon"
    assert first["projected_crs"] == "EPSG:32643"
    assert first["area_hectares"] == pytest.approx(1.0, rel=1e-6)
    assert first["properties"]["plot_id"] == "P-001"


def test_shapefile_already_projected(client, samples):
    info = _upload_ok(client, samples / "roads_utm43n.zip")
    assert info["crs"] == "EPSG:32643"
    data = _measurements(client, info["id"])
    assert [f["length_m"] for f in data["results"]] == pytest.approx([1000, 700], rel=1e-6)


def test_web_mercator_is_measured_correctly(client, samples):
    info = _upload_ok(client, samples / "farm_plots_webmercator.zip")
    assert info["crs"] == "EPSG:3857"
    areas = [f["area_m2"] for f in _measurements(client, info["id"])["results"]]
    assert areas == pytest.approx([10_000, 10_000, 60_000], rel=1e-6)


def test_kml_mixed_geometries(client, samples):
    info = _upload_ok(client, samples / "survey.kml")
    assert info["file_type"] == "KML"
    assert info["crs"] == "EPSG:4326"
    assert info["feature_count"] == 3

    results = _measurements(client, info["id"], include_geometry=True)["results"]
    by_type = {r["geometry_type"]: r for r in results}
    assert by_type["Polygon"]["area_m2"] == pytest.approx(10_000, rel=1e-6)
    assert by_type["LineString"]["length_m"] == pytest.approx(1_000, rel=1e-6)
    assert by_type["Point"]["measurement_status"] == "NOT_APPLICABLE"
    assert "area_m2" not in by_type["Point"]
    assert by_type["Polygon"]["geometry"]["type"] == "Polygon"
    assert by_type["Polygon"]["properties"]["Name"] == "Field A (1 ha)"


def test_filter_and_pagination(client, samples):
    info = _upload_ok(client, samples / "survey.kml")
    only_lines = _measurements(client, info["id"], geometry_type="linestring")
    assert only_lines["count"] == 1
    assert only_lines["results"][0]["geometry_type"] == "LineString"

    page = _measurements(client, info["id"], limit=1, offset=1)
    assert page["count"] == 3 and len(page["results"]) == 1
    assert page["results"][0]["index"] == 1
    assert page["summary"]["total_features"] == 3  # summary always covers the whole file


def test_geojson_features_endpoint(client, samples):
    info = _upload_ok(client, samples / "farm_plots_wgs84.zip")
    fc = client.get(f"/api/files/{info['id']}/features/").json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 3
    assert fc["features"][0]["properties"]["_measurement"]["area_m2"] == pytest.approx(10_000, rel=1e-6)


def test_list_and_delete(client, samples):
    info = _upload_ok(client, samples / "roads_utm43n.zip")
    listing = client.get("/api/files/").json()
    assert any(f["id"] == info["id"] for f in listing["results"])

    assert client.delete(f"/api/files/{info['id']}/").status_code == 204
    assert client.get(f"/api/files/{info['id']}/").status_code == 404


def test_zip_with_macos_metadata_and_subfolder(client, samples, tmp_path):
    """Zips made by macOS Finder put files in a folder and add __MACOSX entries."""
    src = zipfile.ZipFile(samples / "farm_plots_wgs84.zip")
    out = tmp_path / "mac_style.zip"
    with zipfile.ZipFile(out, "w") as zf:
        for name in src.namelist():
            zf.writestr(f"farm_plots/{name}", src.read(name))
            zf.writestr(f"__MACOSX/farm_plots/._{name}", b"junk")
    info = _upload_ok(client, out)
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == 3


def test_shapefile_without_prj_assumes_wgs84_for_lonlat(client, samples, tmp_path):
    src = zipfile.ZipFile(samples / "farm_plots_wgs84.zip")
    out = tmp_path / "no_prj.zip"
    with zipfile.ZipFile(out, "w") as zf:
        for name in src.namelist():
            if not name.endswith(".prj"):
                zf.writestr(name, src.read(name))
    info = _upload_ok(client, out)
    assert info["status"] == "COMPLETED"
    assert info["crs"] == "EPSG:4326"


def test_projected_shapefile_without_prj_fails_clearly(client, samples, tmp_path):
    src = zipfile.ZipFile(samples / "roads_utm43n.zip")
    out = tmp_path / "utm_no_prj.zip"
    with zipfile.ZipFile(out, "w") as zf:
        for name in src.namelist():
            if not name.endswith(".prj"):
                zf.writestr(name, src.read(name))
    info = _upload_ok(client, out)
    assert info["status"] == "FAILED"
    assert ".prj" in info["error"]
    r = client.get(f"/api/files/{info['id']}/measurements/")
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# error handling
# --------------------------------------------------------------------------- #
def test_missing_shx_rejected(client, samples):
    r = upload(client, samples / "broken_missing_shx.zip")
    assert r.status_code == 400
    assert ".shx" in r.json()["detail"]


def test_zip_without_shapefile_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    r = client.post("/api/files/", files={"file": ("data.zip", buf.getvalue())})
    assert r.status_code == 400
    assert "does not contain a .shp" in r.json()["detail"]


def test_corrupt_zip_rejected(client):
    r = client.post("/api/files/", files={"file": ("data.zip", b"this is not a zip")})
    assert r.status_code == 400
    assert "not a valid zip" in r.json()["detail"]


def test_unsupported_extension(client):
    r = client.post("/api/files/", files={"file": ("data.geojson", b"{}")})
    assert r.status_code == 415


def test_empty_upload(client):
    r = client.post("/api/files/", files={"file": ("empty.kml", b"")})
    assert r.status_code == 400


def test_upload_too_large(client):
    big = b"0" * (6 * 1024 * 1024)  # limit is 5 MB in tests
    r = client.post("/api/files/", files={"file": ("big.kml", big)})
    assert r.status_code == 413


def test_invalid_kml_marks_file_failed(client):
    r = client.post("/api/files/", files={"file": ("bad.kml", b"<kml><not closed")})
    assert r.status_code == 202
    info = client.get(f"/api/files/{r.json()['id']}/").json()
    assert info["status"] == "FAILED"
    assert info["error"]


def test_kml_with_unsupported_geometry_does_not_crash(client):
    kml = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>mixed</name><MultiGeometry>
    <Point><coordinates>77.5,12.9</coordinates></Point>
    <LineString><coordinates>77.5,12.9 77.51,12.9</coordinates></LineString>
  </MultiGeometry></Placemark>
  <Placemark><name>line</name>
    <LineString><coordinates>77.5,12.9 77.51,12.9</coordinates></LineString></Placemark>
</Document></kml>"""
    r = client.post("/api/files/", files={"file": ("mixed.kml", kml)})
    info = client.get(f"/api/files/{r.json()['id']}/").json()
    assert info["status"] == "COMPLETED"
    results = _measurements(client, info["id"])["results"]
    assert results[0]["measurement_status"] == "UNSUPPORTED"
    assert results[1]["measurement_status"] == "MEASURED"


def test_unknown_id_404(client):
    assert client.get("/api/files/does-not-exist/").status_code == 404
    assert client.get("/api/files/does-not-exist/measurements/").status_code == 404
