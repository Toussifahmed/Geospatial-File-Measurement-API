import os
import tempfile

# Must be set before the app package is imported.
_tmp = tempfile.mkdtemp(prefix="geo-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:  # lifespan creates the tables
        yield c


def upload(client, name: str, content: bytes, content_type: str = "application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, content, content_type)})
