"""File upload / info / measurements endpoints."""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import MAX_UPLOAD_BYTES, upload_dir
from app.db import get_db
from app.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.schemas import FeatureMeasurement, FileInfo, MeasurementsResponse, MeasurementSummary
from app.services.parser import InvalidUpload, find_shapefile, process_file, safe_extract_zip

router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_EXTENSIONS = {".zip", ".kml"}
CHUNK = 1024 * 1024


def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Stream the upload to disk, enforcing the size limit."""
    size = 0
    with open(dest, "wb") as out:
        while chunk := upload.file.read(CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise InvalidUpload(f"File too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
            out.write(chunk)
    if size == 0:
        raise InvalidUpload("The uploaded file is empty.")


@router.post("/", response_model=FileInfo, status_code=status.HTTP_201_CREATED)
def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a zipped Shapefile (.zip) or a .kml file; it is parsed and measured immediately."""
    original_name = Path(file.filename or "").name
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Unsupported file type. Upload a .zip (Shapefile) or a .kml file.")

    record = UploadedFile(id=uuid.uuid4().hex, filename=original_name)
    workdir = upload_dir() / record.id
    workdir.mkdir(parents=True)
    try:
        saved = workdir / f"upload{ext}"
        _save_upload(file, saved)
        data_path = saved if ext == ".kml" else find_shapefile(safe_extract_zip(saved, workdir / "extracted"))
    except InvalidUpload as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc

    db.add(record)
    db.commit()
    return process_file(db, record, data_path)


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, "File not found.")
    return record


@router.get("/{file_id}/", response_model=FileInfo)
def get_file(file_id: str, db: Session = Depends(get_db)):
    """Basic information about an uploaded file."""
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(
    file_id: str,
    include_geometry: bool = Query(False, description="Include each feature's GeoJSON geometry."),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Per-feature measurements (area for polygons, length for lines) plus a file-level summary."""
    record = _get_file_or_404(db, file_id)
    if record.status != FileStatus.COMPLETED.value:
        raise HTTPException(409, f"File status is {record.status}: {record.error_message or 'not ready'}")

    def count(status_: MeasurementStatus) -> int:
        return db.scalar(
            select(func.count()).where(Feature.file_id == file_id, Feature.measurement_status == status_.value)
        )

    totals = db.execute(
        select(func.coalesce(func.sum(Feature.area_m2), 0.0), func.coalesce(func.sum(Feature.length_m), 0.0))
        .where(Feature.file_id == file_id)
    ).one()

    rows = db.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.feature_index)
        .offset(offset)
        .limit(limit)
    ).all()

    features = []
    for row in rows:
        item = FeatureMeasurement.model_validate(row)
        if not include_geometry:
            item.geometry = None
        features.append(item)

    return MeasurementsResponse(
        file_id=record.id,
        crs=record.crs,
        status=record.status,
        summary=MeasurementSummary(
            total_features=record.feature_count,
            measured=count(MeasurementStatus.MEASURED),
            not_applicable=count(MeasurementStatus.NOT_APPLICABLE),
            unsupported=count(MeasurementStatus.UNSUPPORTED),
            errors=count(MeasurementStatus.ERROR),
            total_area_m2=float(totals[0]),
            total_length_m=float(totals[1]),
        ),
        total=record.feature_count,
        limit=limit,
        offset=offset,
        features=features,
    )
