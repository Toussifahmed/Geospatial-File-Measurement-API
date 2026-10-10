"""Reading geospatial files (zipped Shapefile / KML) and persisting their features."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from sqlalchemy.orm import Session

from app.config import MAX_UNZIPPED_BYTES
from app.models import Feature, FileStatus, UploadedFile
from app.services.measure import crs_label, measure_geometry


class InvalidUpload(Exception):
    """The client sent something we can't accept (maps to HTTP 400)."""


class ProcessingError(Exception):
    """The upload was accepted but could not be processed (file marked FAILED)."""


# --------------------------------------------------------------------------- zip handling
def safe_extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extract ``zip_path`` into ``dest`` defending against zip-slip and zip bombs."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InvalidUpload("The uploaded file is not a valid zip archive.") from exc

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX")]
        if sum(m.file_size for m in members) > MAX_UNZIPPED_BYTES:
            raise InvalidUpload("The zip archive is too large once extracted.")
        dest_root = dest.resolve()
        extracted = []
        for member in members:
            target = (dest_root / member.filename).resolve()
            if dest_root not in target.parents:  # e.g. '../../etc/passwd'
                raise InvalidUpload("The zip archive contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                out.write(src.read())
            extracted.append(target)
    return extracted


def find_shapefile(files: list[Path]) -> Path:
    shapefiles = sorted(p for p in files if p.suffix.lower() == ".shp")
    if not shapefiles:
        raise InvalidUpload("The zip archive does not contain a .shp file.")
    if len(shapefiles) > 1:
        raise InvalidUpload("The zip archive contains more than one .shp file; upload one shapefile per zip.")
    return shapefiles[0]


# --------------------------------------------------------------------------- reading
def read_geodata(path: Path) -> gpd.GeoDataFrame:
    """Read a .shp or .kml into a GeoDataFrame (KML: all layers/folders are merged)."""
    try:
        if path.suffix.lower() == ".kml":
            frames = []
            for layer, _gtype in pyogrio.list_layers(path):
                frame = gpd.read_file(path, layer=layer, engine="pyogrio")
                if len(frame):
                    frames.append(frame)
            if not frames:
                return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
            gdf = pd.concat(frames, ignore_index=True)
            return gpd.GeoDataFrame(gdf, geometry="geometry", crs=frames[0].crs)
        return gpd.read_file(path, engine="pyogrio")
    except Exception as exc:
        raise ProcessingError(f"Could not read geospatial data: {exc}") from exc


def _json_safe_properties(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Attribute table -> list of JSON-safe dicts (NaN -> null, timestamps -> ISO strings)."""
    attrs = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    return json.loads(attrs.to_json(orient="records", date_format="iso"))


# --------------------------------------------------------------------------- processing
def process_file(db: Session, record: UploadedFile, path: Path) -> UploadedFile:
    """Parse ``path``, measure each feature, persist everything, set the final status."""
    try:
        gdf = read_geodata(path)
        if gdf.crs is None:
            raise ProcessingError(
                "The file has no coordinate reference system (CRS). "
                "For shapefiles make sure the .prj file is included in the zip."
            )

        file_crs = crs_label(gdf.crs)
        properties = _json_safe_properties(gdf)

        features = []
        for index, geom in enumerate(gdf.geometry):
            result = measure_geometry(geom, gdf.crs)
            is_null = geom is None or geom.is_empty
            features.append(
                Feature(
                    file_id=record.id,
                    feature_index=index,
                    geometry_type=geom.geom_type if geom is not None else "None",
                    crs=file_crs,
                    geometry=None if is_null else json.loads(shapely.to_geojson(geom)),
                    properties=properties[index],
                    measurement_status=result.status.value,
                    area_m2=result.area_m2,
                    length_m=result.length_m,
                    projected_crs=result.projected_crs,
                    note=result.note,
                )
            )

        db.add_all(features)
        record.crs = file_crs
        record.feature_count = len(features)
        record.status = FileStatus.COMPLETED.value
    except ProcessingError as exc:
        record.status = FileStatus.FAILED.value
        record.error_message = str(exc)
    except Exception as exc:  # unexpected: record it rather than returning a 500
        record.status = FileStatus.FAILED.value
        record.error_message = f"Unexpected error while processing file: {exc}"

    db.add(record)
    db.commit()
    return record
