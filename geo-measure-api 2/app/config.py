"""Application settings, read from environment variables with sensible defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", "data")).resolve()
    )
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "")
    )
    max_upload_mb: int = field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "50"))
    )
    # Guard against zip bombs: total uncompressed size allowed inside an archive.
    max_extracted_mb: int = field(
        default_factory=lambda: int(os.getenv("MAX_EXTRACTED_MB", "500"))
    )

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_extracted_bytes(self) -> int:
        return self.max_extracted_mb * 1024 * 1024


settings = Settings()
