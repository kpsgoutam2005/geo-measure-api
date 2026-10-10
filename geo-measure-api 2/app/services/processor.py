"""Orchestrates processing of one uploaded file: parse -> measure -> persist."""

from __future__ import annotations

import json
import logging
import math
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import mapping
from sqlalchemy import delete
from sqlmodel import Session

from app.database import engine
from app.exceptions import GeoFileError
from app.models import Feature, FileStatus, GeoFile, MeasurementStatus
from app.services.measurement import MeasurementResult, crs_label, measure_geometry
from app.services.parser import read_geofile

logger = logging.getLogger(__name__)

INTERNAL_COLUMNS = {"__layer__"}


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy/datetime values into plain JSON-serialisable values."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):  # pandas Timestamp
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def build_features(gdf, file_id: str) -> list[Feature]:
    src_crs = gdf.crs
    src_label = crs_label(src_crs)
    prop_cols = [c for c in gdf.columns if c != gdf.geometry.name and c not in INTERNAL_COLUMNS]
    has_layer = "__layer__" in gdf.columns

    records = gdf[prop_cols].to_dict(orient="records")
    layers = gdf["__layer__"].tolist() if has_layer else [None] * len(gdf)

    features: list[Feature] = []
    for idx, (geom, raw_props, layer) in enumerate(zip(gdf.geometry, records, layers)):
        props = {k: _json_safe(v) for k, v in raw_props.items()}

        try:
            m = measure_geometry(geom, src_crs)
        except Exception as exc:  # one bad feature must not fail the whole file
            logger.exception("Measurement failed for feature %s of file %s", idx, file_id)
            m = MeasurementResult(MeasurementStatus.ERROR, note=f"Measurement failed: {exc}")

        has_geom = geom is not None and not geom.is_empty
        features.append(
            Feature(
                file_id=file_id,
                feature_index=idx,
                layer=layer,
                geometry_type=geom.geom_type if geom is not None else None,
                geometry=json.dumps(mapping(geom)) if has_geom else None,
                properties=json.dumps(props, ensure_ascii=False),
                crs=src_label,
                measurement_status=m.status,
                projected_crs=m.projected_crs,
                area_m2=m.area_m2,
                perimeter_m=m.perimeter_m,
                length_m=m.length_m,
                note=m.note,
            )
        )
    return features


def process_file(file_id: str) -> None:
    """Background job. Always leaves the file in COMPLETED or FAILED state."""
    with Session(engine) as session:
        geofile = session.get(GeoFile, file_id)
        if geofile is None:
            logger.error("process_file called for unknown id %s", file_id)
            return

        geofile.status = FileStatus.PROCESSING
        session.add(geofile)
        session.commit()

        try:
            with tempfile.TemporaryDirectory(prefix=f"geo_{file_id}_") as tmp:
                gdf = read_geofile(Path(geofile.stored_path), geofile.file_type, Path(tmp))
                features = build_features(gdf, file_id)

            session.exec(delete(Feature).where(Feature.file_id == file_id))  # idempotent re-runs
            session.add_all(features)
            geofile.feature_count = len(features)
            geofile.crs = crs_label(gdf.crs)
            geofile.status = FileStatus.COMPLETED
            geofile.error = None
        except GeoFileError as exc:
            session.rollback()
            geofile.status = FileStatus.FAILED
            geofile.error = str(exc)
        except Exception:
            session.rollback()
            logger.exception("Unexpected error while processing file %s", file_id)
            geofile.status = FileStatus.FAILED
            geofile.error = "Unexpected error while processing the file."

        geofile.processed_at = datetime.now(timezone.utc)
        session.add(geofile)
        session.commit()
