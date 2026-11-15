import os
import re
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/aqar_test")
assert TEST_DB.rsplit("/", 1)[-1].endswith("_test"), "refusing to run tests on a non-test database"
os.environ.update(DATABASE_URL=TEST_DB, SECRET_KEY="test-secret", COOKIE_SECURE="0",
                  SEED_PASSWORD="Passw0rd!2026")

from flask_migrate import upgrade  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.seed import seed_command  # noqa: E402

PASSWORD = "Passw0rd!2026"


@pytest.fixture(scope="session")
def app():
    app = create_app()
    with app.app_context():
        upgrade(directory=str(Path(__file__).resolve().parent.parent / "migrations"))
    return app


@pytest.fixture(autouse=True)
def clean_db(app):
    with app.app_context():
        db.session.execute(db.text("TRUNCATE companies RESTART IDENTITY CASCADE"))
        db.session.commit()
        seed_command.callback()
    yield


def _token(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, email: str) -> None:
    token = _token(client.get("/login").get_data(as_text=True))
    resp = client.post("/login", data={"email": email, "password": PASSWORD,
                                       "csrf_token": token})
    assert resp.status_code == 302, resp.get_data(as_text=True)


def csrf(client) -> str:
    return _token(client.get("/").get_data(as_text=True))