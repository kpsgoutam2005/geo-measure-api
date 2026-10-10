import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway data dir BEFORE it is imported.
_TMP = tempfile.mkdtemp(prefix="geo_api_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["MAX_UPLOAD_MB"] = "5"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.make_samples import main as make_samples  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def samples() -> Path:
    out = Path(_TMP) / "samples"
    make_samples(out)
    return out


def upload(client, path: Path, name: str | None = None):
    with path.open("rb") as fh:
        return client.post("/api/files/", files={"file": (name or path.name, fh)})
