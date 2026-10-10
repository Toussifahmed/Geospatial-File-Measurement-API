"""Measurement calculation with correct CRS handling.

Strategy
--------
Never measure in degrees. For every feature we:
  1. transform its geometry to WGS84 (EPSG:4326) -- this also normalises any source CRS/units,
  2. pick the UTM zone that contains the feature's centroid (UPS near the poles),
  3. transform the geometry from the source CRS into that UTM zone (metres),
  4. read ``.area`` (m^2) / ``.length`` (m) from the projected geometry.

The zone is chosen *per feature*, so a file spanning several zones is still measured
reasonably for each feature. See the README for the trade-offs.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

from app.models import MeasurementStatus

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
POINT_TYPES = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: MeasurementStatus
    area_m2: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    note: str | None = None


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat); UPS for |lat| >= 84 degrees."""
    if lat >= 84:
        return 32661  # UPS North
    if lat <= -80:
        return 32761  # UPS South
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def _transformer(src_wkt: str, dst: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_user_input(dst), always_xy=True)


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    """Reproject a shapely geometry (2D; any Z values are dropped)."""
    return shapely.transform(geom, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def crs_label(crs: CRS | None) -> str | None:
    """Human readable CRS id, e.g. 'EPSG:4326' (falls back to the CRS name)."""
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> Measurement:
    """Measure one geometry. Never raises: problems are reported in the result."""
    try:
        if geom is None or geom.is_empty:
            return Measurement(MeasurementStatus.UNSUPPORTED, note="Empty or missing geometry.")

        gtype = geom.geom_type
        if gtype in POINT_TYPES:
            return Measurement(MeasurementStatus.NOT_APPLICABLE, note="No measurement required for points.")
        if gtype not in AREA_TYPES | LENGTH_TYPES:
            return Measurement(
                MeasurementStatus.UNSUPPORTED,
                note=f"Measurement is not supported for geometry type '{gtype}'.",
            )

        src_wkt = src_crs.to_wkt()
        to_wgs84 = _transformer(src_wkt, "EPSG:4326")
        centroid = _reproject(geom, to_wgs84).centroid
        epsg = utm_epsg_for(centroid.x, centroid.y)
        projected = _reproject(geom, _transformer(src_wkt, f"EPSG:{epsg}"))

        note = None
        if gtype in AREA_TYPES:
            if not projected.is_valid:
                note = "Geometry is invalid (e.g. self-intersecting); area may be unreliable."
            return Measurement(
                MeasurementStatus.MEASURED,
                area_m2=float(projected.area),
                projected_crs=f"EPSG:{epsg}",
                note=note,
            )
        return Measurement(
            MeasurementStatus.MEASURED,
            length_m=float(projected.length),
            projected_crs=f"EPSG:{epsg}",
        )
    except Exception as exc:  # one bad feature must never fail the whole file
        return Measurement(MeasurementStatus.ERROR, note=f"Measurement failed: {exc}")
