"""Builders for sample geospatial files (used by tests and to generate ./sample_data)."""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

# ~1 km x 1 km square near Kurnool, India (lon/lat order in KML and shapely).
KURNOOL_SQUARE = [(78.04, 15.83), (78.05, 15.83), (78.05, 15.84), (78.04, 15.84), (78.04, 15.83)]


def _coords(points) -> str:
    return " ".join(f"{x},{y},0" for x, y in points)


def kml_document(placemarks: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{placemarks}"
        "</Document></kml>"
    ).encode()


def sample_kml() -> bytes:
    """Polygon + LineString + Point."""
    return kml_document(
        f"""
        <Placemark><name>Plot A</name><description>Farm plot</description>
          <Polygon><outerBoundaryIs><LinearRing><coordinates>{_coords(KURNOOL_SQUARE)}</coordinates></LinearRing></outerBoundaryIs></Polygon>
        </Placemark>
        <Placemark><name>Road 1</name>
          <LineString><coordinates>{_coords([(78.04, 15.83), (78.05, 15.83)])}</coordinates></LineString>
        </Placemark>
        <Placemark><name>Well</name>
          <Point><coordinates>78.045,15.835,0</coordinates></Point>
        </Placemark>
        """
    )


def kml_with_unsupported_geometry() -> bytes:
    """Contains a KML MultiGeometry -> GeometryCollection, which we do not measure."""
    return kml_document(
        f"""
        <Placemark><name>Mixed</name>
          <MultiGeometry>
            <Point><coordinates>78.04,15.83,0</coordinates></Point>
            <LineString><coordinates>{_coords([(78.04, 15.83), (78.05, 15.83)])}</coordinates></LineString>
          </MultiGeometry>
        </Placemark>
        <Placemark><name>Plot A</name>
          <Polygon><outerBoundaryIs><LinearRing><coordinates>{_coords(KURNOOL_SQUARE)}</coordinates></LinearRing></outerBoundaryIs></Polygon>
        </Placemark>
        """
    )


def zip_shapefile(gdf: gpd.GeoDataFrame, drop_prj: bool = False) -> bytes:
    """Write ``gdf`` as a shapefile and return it zipped, as bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        shp = Path(tmp) / "data.shp"
        gdf.to_file(shp, driver="ESRI Shapefile")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for part in sorted(Path(tmp).iterdir()):
                if drop_prj and part.suffix == ".prj":
                    continue
                zf.write(part, part.name)
        return buf.getvalue()


def projected_rectangle_shapefile() -> bytes:
    """Rectangle of exactly 100 m x 200 m (= 20,000 m^2) in UTM zone 44N (EPSG:32644)."""
    x, y = 500000, 1750000
    gdf = gpd.GeoDataFrame(
        {"name": ["Rect"], "geometry": [box(x, y, x + 100, y + 200)]}, crs="EPSG:32644"
    )
    return zip_shapefile(gdf)


def mixed_wgs84_shapefile(drop_prj: bool = False) -> bytes:
    """Polygon, MultiPolygon, LineString and Point in EPSG:4326."""
    poly = Polygon(KURNOOL_SQUARE)
    multi = MultiPolygon([box(78.10, 15.80, 78.11, 15.81), box(78.12, 15.80, 78.13, 15.81)])
    line = LineString([(78.04, 15.83), (78.05, 15.83)])
    gdf = gpd.GeoDataFrame(
        {
            "name": ["poly", "multi", "line", "point"],
            "value": [1, 2, 3, 4],
            "geometry": [poly, multi, line, Point(78.045, 15.835)],
        },
        crs="EPSG:4326",
    )
    # Shapefiles hold a single geometry type per file, so split into polygon/line/point layers:
    # ``to_file`` raises for mixed types, hence only keep polygons + multipolygons here.
    return zip_shapefile(gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])], drop_prj)


def line_shapefile() -> bytes:
    gdf = gpd.GeoDataFrame(
        {"name": ["road"], "geometry": [LineString([(78.04, 15.83), (78.05, 15.83)])]}, crs="EPSG:4326"
    )
    return zip_shapefile(gdf)


def point_shapefile() -> bytes:
    gdf = gpd.GeoDataFrame({"name": ["well"], "geometry": [Point(78.045, 15.835)]}, crs="EPSG:4326")
    return zip_shapefile(gdf)


def write_sample_data(out_dir: Path) -> None:
    out_dir.mkdir(exist_ok=True)
    (out_dir / "survey.kml").write_bytes(sample_kml())
    (out_dir / "polygons_wgs84.zip").write_bytes(mixed_wgs84_shapefile())
    (out_dir / "rectangle_utm44n.zip").write_bytes(projected_rectangle_shapefile())
    (out_dir / "roads.zip").write_bytes(line_shapefile())
    (out_dir / "wells.zip").write_bytes(point_shapefile())
    (out_dir / "mixed_unsupported.kml").write_bytes(kml_with_unsupported_geometry())


if __name__ == "__main__":
    write_sample_data(Path("sample_data"))
    print("Sample files written to ./sample_data")
