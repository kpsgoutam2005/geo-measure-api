"""API response models (kept separate from DB tables so the API contract is explicit)."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models import FileStatus, FileType, MeasurementStatus


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: FileType
    size_bytes: int
    status: FileStatus
    feature_count: Optional[int] = None
    crs: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None


class FileList(BaseModel):
    count: int
    limit: int
    offset: int
    results: list[FileInfo]


class FeatureMeasurement(BaseModel):
    index: int
    layer: Optional[str] = None
    geometry_type: Optional[str] = None
    measurement_status: MeasurementStatus
    projected_crs: Optional[str] = None
    area_m2: Optional[float] = None
    area_hectares: Optional[float] = None
    perimeter_m: Optional[float] = None
    length_m: Optional[float] = None
    length_km: Optional[float] = None
    note: Optional[str] = None
    geometry: Optional[dict[str, Any]] = None
    properties: Optional[dict[str, Any]] = None


class MeasurementSummary(BaseModel):
    total_features: int
    by_status: dict[str, int]
    by_geometry_type: dict[str, int]
    total_area_m2: float
    total_length_m: float


class MeasurementsResponse(BaseModel):
    file_id: str
    filename: str
    source_crs: Optional[str] = None
    units: dict[str, str] = {
        "area": "square metres (m2) / hectares",
        "length": "metres (m) / kilometres",
    }
    summary: MeasurementSummary
    count: int
    limit: int
    offset: int
    results: list[FeatureMeasurement]


class ErrorResponse(BaseModel):
    detail: str
