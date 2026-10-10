# Geospatial File Measurement API

A FastAPI service that accepts a **Shapefile (.zip)** or **KML** file, extracts every feature, and returns the
**area of polygons** and **length of lines** in metres, measured in a suitable projected coordinate system
(never in latitude/longitude degrees).

```
POST /api/files/                       upload + process a file
GET  /api/files/{id}/                  file info and processing status
GET  /api/files/{id}/measurements/     per-feature measurements + summary
```

---

## Setup

Requires **Python 3.10+**. `pyogrio` and `pyproj` ship pre-built wheels (with GDAL/PROJ bundled) for
Windows, macOS and Linux, so no system GDAL install is needed.

```bash
git clone <this-repo-url>
cd geo-measure-api

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger UI): http://127.0.0.1:8000/docs

Run the tests:

```bash
pytest
```

Generate sample files with **known** sizes (written to `samples/`; already included in the repo):

```bash
python scripts/make_samples.py
```

| Sample | CRS | Contents | Expected result |
|---|---|---|---|
| `farm_plots_wgs84.zip` | EPSG:4326 (lat/lon) | 3 polygons | 10,000 / 10,000 / 60,000 m² |
| `roads_utm43n.zip` | EPSG:32643 (UTM 43N) | 2 lines | 1,000 / 700 m |
| `farm_plots_webmercator.zip` | EPSG:3857 | same 3 polygons | 10,000 / 10,000 / 60,000 m² |
| `survey.kml` | EPSG:4326 | polygon + line + point | 10,000 m², 1,000 m, no measurement |
| `broken_missing_shx.zip` | – | shapefile without `.shx` | HTTP 400 |

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | Where uploads and the SQLite database are stored |
| `DATABASE_URL` | `sqlite:///<DATA_DIR>/app.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `MAX_UPLOAD_MB` | `50` | Maximum upload size |
| `MAX_EXTRACTED_MB` | `500` | Maximum total uncompressed size of a zip (zip-bomb guard) |

---

## API

### `POST /api/files/` — upload a file

Multipart form field `file`: a `.zip` containing **one** shapefile (`.shp`, `.shx`, `.dbf`, ideally `.prj`), or a `.kml`.

```bash
curl -F "file=@samples/farm_plots_wgs84.zip" http://127.0.0.1:8000/api/files/
```

`202 Accepted` — the file is stored and processed in the background:

```json
{
  "id": "b8ed2c6e618e4bb688f0500af77120a9",
  "filename": "farm_plots_wgs84.zip",
  "file_type": "SHAPEFILE",
  "size_bytes": 1088,
  "status": "PENDING",
  "feature_count": null,
  "crs": null,
  "error": null,
  "created_at": "2026-10-10T05:12:36.265221Z",
  "processed_at": null
}
```

Errors:

| Code | When |
|---|---|
| `400` | Empty file, corrupt zip, zip without `.shp`, missing `.shx`/`.dbf`, more than one shapefile |
| `413` | File larger than `MAX_UPLOAD_MB` |
| `415` | Extension other than `.zip` / `.kml` |

```json
{ "detail": "Shapefile 'broken.shp' is missing required component(s): .shx." }
```

### `GET /api/files/{id}/` — file information

```bash
curl http://127.0.0.1:8000/api/files/b8ed2c6e618e4bb688f0500af77120a9/
```

```json
{
  "id": "b8ed2c6e618e4bb688f0500af77120a9",
  "filename": "farm_plots_wgs84.zip",
  "file_type": "SHAPEFILE",
  "size_bytes": 1088,
  "status": "COMPLETED",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "error": null,
  "created_at": "2026-10-10T05:12:36.265221Z",
  "processed_at": "2026-10-10T05:12:36.300654Z"
}
```

`status` is one of `PENDING → PROCESSING → COMPLETED | FAILED`. When `FAILED`, `error` explains why
(e.g. *"The shapefile has no .prj file and its coordinates are not longitude/latitude…"*).

### `GET /api/files/{id}/measurements/` — measurements

Query parameters:

| Param | Default | Meaning |
|---|---|---|
| `limit`, `offset` | 100, 0 | Pagination |
| `geometry_type` | – | Filter, e.g. `Polygon`, `LineString` (case-insensitive) |
| `include_geometry` | `false` | Include each feature's GeoJSON geometry (in the source CRS) |
| `include_properties` | `true` | Include each feature's attributes |

```bash
curl "http://127.0.0.1:8000/api/files/<id>/measurements/?include_geometry=true"
```

```json
{
  "file_id": "77b95e981c4f4412a3369f161d73d0cb",
  "filename": "survey.kml",
  "source_crs": "EPSG:4326",
  "units": { "area": "square metres (m2) / hectares", "length": "metres (m) / kilometres" },
  "summary": {
    "total_features": 3,
    "by_status": { "MEASURED": 2, "NOT_APPLICABLE": 1 },
    "by_geometry_type": { "LineString": 1, "Point": 1, "Polygon": 1 },
    "total_area_m2": 10000.0,
    "total_length_m": 1000.0
  },
  "count": 3, "limit": 100, "offset": 0,
  "results": [
    {
      "index": 0,
      "layer": "survey",
      "geometry_type": "Polygon",
      "measurement_status": "MEASURED",
      "projected_crs": "EPSG:32643",
      "area_m2": 10000.0,
      "area_hectares": 1.0,
      "perimeter_m": 400.0,
      "geometry": { "type": "Polygon", "coordinates": [[[77.5445, 12.9231], "..."]] },
      "properties": { "Name": "Field A (1 ha)", "Description": "100 m x 100 m" }
    },
    {
      "index": 1, "layer": "survey", "geometry_type": "LineString",
      "measurement_status": "MEASURED", "projected_crs": "EPSG:32643",
      "length_m": 1000.0, "length_km": 1.0,
      "properties": { "Name": "Boundary road (1 km)", "Description": "straight east-west line" }
    },
    {
      "index": 2, "layer": "survey", "geometry_type": "Point",
      "measurement_status": "NOT_APPLICABLE", "note": "Point has no area or length.",
      "properties": { "Name": "Survey marker", "Description": "control point" }
    }
  ]
}
```

`measurement_status` values:

| Value | Meaning |
|---|---|
| `MEASURED` | Area (polygons) or length (lines) calculated |
| `NOT_APPLICABLE` | Point / MultiPoint – nothing to measure |
| `UNSUPPORTED` | e.g. GeometryCollection – reported, not crashed on |
| `SKIPPED` | Feature has no / empty geometry |
| `ERROR` | Unexpected failure for this one feature; the rest of the file is still processed |

Returns `409` if the file is still processing, `422` if processing failed, `404` for an unknown id.

### Extra endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/files/` | List uploads (paginated, newest first) |
| `GET /api/files/{id}/features/` | All features as a GeoJSON `FeatureCollection`, measurements under `properties._measurement` – can be dropped straight onto a map (e.g. geojson.io) |
| `DELETE /api/files/{id}/` | Delete a file, its stored upload and its results |
| `GET /health` | Liveness check |

---

## Architecture

### Application structure

```
app/
├── main.py              FastAPI app, lifespan (creates DB tables + upload dir)
├── config.py            Settings from environment variables
├── database.py          SQLAlchemy/SQLModel engine and session dependency
├── models.py            DB tables: GeoFile, Feature (+ status enums)
├── schemas.py           API response models (separate from DB tables)
├── exceptions.py        Domain errors with client-safe messages
├── api/
│   └── routes.py        HTTP layer only: validation, status codes, serialisation
└── services/
    ├── parser.py        File → GeoDataFrame (zip safety, shapefile checks, KML layers, CRS detection)
    ├── measurement.py   CRS selection + area/length for a single geometry (pure functions)
    └── processor.py     Orchestration: parse → measure each feature → persist
scripts/make_samples.py  Generates sample files with known sizes
tests/                   Unit tests (measurement) + end-to-end API tests
```

The layers depend in one direction: `routes → services → models`. `measurement.py` has no knowledge of HTTP,
files or the database, which keeps the core logic easy to test.

### File-processing flow

```
client ──POST file──▶ routes.upload_file
                        1. check extension (.zip / .kml)                → 415
                        2. stream to disk in 1 MB chunks, enforce size  → 413
                        3. zip only: quick structural check             → 400
                           (valid zip? exactly one .shp? .shx/.dbf present? uncompressed size OK?)
                        4. insert GeoFile(status=PENDING)
                        5. schedule background task, return 202 + id
                                │
                                ▼
                    processor.process_file   (status = PROCESSING)
                        6. extract zip to a temp dir (zip-slip protected,
                           ignores __MACOSX/ and ._ files, supports sub-folders)
                        7. read with GeoPandas/pyogrio
                           - Shapefile: CRS from .prj
                           - KML: every layer/folder is read and combined
                        8. determine source CRS (see CRS handling)
                        9. measure every feature (one bad feature never fails the file)
                       10. store Feature rows; status = COMPLETED
                           (any GeoFileError → FAILED + readable error)
```

Fast, cheap checks run **synchronously** so the user gets an immediate 400 for obvious mistakes; the
expensive parsing runs in the background so large files don't block the request.

### Measurement calculation flow (per feature)

```
geometry ─▶ empty / None?          ─▶ SKIPPED
        ─▶ Point / MultiPoint?     ─▶ NOT_APPLICABLE
        ─▶ not polygon/line?       ─▶ UNSUPPORTED
        ─▶ invalid polygon?        ─▶ repair with shapely.make_valid (keep polygon parts, add note)
        ─▶ reproject source CRS → WGS84 (lon/lat)
        ─▶ densify edges to ≤ 0.01° (keeps long edges accurate after projection)
        ─▶ choose a local metric CRS for this feature
        ─▶ reproject WGS84 → local CRS
        ─▶ Polygon: area_m2 + perimeter_m      LineString: length_m
```

### CRS handling

1. **Detect the source CRS**
   - Shapefile: from the `.prj` file.
   - KML: always EPSG:4326 (WGS84), as defined by the KML standard.
   - Shapefile **without** `.prj`: if every coordinate is within ±180/±90 it is assumed to be EPSG:4326;
     otherwise the file is marked `FAILED` with a clear message, because guessing a projected CRS would
     silently produce wrong numbers.
2. **Never measure in degrees.** Every geometry is converted to WGS84 and then projected to a metric CRS
   chosen **per feature**:
   - **UTM zone of the feature's centroid** (`EPSG:326xx` north / `EPSG:327xx` south) when the feature is
     at most 6° wide and between 84°S and 84°N. This covers practically all survey/drone data; distortion
     is below 0.1 %.
   - **Very wide or polar features**: a projection centred on the feature — *Lambert Azimuthal
     Equal-Area* for polygons (preserves area exactly) and *Azimuthal Equidistant* for lines.
3. **Projected input is re-projected too.** A file can be "projected" but still wrong to measure in —
   Web Mercator (EPSG:3857) overstates area by `1/cos²(latitude)` (≈5 % in Bengaluru, ≈30 % in Delhi,
   4× at 60°N). The `farm_plots_webmercator.zip` sample and a test prove this case returns the true area.
4. The CRS that was used is returned for every feature as `projected_crs`, so results are auditable.

---

## Design decisions

| Decision | Alternatives considered | Why |
|---|---|---|
| **FastAPI** | Django + DRF | No admin/ORM-heavy needs; automatic OpenAPI docs and typed request/response models; less boilerplate for a small service. |
| **Per-feature UTM zone** | One CRS for the whole file (`estimate_utm_crs` on total bounds); geodesic maths (`pyproj.Geod`) | A file may contain features far apart (different zones). Per-feature UTM is standard, explainable and accurate. Geodesic maths is equally accurate but the brief asks to *transform to a projected CRS*; it is used in the tests as an independent reference. |
| **Compute UTM EPSG code directly** from the centroid | `GeoSeries.estimate_utm_crs()` | Same result but no PROJ database query per feature, so it is much faster for files with many features. Transformers are cached with `lru_cache`. |
| **Always go via WGS84** | Measure directly if the input is already projected | Protects against distorted projected CRSs such as Web Mercator. Costs one extra transform. |
| **Densify edges before projecting** | Project vertices only | For a 15°-wide polygon the error drops from ~0.1 % to < 0.0001 %. No effect on small features. |
| **Repair invalid polygons** (`make_valid`) | Reject them | Real-world digitised data often has self-intersections; repairing and adding a `note` is more useful than failing. |
| **Background processing + status field** | Process synchronously in the POST | Matches the `status` field in the brief; large files don't block requests or time out. Cheap validation is still synchronous for instant feedback. |
| **FastAPI `BackgroundTasks`** | Celery / RQ + Redis | No extra infrastructure for an assignment. The processor is a plain function, so moving to a real queue is a small change (see Future scope). |
| **SQLite via SQLModel** | PostgreSQL + PostGIS; in-memory dict | Zero setup for reviewers, results survive restarts, and `DATABASE_URL` allows switching to PostgreSQL. |
| **Store measurements, not just files** | Recompute on every GET | GETs are fast and support pagination/filtering/summary in SQL. |
| **Per-feature error isolation** | Fail the whole file on the first problem | One odd feature (e.g. a GeometryCollection in KML) should not hide results for the other 999. |
| **Read all KML layers** | Read only the first layer (GeoPandas default) | KML files from Google Earth often use several folders; reading only the first would silently drop features. |
| **Clear HTTP status codes** (400/404/409/413/415/422) | Always 400/500 | Clients can tell "your file is wrong" from "not ready yet" from "not found". |

---

## Learnings

- Area in "square degrees" is meaningless: one degree of longitude is ~111 km at the equator but ~56 km at
  60° latitude. Measurements must be done in a projected, metric CRS.
- Not every projected CRS is good for measuring — Web Mercator looks metric but distorts area heavily.
- How UTM zones work (6° wide, north/south EPSG codes) and when they stop being accurate.
- Shapefiles are really several files (`.shp`, `.shx`, `.dbf`, `.prj`); zips made on macOS add
  `__MACOSX/` folders; KML can contain multiple folders/layers and mixed `MultiGeometry`.
- `always_xy=True` matters: EPSG:4326 officially uses latitude-first axis order, which silently swaps
  coordinates if ignored.
- Writing tests with shapes of known size (built in metres, saved in degrees) is the easiest way to prove
  the CRS handling is correct.

## Future scope

- **Task queue** (Celery/RQ + Redis) and multiple workers for very large files; progress reporting.
- **More formats**: GeoJSON, GeoPackage, KMZ, multiple shapefiles per zip.
- **PostGIS** for storage, spatial queries (e.g. "features inside this area") and `ST_Area(geography)`.
- **Choice of method/units** via query params (UTM vs geodesic; acres, sq ft, miles).
- **Antimeridian-crossing** geometries (split at ±180°) and Svalbard/Norway UTM exceptions.
- **3D**: surface area and slope length using elevation (useful for drone/terrain data).
- **Auth, rate limiting, object storage (S3)** for uploads, Docker image and CI pipeline.
- **Export** results as CSV / GeoJSON download.
