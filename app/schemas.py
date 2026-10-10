"""Pydantic response models (these also drive the Swagger docs at /docs)."""
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: str
    error_message: str | None = None


class FeatureMeasurement(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_index: int
    geometry_type: str
    crs: str | None
    properties: dict[str, Any]
    measurement_status: str
    area_m2: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    note: str | None = None
    geometry: dict[str, Any] | None = None  # only populated with ?include_geometry=true


class MeasurementSummary(BaseModel):
    total_features: int
    measured: int
    not_applicable: int
    unsupported: int
    errors: int
    total_area_m2: float
    total_length_m: float


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: str | None
    status: str
    summary: MeasurementSummary
    total: int
    limit: int
    offset: int
    features: list[FeatureMeasurement]
