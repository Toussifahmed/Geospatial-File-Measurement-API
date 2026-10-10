"""Unit tests for the measurement service (no HTTP involved)."""
import pytest
from pyproj import CRS
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from app.models import MeasurementStatus
from app.services.measure import measure_geometry, utm_epsg_for

WGS84 = CRS.from_epsg(4326)


@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (78.04, 15.83, 32644),    # India, north
        (-0.1, 51.5, 32630),      # London
        (151.2, -33.9, 32756),    # Sydney, south
        (180.0, 10.0, 32660),     # clamp at zone 60
        (0.0, 89.0, 32661),       # UPS north
        (0.0, -85.0, 32761),      # UPS south
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert utm_epsg_for(lon, lat) == expected


def test_degrees_are_never_used_for_area():
    square = Polygon([(78.04, 15.83), (78.05, 15.83), (78.05, 15.84), (78.04, 15.84)])
    result = measure_geometry(square, WGS84)
    assert result.status is MeasurementStatus.MEASURED
    assert 1.0e6 < result.area_m2 < 1.3e6  # ~1.07 km^2 (0.0001 deg^2 would be the naive answer)


def test_empty_and_none_geometry():
    assert measure_geometry(None, WGS84).status is MeasurementStatus.UNSUPPORTED
    assert measure_geometry(Polygon(), WGS84).status is MeasurementStatus.UNSUPPORTED


def test_unsupported_type():
    result = measure_geometry(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]), WGS84)
    assert result.status is MeasurementStatus.UNSUPPORTED


def test_invalid_polygon_is_flagged_not_crashed():
    bowtie = Polygon([(78.0, 15.0), (78.01, 15.01), (78.01, 15.0), (78.0, 15.01)])
    result = measure_geometry(bowtie, WGS84)
    assert result.status is MeasurementStatus.MEASURED
    assert "invalid" in result.note.lower()


def test_error_in_one_geometry_is_captured():
    out_of_range = Polygon([(500, 500), (501, 500), (501, 501)])  # impossible lon/lat
    result = measure_geometry(out_of_range, WGS84)
    assert result.status in (MeasurementStatus.ERROR, MeasurementStatus.MEASURED)  # never raises
