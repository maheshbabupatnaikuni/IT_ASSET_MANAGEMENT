"""IT Asset Management runtime configuration.

This edition deliberately has no production environment selector. Every writable
path is anchored below this repository's ignored ``.runtime`` directory.
"""

from datetime import timedelta
import os
from pathlib import Path
import secrets


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = (BASE_DIR / ".runtime").resolve()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _runtime_path(name: str) -> Path:
    candidate = (RUNTIME_DIR / name).resolve()
    if candidate != RUNTIME_DIR and RUNTIME_DIR not in candidate.parents:
        raise RuntimeError(f"Unsafe runtime path: {candidate}")
    return candidate


def _secret_key() -> str:
    supplied = os.environ.get("ITAMS_SECRET_KEY", "").strip()
    if supplied:
        if len(supplied) < 32:
            raise RuntimeError("ITAMS_SECRET_KEY must contain at least 32 characters.")
        return supplied
    if _env_flag("ITAMS_ALLOW_EPHEMERAL_SECRET", default=False):
        return secrets.token_urlsafe(48)
    raise RuntimeError(
        "Set ITAMS_SECRET_KEY to a strong random value. For disposable local "
        "development only, set ITAMS_ALLOW_EPHEMERAL_SECRET=true."
    )


class Config:
    SAFE_MODE = True

    PROJECT_ROOT = BASE_DIR
    APP_VERSION = "1.0.0"
    APP_NAME = "IT Asset Management"
    ITAMS_SERVER_NAME = "IT Asset Management"
    SECRET_KEY = _secret_key()

    HOST = os.environ.get("ITAMS_HOST", "127.0.0.1")
    if HOST not in {"127.0.0.1", "localhost", "::1"}:
        public_password = os.environ.get("ITAMS_ADMIN_PASSWORD", "").strip()
        if not public_password or public_password == "admin123":
            raise RuntimeError("Set a unique ITAMS_ADMIN_PASSWORD before non-local hosting.")
    PORT = int(os.environ.get("ITAMS_PORT", "5000"))
    WAITRESS_THREADS = int(os.environ.get("ITAMS_THREADS", "4"))
    LAN_HOSTNAME = "localhost"
    ASSET_PROFILE_BASE_URL = os.environ.get(
        "ITAMS_PUBLIC_BASE_URL", f"http://localhost:{PORT}"
    ).strip().rstrip("/")

    DATABASE_PATH = _runtime_path("itams.sqlite")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH.as_posix()}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}

    MAX_CONTENT_LENGTH = int(os.environ.get("ITAMS_MAX_UPLOAD_MB", "5")) * 1024 * 1024
    UPLOAD_DIR = _runtime_path("uploads")
    DOCUMENT_DIR = _runtime_path("documents")
    LOG_DIR = _runtime_path("logs")
    QR_CODE_DIR = _runtime_path("qr")
    TEMP_DIR = _runtime_path("tmp")
    BACKUP_DIR = _runtime_path("disabled-backups")
    ENV_INSTANCE_DIR = RUNTIME_DIR
    PID_FILE = _runtime_path("itams.pid")

    DEBUG = False
    TESTING = False
    EXTERNAL_NOTIFICATIONS_ENABLED = False
    SINGLE_ACTIVE_SESSION_ENABLED = False
    SESSION_COOKIE_NAME = "itams_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_flag("ITAMS_HTTPS", default=False)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"
    MAX_LOGIN_ATTEMPTS = int(os.environ.get("ITAMS_MAX_LOGIN_ATTEMPTS", "8"))
    LOGIN_WINDOW_SECONDS = int(os.environ.get("ITAMS_LOGIN_WINDOW_SECONDS", "60"))


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SINGLE_ACTIVE_SESSION_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-only-secret-key-that-is-never-used-outside-tests"
