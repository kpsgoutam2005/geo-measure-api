"""HTTP endpoints under /api/files/."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Annotated, Any, Optional
from uuid import uuid4

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status,
)
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.config import settings
from app.database import get_session
from app.exceptions import FileTooLarge, GeoFileError, UnsupportedFileType
from app.models import Feature, FileStatus, FileType, GeoFile
from app.schemas import (
    ErrorResponse, FeatureMeasurement, FileInfo, FileList, MeasurementsResponse, MeasurementSummary,
)
from app.services.parser import detect_file_type, validate_zip
from app.services.processor import process_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

SessionDep = Annotated[Session, Depends(get_session)]
CHUNK = 1024 * 1024


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _save_upload(upload: UploadFile, dest: Path) -> int:
    """Stream the upload to disk, enforcing the size limit without loading it into memory."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with dest.open("wb") as out:
        while chunk := upload.file.read(CHUNK):
            written += len(chunk)
            if written > settings.max_upload_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise FileTooLarge(f"File exceeds the {settings.max_upload_mb} MB upload limit.")
            out.write(chunk)
    if written == 0:
        dest.unlink(missing_ok=True)
        raise GeoFileError("The uploaded file is empty.")
    return written


def _get_file_or_404(session: Session, file_id: str) -> GeoFile:
    geofile = session.get(GeoFile, file_id)
    if geofile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"File '{file_id}' not found.")
    return geofile


def _require_completed(geofile: GeoFile) -> None:
    if geofile.status in (FileStatus.PENDING, FileStatus.PROCESSING):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"File is still being processed (status: {geofile.status.value}). Try again shortly.",
        )
    if geofile.status == FileStatus.FAILED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File processing failed: {geofile.error}",
        )


def _summary(session: Session, file_id: str) -> MeasurementSummary:
    base = select(Feature).where(Feature.file_id == file_id).subquery()
    totals = session.exec(
        select(
            func.count(),
            func.coalesce(func.sum(base.c.area_m2), 0.0),
            func.coalesce(func.sum(base.c.length_m), 0.0),
        ).select_from(base)
    ).one()
    by_status = session.exec(
        select(base.c.measurement_status, func.count()).group_by(base.c.measurement_status)
    ).all()
    by_type = session.exec(
        select(base.c.geometry_type, func.count()).group_by(base.c.geometry_type)
    ).all()
    return MeasurementSummary(
        total_features=totals[0],
        total_area_m2=round(totals[1], 4),
        total_length_m=round(totals[2], 4),
        by_status={(s.value if hasattr(s, "value") else str(s)): n for s, n in by_status},
        by_geometry_type={str(t): n for t, n in by_type},
    )


def _to_measurement(f: Feature, include_geometry: bool, include_properties: bool) -> FeatureMeasurement:
    return FeatureMeasurement(
        index=f.feature_index,
        layer=f.layer,
        geometry_type=f.geometry_type,
        measurement_status=f.measurement_status,
        projected_crs=f.projected_crs,
        area_m2=None if f.area_m2 is None else round(f.area_m2, 4),
        area_hectares=None if f.area_m2 is None else round(f.area_m2 / 10_000, 6),
        perimeter_m=None if f.perimeter_m is None else round(f.perimeter_m, 4),
        length_m=None if f.length_m is None else round(f.length_m, 4),
        length_km=None if f.length_m is None else round(f.length_m / 1000, 6),
        note=f.note,
        geometry=json.loads(f.geometry) if include_geometry and f.geometry else None,
        properties=json.loads(f.properties) if include_properties else None,
    )


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
@router.post(
    "/",
    response_model=FileInfo,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}},
    summary="Upload a geospatial file (.zip Shapefile or .kml) for processing",
)
def upload_file(
    session: SessionDep,
    background: BackgroundTasks,
    file: UploadFile = File(..., description="A .zip containing one Shapefile, or a .kml file"),
) -> GeoFile:
    try:
        file_type = detect_file_type(file.filename)
    except UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc

    file_id = uuid4().hex
    suffix = ".zip" if file_type == FileType.SHAPEFILE else ".kml"
    dest = settings.upload_dir / file_id / f"original{suffix}"

    try:
        size = _save_upload(file, dest)
        if file_type == FileType.SHAPEFILE:
            validate_zip(dest)  # fail fast on obviously broken archives
    except FileTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except GeoFileError as exc:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    geofile = GeoFile(
        id=file_id,
        filename=Path(file.filename).name,
        file_type=file_type,
        stored_path=str(dest),
        size_bytes=size,
    )
    session.add(geofile)
    session.commit()
    session.refresh(geofile)

    background.add_task(process_file, file_id)
    logger.info("Accepted %s (%s, %d bytes) as %s", geofile.filename, file_type.value, size, file_id)
    return geofile


@router.get("/", response_model=FileList, summary="List uploaded files")
def list_files(
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    total = session.exec(select(func.count()).select_from(GeoFile)).one()
    rows = session.exec(
        select(GeoFile).order_by(col(GeoFile.created_at).desc()).offset(offset).limit(limit)
    ).all()
    return {"count": total, "limit": limit, "offset": offset, "results": rows}


@router.get(
    "/{file_id}/",
    response_model=FileInfo,
    responses={404: {"model": ErrorResponse}},
    summary="Get information about an uploaded file",
)
def get_file(file_id: str, session: SessionDep) -> GeoFile:
    return _get_file_or_404(session, file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Get measurements for the features in a file",
)
def get_measurements(
    file_id: str,
    session: SessionDep,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    geometry_type: Optional[str] = Query(None, description="Filter, e.g. Polygon or LineString"),
    include_geometry: bool = Query(False, description="Include GeoJSON geometry (source CRS)"),
    include_properties: bool = Query(True, description="Include feature attributes"),
) -> dict[str, Any]:
    geofile = _get_file_or_404(session, file_id)
    _require_completed(geofile)

    query = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        query = query.where(func.lower(Feature.geometry_type) == geometry_type.lower())
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(query.order_by(Feature.feature_index).offset(offset).limit(limit)).all()

    return {
        "file_id": geofile.id,
        "filename": geofile.filename,
        "source_crs": geofile.crs,
        "summary": _summary(session, file_id),
        "count": count,
        "limit": limit,
        "offset": offset,
        "results": [_to_measurement(f, include_geometry, include_properties) for f in rows],
    }


@router.get(
    "/{file_id}/features/",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Get all features as a GeoJSON FeatureCollection (with measurements in properties)",
)
def get_features_geojson(file_id: str, session: SessionDep) -> dict[str, Any]:
    geofile = _get_file_or_404(session, file_id)
    _require_completed(geofile)
    rows = session.exec(
        select(Feature).where(Feature.file_id == file_id).order_by(Feature.feature_index)
    ).all()
    features = []
    for f in rows:
        m = _to_measurement(f, include_geometry=False, include_properties=False)
        features.append({
            "type": "Feature",
            "id": f.feature_index,
            "geometry": json.loads(f.geometry) if f.geometry else None,
            "properties": {
                **json.loads(f.properties),
                "_measurement": m.model_dump(exclude_none=True, exclude={"geometry", "properties"}),
            },
        })
    return {"type": "FeatureCollection", "crs_name": geofile.crs, "features": features}


@router.delete(
    "/{file_id}/",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
    summary="Delete a file and its results",
)
def delete_file(file_id: str, session: SessionDep) -> Response:
    geofile = _get_file_or_404(session, file_id)
    for feature in session.exec(select(Feature).where(Feature.file_id == file_id)).all():
        session.delete(feature)
    session.delete(geofile)
    session.commit()
    shutil.rmtree(Path(geofile.stored_path).parent, ignore_errors=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
