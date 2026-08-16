"""Waitress entry point for IT Asset Management."""

from pathlib import Path
import os

from waitress import serve

from itams import create_app


app = create_app()


if __name__ == "__main__":
    print(f"Starting {app.config['APP_NAME']}")
    print(f"Local URL: http://localhost:{app.config['PORT']}")
    print(f"Database: {Path(app.config['DATABASE_PATH']).resolve()}")
    serve(
        app,
        host=app.config["HOST"],
        port=app.config["PORT"],
        threads=app.config["WAITRESS_THREADS"],
        url_scheme=app.config["PREFERRED_URL_SCHEME"],
        clear_untrusted_proxy_headers=True,
    )
