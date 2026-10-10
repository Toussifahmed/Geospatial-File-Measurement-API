"""Runtime configuration, read from environment variables (with sane defaults)."""
import os
from pathlib import Path


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./geospatial.db")


def upload_dir() -> Path:
    path = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


# Maximum accepted upload size (bytes) and maximum total size once a zip is extracted.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_UNZIPPED_BYTES = int(os.getenv("MAX_UNZIPPED_MB", "200")) * 1024 * 1024
