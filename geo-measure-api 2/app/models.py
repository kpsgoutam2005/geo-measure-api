"""Database tables."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileType(str, Enum):
    SHAPEFILE = "SHAPEFILE"
    KML = "KML"


class MeasurementStatus(str, Enum):
    MEASURED = "MEASURED"              # area or length was calculated
    NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. Point: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"        # e.g. GeometryCollection
    SKIPPED = "SKIPPED"                # empty / missing geometry
    ERROR = "ERROR"                    # unexpected failure for this feature only


class GeoFile(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    filename: str
    file_type: FileType
    stored_path: str
    size_bytes: int
    status: FileStatus = Field(default=FileStatus.PENDING, index=True)
    feature_count: Optional[int] = None
    crs: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    processed_at: Optional[datetime] = None


class Feature(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: str = Field(foreign_key="geofile.id", index=True)
    feature_index: int
    layer: Optional[str] = None
    geometry_type: Optional[str] = None
    geometry: Optional[str] = None      # GeoJSON (source CRS), stored as text
    properties: str = "{}"              # JSON object, stored as text
    crs: Optional[str] = None

    measurement_status: MeasurementStatus
    projected_crs: Optional[str] = None
    area_m2: Optional[float] = None
    perimeter_m: Optional[float] = None
    length_m: Optional[float] = None
    note: Optional[str] = None
