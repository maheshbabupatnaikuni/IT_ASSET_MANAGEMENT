#!/usr/bin/env python3
"""Verify templates, routes, and the generated synthetic database."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ITAMS_SECRET_KEY", "test-only-release-verification-secret-key")
os.environ.setdefault("ITAMS_ALLOW_EPHEMERAL_SECRET", "false")
sys.path.insert(0, str(ROOT))

from itams import create_app  # noqa: E402


URL_FOR_RE = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")


def verify_templates(app) -> tuple[int, set[str]]:
    templates = app.jinja_env.list_templates()
    referenced_endpoints: set[str] = set()
    for name in templates:
        app.jinja_env.get_template(name)
        source, _, _ = app.jinja_env.loader.get_source(app.jinja_env, name)
        referenced_endpoints.update(URL_FOR_RE.findall(source))
    return len(templates), referenced_endpoints


def verify_routes(app, endpoints: set[str]) -> None:
    builtins = {"static"}
    missing = sorted(endpoints - set(app.view_functions) - builtins)
    if missing:
        raise RuntimeError("Templates reference missing endpoints: " + ", ".join(missing))


def verify_database(app) -> None:
    database = Path(app.config["DATABASE_PATH"])
    if not database.is_file() or ROOT not in database.resolve().parents:
        raise RuntimeError("Expected a project-local generated database")
    with sqlite3.connect(database) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(f"Database check failed: integrity={integrity}, foreign_keys={len(foreign_keys)}")
        counts = {
            "locations": connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0],
            "employees": connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
            "assets": connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            "infrastructure": connection.execute("SELECT COUNT(*) FROM infrastructure_items").fetchone()[0],
        }
    expected = {"locations": 3, "employees": 15, "assets": 30, "infrastructure": 10}
    if counts != expected:
        raise RuntimeError(f"Synthetic fixture counts changed: expected {expected}, found {counts}")


def main() -> int:
    app = create_app()
    with app.app_context():
        template_count, endpoints = verify_templates(app)
        verify_routes(app, endpoints)
        verify_database(app)
    print(f"Release verification: PASS ({template_count} templates, {len(endpoints)} referenced endpoints)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
