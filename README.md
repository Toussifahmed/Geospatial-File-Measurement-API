# Geospatial File Measurement API

A FastAPI backend that accepts a **zipped Shapefile** or a **KML** file, extracts every feature
(ID, geometry type, geometry, CRS, properties) and returns **area** (polygons) and **length**
(lines) in square metres / metres.

Measurements are never computed in degrees: each geometry is first transformed to a projected
(UTM) coordinate system. See [CRS handling](#crs-handling).

---

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/Toussifahmed/Geospatial-File-Measurement-API.git
cd geospatial-api

python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt # use requirements.txt if you don't need the tests

uvicorn app.main:app --reload
```

The API is now at <http://localhost:8000> and interactive Swagger docs at <http://localhost:8000/docs>.

`geopandas`, `pyogrio`, `pyproj` and `shapely` install from wheels that bundle GDAL/PROJ, so no
system GIS libraries are required on Linux, macOS or Windows.

### Docker (alternative)

```bash
docker compose up --build
```

### Run the tests

```bash
pytest -q
```

Tests build their own sample files and use a throw-away SQLite database and upload folder.

### Sample files

`sample_data/` contains ready-to-upload files (regenerate with `python -m tests.factories`):

| File | Contents |
|---|---|
| `survey.kml` | Polygon + LineString + Point (EPSG:4326) |
| `polygons_wgs84.zip` | Polygon + MultiPolygon shapefile (EPSG:4326) |
| `rectangle_utm44n.zip` | 100 m x 200 m rectangle in EPSG:32644 (area must be exactly 20,000 m²) |
| `roads.zip` / `wells.zip` | Line-only / point-only shapefiles |
| `mixed_unsupported.kml` | Contains a `GeometryCollection` (unsupported for measurement) |

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./geospatial.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `UPLOAD_DIR` | `./uploads` | Where uploaded files are stored |
| `MAX_UPLOAD_MB` | `50` | Max upload size |
| `MAX_UNZIPPED_MB` | `200` | Max total size of an extracted zip (zip-bomb guard) |

---

## API

### `POST /api/files/` — upload and process

Multipart form with a `file` field: a `.kml`, or a `.zip` containing **one** shapefile
(`.shp`, `.shx`, `.dbf` and `.prj`).

```bash
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
```

`201 Created`:

```json
{
  "id": "fb75167b90ac42c28096279cc0ea762d",
  "filename": "survey.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": null
}
```

| Outcome | Response |
|---|---|
| Wrong extension, empty file, corrupt zip, zip without `.shp`, more than one `.shp`, unsafe zip paths, too large | `400` with a `detail` message (nothing is stored) |
| File accepted but cannot be processed (unreadable data, **missing CRS**, e.g. no `.prj`) | `201` with `"status": "FAILED"` and an `error_message` |
| Success | `201` with `"status": "COMPLETED"` |

### `GET /api/files/{id}/` — file information

```bash
curl http://localhost:8000/api/files/fb75167b90ac42c28096279cc0ea762d/
```

Returns the same object as above. `404` if the id is unknown.

### `GET /api/files/{id}/measurements/` — per-feature measurements

Query parameters: `include_geometry` (default `false`, adds GeoJSON in the source CRS),
`limit` (1–1000, default 100) and `offset` (default 0).

```bash
curl http://localhost:8000/api/files/fb75167b90ac42c28096279cc0ea762d/measurements/
```

```json
{
  "file_id": "fb75167b90ac42c28096279cc0ea762d",
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "summary": {
    "total_features": 3, "measured": 2, "not_applicable": 1,
    "unsupported": 0, "errors": 0,
    "total_area_m2": 1187368.16, "total_length_m": 1072.14
  },
  "total": 3, "limit": 100, "offset": 0,
  "features": [
    {
      "feature_index": 0,
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "properties": {"Name": "Plot A", "description": "Farm plot"},
      "measurement_status": "MEASURED",
      "area_m2": 1187368.16,
      "length_m": null,
      "projected_crs": "EPSG:32644",
      "note": null,
      "geometry": null
    }
  ]
}
```

(Properties are abbreviated here; KML files also report fields like `tessellate`, `visibility`.)

`measurement_status` per feature:

| Status | Meaning |
|---|---|
| `MEASURED` | `area_m2` (Polygon/MultiPolygon) or `length_m` (LineString/MultiLineString) filled in |
| `NOT_APPLICABLE` | Point / MultiPoint — no measurement required |
| `UNSUPPORTED` | Other types (e.g. `GeometryCollection`) or empty/null geometry; see `note` |
| `ERROR` | Measuring this one feature failed; see `note`. Other features are unaffected |

`409 Conflict` is returned if the file's status is not `COMPLETED`; `404` if the id is unknown.

### `GET /health`

Returns `{"status": "ok"}`.

---

## Architecture

### Application structure

```
app/
  main.py              FastAPI app, router registration, DB init on startup
  config.py            Environment-based settings
  db.py                SQLAlchemy engine/session + get_db dependency
  models.py            UploadedFile and Feature tables
  schemas.py           Pydantic response models
  api/files.py         HTTP layer: validation, status codes
  services/parser.py   Safe zip extraction, reading files, persisting features
  services/measure.py  CRS selection and area/length calculation (pure functions)
tests/                 pytest suite + sample-file factories
```

The HTTP layer only deals with requests/responses; file parsing and measuring live in
`services/` and know nothing about HTTP, which keeps them easy to unit-test.

### File-processing flow

1. **Validate** the extension (`.zip` / `.kml`), stream the body to disk with a size limit, reject empty files.
2. **Zip only:** extract safely (rejects `../` paths — zip-slip — and oversized archives), then locate exactly one `.shp`.
3. **Persist** an `UploadedFile` row (`PROCESSING`).
4. **Read** with GeoPandas/pyogrio. For KML every layer (folder) is merged.
5. **Check CRS.** No CRS (a shapefile without `.prj`) → file marked `FAILED` with an explanatory message.
6. **For each feature** record index, geometry type, GeoJSON geometry, CRS and properties (made JSON-safe: NaN → `null`, timestamps → ISO strings), and measure it.
7. **Save** all features and set the file to `COMPLETED` (or `FAILED` with an error message).

### Measurement calculation flow

Per feature: classify the geometry type → Polygon/MultiPolygon get `.area`, LineString/MultiLineString
get `.length`, points are `NOT_APPLICABLE`, anything else is `UNSUPPORTED`. The whole call is wrapped
so a failure becomes an `ERROR` result for that feature instead of failing the file. Self-intersecting
polygons are still measured but flagged in `note`.

### CRS handling

For every feature (`app/services/measure.py`):

1. Transform the geometry from the file's CRS to WGS84 and take its centroid.
2. Choose the UTM zone for that centroid: `zone = floor((lon + 180) / 6) + 1`, EPSG `326xx` (north) or
   `327xx` (south). Beyond 84°N / 80°S it uses UPS (EPSG:32661 / 32761).
3. Transform the **original** geometry directly into that UTM zone (metres).
4. Read `.area` / `.length` from the projected geometry.

This works for any source CRS — geographic (EPSG:4326, KML is always this) or projected in any
unit (the transform handles feet etc.). The chosen projected CRS is returned as `projected_crs`.
Tests compare results against an independent geodesic calculation on the WGS84 ellipsoid
(within 0.5%) and check an exactly-known 100 m x 200 m rectangle.

---

## Design decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| **FastAPI** | Typed request/response models, automatic Swagger docs, small and quick for a single-purpose service | Django + DRF: more batteries (admin, auth) than needed here |
| **UTM zone per feature (centroid-based)** | Accurate (<~0.1% distortion inside a zone), simple, returns metres, and each feature is handled independently | A single zone for the whole file (inaccurate for files spanning zones); an equal-area CRS such as EASE-Grid / Lambert Azimuthal Equal-Area (exact for area, but less natural for lengths); geodesic calculations with `pyproj.Geod` (most accurate, but the task asks for transform-to-projected, so I use Geod only as a test oracle) |
| **Synchronous processing** | Parsing a typical survey file takes milliseconds to seconds; the response contains the final status with no polling | Background workers (Celery/RQ) for big files — see future scope. The `status` field and separate GET endpoints already allow this change without altering the API |
| **SQLite via SQLAlchemy** | Zero setup; `DATABASE_URL` switches to PostgreSQL | PostGIS: powerful, but heavy for this scope |
| **Store features in the DB (geometry as GeoJSON/JSON)** | Measurements endpoint is a cheap query with pagination, no re-parsing | Re-read the file on each request (slow, needs files retained) |
| **400 for bad uploads, `FAILED` record for unprocessable files** | A bad request is the client's mistake; a readable-looking upload that has e.g. no CRS is still a result the client can query later | Returning 4xx for everything |
| **`.prj` required; no CRS guessing** | Silently assuming EPSG:4326 can produce wrong areas by orders of magnitude | Assume WGS84 when missing (convenient, but unsafe) |
| **One shapefile per zip** | Avoids ambiguity about which CRS/layer to report | Merge multiple shapefiles |
| **Failures isolated per feature** | One bad geometry doesn't hide measurements for the rest | Fail the whole file |

### Known limitations

- A very large polygon crossing many UTM zones is measured in the zone of its centroid, so distortion grows with size.
- Shapefiles hold a single geometry type per file; KML files can mix types.
- Processing is synchronous, so very large files block the request until done.
- No authentication or rate limiting.

---

## Learnings

- **Degrees are not meters.** Latitude/longitude (EPSG:4326) is measured in degrees, and one degree covers a different distance depending on where you are on Earth. So area and length cannot be calculated directly from it. I first convert each shape to a UTM zone, which uses meters, and then measure.
- **Choosing the CRS.** I find the center point of each feature, work out which UTM zone it falls in, and project into that zone. This keeps measurements accurate for local areas like farms, plots and roads.
- **Real files are messy.** Some shapefiles have no .prj file (so no CRS), some KML files have mixed or unsupported geometry types, and zip files can be unsafe. I handled each of these cleanly: a bad feature gets a note instead of crashing the whole file, and a missing CRS gives a clear error.
- **Testing needs a second opinion.** Checking that "a number comes back" is not enough. I compared my results with an independent calculation (geodesic area on the WGS84 ellipsoid) and with a rectangle of known size (100 m x 200 m = 20,000 m²) to make sure the numbers are actually correct.
- **Layers keep code simple.** I kept the API code, file reading, and measuring in separate files, which made each part easier to test and to explain.

## Future scope

- Process big files in the background (for example with Celery) so the upload returns immediately and the client checks the `status` field.
- Use PostgreSQL with PostGIS for spatial queries instead of SQLite.
- Support more formats such as GeoJSON, GeoPackage and KMZ, and several shapefiles in one upload.
- Offer a more accurate method for very large shapes that cross several UTM zones (geodesic or equal-area calculations).
- Let the user provide a CRS when the .prj file is missing.
- Add login, per-user files, file deletion and rate limiting.
- Add a list-files endpoint, pagination, CSV export of measurements, and a simple web page for uploading and viewing results.
