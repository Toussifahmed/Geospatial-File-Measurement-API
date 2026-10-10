import io
import zipfile

import pytest
from pyproj import Geod
from shapely.geometry import Polygon

from tests import factories
from tests.conftest import upload


# ------------------------------------------------------------------ upload & file info
def test_upload_kml_returns_file_info(client):
    res = upload(client, "survey.kml", factories.sample_kml())
    assert res.status_code == 201
    body = res.json()
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"

    info = client.get(f"/api/files/{body['id']}/")
    assert info.status_code == 200
    assert info.json() == body


def test_upload_zipped_shapefile(client):
    res = upload(client, "plots.zip", factories.mixed_wgs84_shapefile())
    assert res.status_code == 201
    assert res.json()["feature_count"] == 2
    assert res.json()["status"] == "COMPLETED"


def test_unknown_file_id_404(client):
    assert client.get("/api/files/doesnotexist/").status_code == 404
    assert client.get("/api/files/doesnotexist/measurements/").status_code == 404


# ------------------------------------------------------------------ validation
def test_rejects_unsupported_extension(client):
    res = upload(client, "notes.txt", b"hello")
    assert res.status_code == 400


def test_rejects_empty_file(client):
    assert upload(client, "empty.kml", b"").status_code == 400


def test_rejects_corrupt_zip(client):
    assert upload(client, "bad.zip", b"this is not a zip").status_code == 400


def test_rejects_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no shapefile here")
    assert upload(client, "empty.zip", buf.getvalue()).status_code == 400


def test_rejects_zip_slip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.shp", "x")
    assert upload(client, "evil.zip", buf.getvalue()).status_code == 400


def test_missing_request_file_is_422(client):
    assert client.post("/api/files/").status_code == 422


def test_unreadable_kml_marks_file_failed(client):
    res = upload(client, "broken.kml", b"<kml><not-closed>")
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "FAILED"
    assert body["error_message"]
    # measurements are not available for a failed file
    assert client.get(f"/api/files/{body['id']}/measurements/").status_code == 409


def test_shapefile_without_prj_fails_gracefully(client):
    res = upload(client, "noprj.zip", factories.mixed_wgs84_shapefile(drop_prj=True))
    body = res.json()
    assert res.status_code == 201
    assert body["status"] == "FAILED"
    assert "CRS" in body["error_message"]


# ------------------------------------------------------------------ measurements
def _measurements(client, file_id, **params):
    res = client.get(f"/api/files/{file_id}/measurements/", params=params)
    assert res.status_code == 200
    return res.json()


def test_kml_measurements_per_geometry_type(client):
    file_id = upload(client, "survey.kml", factories.sample_kml()).json()["id"]
    data = _measurements(client, file_id)
    by_type = {f["geometry_type"]: f for f in data["features"]}

    polygon = by_type["Polygon"]
    assert polygon["measurement_status"] == "MEASURED"
    assert polygon["area_m2"] > 0 and polygon["length_m"] is None
    assert polygon["projected_crs"] == "EPSG:32644"  # Kurnool is in UTM zone 44N
    assert polygon["properties"]["Name"] == "Plot A"

    line = by_type["LineString"]
    assert line["measurement_status"] == "MEASURED"
    assert line["length_m"] > 0 and line["area_m2"] is None

    point = by_type["Point"]
    assert point["measurement_status"] == "NOT_APPLICABLE"
    assert point["area_m2"] is None and point["length_m"] is None

    assert data["summary"]["measured"] == 2
    assert data["summary"]["not_applicable"] == 1
    assert data["summary"]["total_area_m2"] == pytest.approx(polygon["area_m2"])


def test_area_is_measured_in_metres_not_degrees(client):
    """Compare with an independent geodesic calculation on the ellipsoid (<0.5% apart)."""
    file_id = upload(client, "survey.kml", factories.sample_kml()).json()["id"]
    polygon = next(f for f in _measurements(client, file_id)["features"] if f["geometry_type"] == "Polygon")

    geodesic_area, _ = Geod(ellps="WGS84").geometry_area_perimeter(Polygon(factories.KURNOOL_SQUARE))
    assert polygon["area_m2"] == pytest.approx(abs(geodesic_area), rel=0.005)
    assert polygon["area_m2"] > 1_000_000  # ~1.1 km^2, definitely not a tiny degree^2 value


def test_line_length_matches_geodesic(client):
    file_id = upload(client, "roads.zip", factories.line_shapefile()).json()["id"]
    line = _measurements(client, file_id)["features"][0]
    _, _, geodesic = Geod(ellps="WGS84").inv(78.04, 15.83, 78.05, 15.83)
    assert line["length_m"] == pytest.approx(geodesic, rel=0.005)


def test_projected_input_crs_is_handled(client):
    """100 m x 200 m rectangle in EPSG:32644 -> 20,000 m^2."""
    res = upload(client, "rect.zip", factories.projected_rectangle_shapefile())
    assert res.json()["crs"] == "EPSG:32644"
    feature = _measurements(client, res.json()["id"])["features"][0]
    assert feature["area_m2"] == pytest.approx(20_000, rel=1e-4)


def test_multipolygon_area_is_sum_of_parts(client):
    file_id = upload(client, "plots.zip", factories.mixed_wgs84_shapefile()).json()["id"]
    feats = _measurements(client, file_id)["features"]
    single = next(f for f in feats if f["geometry_type"] == "Polygon")
    multi = next(f for f in feats if f["geometry_type"] == "MultiPolygon")
    assert multi["measurement_status"] == "MEASURED"
    assert multi["area_m2"] == pytest.approx(2 * 1_000_000, rel=0.3)  # two ~1.1 km^2 squares
    assert single["area_m2"] > 0


def test_point_shapefile_needs_no_measurement(client):
    file_id = upload(client, "wells.zip", factories.point_shapefile()).json()["id"]
    feature = _measurements(client, file_id)["features"][0]
    assert feature["measurement_status"] == "NOT_APPLICABLE"


def test_unsupported_geometry_is_handled_gracefully(client):
    res = upload(client, "mixed.kml", factories.kml_with_unsupported_geometry())
    assert res.status_code == 201 and res.json()["status"] == "COMPLETED"
    data = _measurements(client, res.json()["id"])
    statuses = {f["geometry_type"]: f for f in data["features"]}
    assert statuses["GeometryCollection"]["measurement_status"] == "UNSUPPORTED"
    assert "not supported" in statuses["GeometryCollection"]["note"]
    assert statuses["Polygon"]["measurement_status"] == "MEASURED"  # others unaffected
    assert data["summary"]["unsupported"] == 1


def test_geometry_only_included_on_request(client):
    file_id = upload(client, "survey.kml", factories.sample_kml()).json()["id"]
    assert all(f["geometry"] is None for f in _measurements(client, file_id)["features"])
    with_geom = _measurements(client, file_id, include_geometry="true")["features"]
    assert all(f["geometry"]["type"] == f["geometry_type"] for f in with_geom)


def test_pagination(client):
    file_id = upload(client, "survey.kml", factories.sample_kml()).json()["id"]
    page = _measurements(client, file_id, limit=2, offset=1)
    assert page["total"] == 3
    assert [f["feature_index"] for f in page["features"]] == [1, 2]
    assert client.get(f"/api/files/{file_id}/measurements/", params={"limit": 0}).status_code == 422
