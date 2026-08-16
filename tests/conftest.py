import os

os.environ.setdefault("ITAMS_SECRET_KEY", "test-only-secret-key-for-automated-verification")
os.environ.setdefault("ITAMS_ADMIN_PASSWORD", "test-only-admin-password")

import pytest

from config import TestConfig
from itams import create_app
from itams.extensions import db
from itams.models import Location, Role, User


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        test_users = [
            ("operator", "IT User", "test-only-operator-password", False),
            ("location_user", "Location User", "test-only-location-password", True),
            ("viewer", "Viewer", "test-only-viewer-password", False),
        ]
        for username, role_name, password, scoped in test_users:
            user = User(
                username=username, full_name=username.replace("_", " ").title(),
                role=Role.query.filter_by(name=role_name).one(),
                approval_level="Location User" if scoped else "Location Admin",
                location_scope_enabled=scoped,
            )
            user.set_password(password)
            db.session.add(user)
            if scoped:
                user.locations.append(Location.query.filter_by(code="OPS").one())
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username="admin", password="test-only-admin-password"):
    return client.post("/auth/login", data={"username": username, "password": password}, follow_redirects=False)
