"""Start IT Asset Management and open it in the default browser."""

from __future__ import annotations

import os
from pathlib import Path
import socket
from threading import Timer
import webbrowser


os.environ.setdefault("ITAMS_ALLOW_EPHEMERAL_SECRET", "true")
os.environ.setdefault("ITAMS_PUBLIC_BASE_URL", "http://127.0.0.1:5000")
os.environ.setdefault("ITAMS_ADMIN_PASSWORD", "admin123")  # local-only credential

from waitress import serve  # noqa: E402

from config import Config  # noqa: E402
from sample_data.reset import reset  # noqa: E402
from itams import create_app  # noqa: E402


def _ensure_sample_data() -> None:
    database = Path(Config.DATABASE_PATH)
    if not database.is_file():
        counts = reset()
        print(
            "Created sample data: "
            f"{counts['assets']} assets, "
            f"{counts['infrastructure']} infrastructure items."
        )


def _port_is_busy(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex((probe_host, port)) == 0


def main() -> int:
    _ensure_sample_data()
    app = create_app()
    host = app.config["HOST"]
    port = app.config["PORT"]
    local_url = f"http://127.0.0.1:{port}"
    if _port_is_busy(host, port):
        raise SystemExit(
            f"Port {port} is already in use. Stop the existing process or set ITAMS_PORT."
        )

    print(f"Starting {app.config['APP_NAME']}")
    print(f"Open: {local_url}")
    print("Keep this terminal running. Press Ctrl+C to stop the site.")
    if os.environ.get("ITAMS_OPEN_BROWSER", "true").lower() in {"1", "true", "yes", "on"}:
        Timer(1.0, lambda: webbrowser.open(local_url)).start()

    serve(
        app,
        host=host,
        port=port,
        threads=app.config["WAITRESS_THREADS"],
        url_scheme=app.config["PREFERRED_URL_SCHEME"],
        clear_untrusted_proxy_headers=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
