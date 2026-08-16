# IT Asset Management

IT Asset Management is a Flask application for tracking workplace assets, assignments, employees, locations, repairs, infrastructure devices, lifecycle events, approvals, QR profiles, and operational reports.

## Features

- Central asset and infrastructure inventory
- Employee assignment, transfer, return, and repair workflows
- Asset status and lifecycle history
- Role-based and location-based access control
- User and role administration
- Approval workflow for protected changes
- QR generation with read-only public asset profiles
- Dashboard summaries, audit history, and exports
- Strict upload validation and security headers
- Repeatable sample-data reset for local development

## Technology

- Python 3.11+
- Flask, Flask-SQLAlchemy, Flask-WTF, and Werkzeug
- SQLite
- Waitress
- Jinja, HTML, CSS, and JavaScript
- ReportLab, pandas, and openpyxl
- pytest

## Project Structure

- `itams/` — application routes, services, models, templates, and static assets
- `sample_data/` — repeatable local sample-data generation
- `tests/` — integration and security tests
- `tools/` — release verification and repository audit utilities
- `.runtime/` — ignored local database, credentials, QR images, uploads, and logs

The application uses one runtime and does not include production/testing environment switching or deployment controls.

## Run in GitHub Codespaces

[Open IT Asset Management in GitHub Codespaces](https://codespaces.new/maheshbabupatnaikuni/IT_ASSET_MANAGEMENT?quickstart=1)

Create the codespace and wait for setup to finish. Dependencies and sample data are prepared automatically, the application starts on port `5000`, and the forwarded application opens in the browser.

- Username: `admin`
- Password: `test-only-codespace123`

The forwarded port is private to the codespace owner. Runtime data and credentials remain excluded from the repository.

## Run in PowerShell

```powershell
& '.\.venv\Scripts\python.exe' '.\run_app.py'
```

Open `http://127.0.0.1:5000`. Keep the terminal running and press **Ctrl+C** to stop the server.

## First-Time Setup

```powershell
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
    python -m venv '.venv'
}

& '.\.venv\Scripts\python.exe' -m pip install -r '.\requirements.txt' -r '.\requirements-dev.txt'
& '.\.venv\Scripts\python.exe' '.\reset_data.py'
& '.\.venv\Scripts\python.exe' '.\run_app.py'
```

## Login

The local reset creates one administrator account:

- Username: `admin`
- Password: `admin123`

Additional users and custom roles can be created under **Administration**. For non-local hosting, set a unique `ITAMS_ADMIN_PASSWORD`; the application rejects the known local password on a non-loopback host.

## VS Code

1. Open the cloned project folder in VS Code.
2. Run **Terminal → Run Build Task** for initial setup.
3. Open **Run and Debug**, select `IT Asset Management`, and press **F5**.

The browser opens automatically when the server is ready.

## Reset Sample Data

```powershell
& '.\.venv\Scripts\python.exe' '.\reset_data.py'
```

The reset replaces only the project-local `.runtime` directory and recreates the database, sample records, administrator login, and QR images.

## Configuration

Configuration is supplied through environment variables. See `.env.example` for available settings. Secrets, databases, credentials, uploads, generated files, and logs are excluded from version control.

For hosting:

1. Configure a strong `ITAMS_SECRET_KEY` and `ITAMS_ADMIN_PASSWORD`.
2. Set `ITAMS_PUBLIC_BASE_URL` to the final HTTPS origin before regenerating QR images.
3. Set `ITAMS_HTTPS=true` for secure cookies.
4. Use a maintained HTTPS reverse proxy or managed platform.
5. Replace process-local rate limiting and SQLite when deploying multiple instances.

## Validation

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q
& '.\.venv\Scripts\python.exe' '.\tools\verify_release.py'
& '.\.venv\Scripts\python.exe' '.\tools\release_audit.py'
```

See [docs/architecture.md](docs/architecture.md), [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md), and [LICENSING_NOTE.md](LICENSING_NOTE.md) for additional technical and release information.
