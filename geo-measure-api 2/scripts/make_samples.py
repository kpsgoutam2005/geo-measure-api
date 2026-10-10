"""Generate sample input files with KNOWN measurements, for demos and manual testing.

    python scripts/make_samples.py            # writes into ./samples

Shapes are built in UTM zone 43N (EPSG:32643, around Bengaluru) where sizes in
metres are exact, then saved in other CRSs - so a correct API must return
the original metric sizes back.
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon, box

UTM43N = "EPSG:32643"
X0, Y0 = 776_000, 1_430_000  # a spot near Bengaluru in UTM 43N


def plots_gdf() -> gpd.GeoDataFrame:
    """Polygons: 100 m x 100 m (1 ha), 200 m x 50 m (1 ha), and a right triangle with legs 300 m and 400 m (60,000 m2)."""
    return gpd.GeoDataFrame(
        {
            "plot_id": ["P-001", "P-002", "P-003"],
            "owner": ["Asha", "Ravi", "Meena"],
            "exp_area_m": [10_000.0, 10_000.0, 60_000.0],
        },
        geometry=[
            box(X0, Y0, X0 + 100, Y0 + 100),
            box(X0 + 500, Y0, X0 + 700, Y0 + 50),
            Polygon([(X0, Y0 + 500), (X0 + 300, Y0 + 500), (X0, Y0 + 900)]),
        ],
        crs=UTM43N,
    )


def roads_gdf() -> gpd.GeoDataFrame:
    """Lines: 1000 m straight, and an L-shape 300 m + 400 m = 700 m."""
    return gpd.GeoDataFrame(
        {"road": ["Main St", "Lake Rd"], "exp_len_m": [1000.0, 700.0]},
        geometry=[
            LineString([(X0, Y0 - 200), (X0 + 1000, Y0 - 200)]),
            LineString([(X0, Y0 - 400), (X0 + 300, Y0 - 400), (X0 + 300, Y0 - 800)]),
        ],
        crs=UTM43N,
    )


def zip_shapefile(gdf: gpd.GeoDataFrame, name: str, out_zip: Path, drop: tuple[str, ...] = ()) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        shp = Path(tmp) / f"{name}.shp"
        gdf.to_file(shp, driver="ESRI Shapefile", engine="pyogrio")
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(Path(tmp).iterdir()):
                if f.suffix.lower() not in drop:
                    zf.write(f, f.name)


def write_kml(out_path: Path) -> None:
    plots = plots_gdf()
    roads = roads_gdf()
    survey = gpd.GeoDataFrame(
        {
            "Name": ["Field A (1 ha)", "Boundary road (1 km)", "Survey marker"],
            "Description": ["100 m x 100 m", "straight east-west line", "control point"],
        },
        geometry=[plots.geometry.iloc[0], roads.geometry.iloc[0], Point(X0 + 50, Y0 + 50)],
        crs=UTM43N,
    ).to_crs("EPSG:4326")
    survey.to_file(out_path, driver="KML", engine="pyogrio")


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Geographic CRS (lat/lon) - the case where naive code gets it wrong.
    zip_shapefile(plots_gdf().to_crs("EPSG:4326"), "farm_plots", out_dir / "farm_plots_wgs84.zip")
    # Already-projected CRS.
    zip_shapefile(roads_gdf(), "roads", out_dir / "roads_utm43n.zip")
    # Web Mercator - projected, but distorted; must NOT be measured directly.
    zip_shapefile(plots_gdf().to_crs("EPSG:3857"), "farm_plots_3857", out_dir / "farm_plots_webmercator.zip")
    # Broken archive: missing .shx -> should be rejected with HTTP 400.
    zip_shapefile(plots_gdf(), "broken", out_dir / "broken_missing_shx.zip", drop=(".shx",))
    write_kml(out_dir / "survey.kml")
    print(f"Samples written to {out_dir.resolve()}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples"))
