from pathlib import Path
from flask import Flask, flash, redirect, request, url_for, session, render_template, g, has_request_context
from flask_wtf.csrf import CSRFProtect
from flask_wtf.csrf import CSRFError
from .extensions import db
from config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from datetime import timezone
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler
import logging

csrf = CSRFProtect()

@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(connection, _record):
    if connection.__class__.__module__.startswith("sqlite3"):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

def migrate_sqlite_schema():
    """Small idempotent migrations for installations created before ITAMS-018/024."""
    if Config.SAFE_MODE:
        return
    if db.engine.dialect.name != "sqlite": return
    tables = {row[0] for row in db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    migrations = {
        "audit_logs": [("reason", "TEXT"), ("location_id", "INTEGER REFERENCES locations(id)")],
        "backups": [("size_bytes", "INTEGER NOT NULL DEFAULT 0"), ("verified", "BOOLEAN NOT NULL DEFAULT 0")],
        "assets": [("purchase_cost", "FLOAT"), ("invoice_number", "VARCHAR(100)"), ("manually_created", "BOOLEAN NOT NULL DEFAULT 1"), ("state_version", "INTEGER NOT NULL DEFAULT 1"), ("system_asset_reference", "VARCHAR(30)"), ("location_asset_reference", "VARCHAR(40)")],
        "employees": [("designation", "VARCHAR(120)"), ("blocked", "BOOLEAN NOT NULL DEFAULT 0"), ("blocked_reason", "TEXT"), ("blocked_date", "DATE"), ("blocked_by", "VARCHAR(120)"), ("unblocked_date", "DATE"), ("employment_status", "VARCHAR(30) NOT NULL DEFAULT 'Active'"), ("it_clearance_status", "VARCHAR(30) NOT NULL DEFAULT 'Not Required'"), ("manually_created", "BOOLEAN NOT NULL DEFAULT 1")],
        "repair_logs": [("invoice_number", "VARCHAR(100)"), ("parts_changed", "TEXT"), ("payment_type", "VARCHAR(30)"), ("condition_before", "VARCHAR(50)"), ("condition_after", "VARCHAR(50)")],
        "vendors": [("services", "TEXT")],
        "asset_assignments": [("accessories_returned", "TEXT")],
        "users": [
            ("must_change_password", "BOOLEAN NOT NULL DEFAULT 0"), ("last_login_at", "DATETIME"),
            ("approval_level", "VARCHAR(40) NOT NULL DEFAULT 'Location User'"),
            ("location_scope_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
            ("active_session_token_hash", "VARCHAR(64)"),
            ("active_session_started_at", "DATETIME"),
            ("active_session_last_seen_at", "DATETIME"),
            ("active_session_ip", "VARCHAR(80)"),
            ("active_session_user_agent", "VARCHAR(255)"),
        ],
        "locations": [
            ("code", "VARCHAR(40)"), ("short_name", "VARCHAR(80)"), ("address", "TEXT"),
            ("city", "VARCHAR(100)"), ("state", "VARCHAR(100)"), ("country", "VARCHAR(100)"),
            ("remarks", "TEXT"),
        ],
        "roles": [("description","TEXT"),("is_system_role","BOOLEAN NOT NULL DEFAULT 0"),("is_active","BOOLEAN NOT NULL DEFAULT 1"),("created_by","VARCHAR(120)")],
        "asset_requests": [("state_version", "INTEGER NOT NULL DEFAULT 1"), ("location_validated", "BOOLEAN NOT NULL DEFAULT 0")],
        "asset_blocks": [("previous_status", "VARCHAR(30)")],
        "infrastructure_items": [
            ("site", "VARCHAR(120)"), ("rack", "VARCHAR(120)"), ("rack_unit", "VARCHAR(40)"),
            ("location_node_id", "INTEGER REFERENCES infrastructure_locations(id)"),
            ("management_ip", "VARCHAR(80)"), ("subnet", "VARCHAR(80)"),
            ("gateway", "VARCHAR(80)"), ("dns", "VARCHAR(255)"),
            ("network_vlan", "VARCHAR(80)"), ("ip_assignment", "VARCHAR(20)"),
            ("last_verified_date", "DATE"), ("verified_by", "VARCHAR(120)"),
            ("verification_condition", "VARCHAR(80)"), ("next_verification_date", "DATE"),
            ("location_id", "INTEGER REFERENCES locations(id)"),
        ],
        "approval_requests": [
            ("location_id", "INTEGER REFERENCES locations(id)"),
            ("target_location_id", "INTEGER REFERENCES locations(id)"),
        ],
        "infrastructure_documents": [("photo_type", "VARCHAR(40)")],
        "infrastructure_locations": [("company_location_id", "INTEGER REFERENCES locations(id)")],
    }
    migration_backup_created = False
    for table, columns in migrations.items():
        if table not in tables: continue
        existing = {row[1] for row in db.session.execute(text(f"PRAGMA table_info({table})"))}
        for name, definition in columns:
            if name not in existing:
                migration_backup_created = True
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    if "locations" in tables:
        rows = db.session.execute(text("SELECT id,name,code FROM locations ORDER BY id")).all()
        used = {str(row.code).upper() for row in rows if row.code}
        import re
        for row in rows:
            if row.code:
                continue
            base = re.sub(r"[^A-Z0-9]+", "-", (row.name or "LOC").upper()).strip("-")[:32] or "LOC"
            code, suffix = base, 1
            while code in used:
                suffix += 1
                code = f"{base[:27]}-{suffix}"
            used.add(code)
            db.session.execute(text("UPDATE locations SET code=:code WHERE id=:id"), {"code": code, "id": row.id})
        db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_locations_code ON locations(code)"))
    default_location_id = None
    if "locations" in tables:
        default_location_id = db.session.execute(text(
            "SELECT id FROM locations WHERE active=1 ORDER BY CASE WHEN LOWER(name)='unspecified' THEN 0 ELSE 1 END,id LIMIT 1"
        )).scalar()
    if default_location_id:
        if "assets" in tables:
            db.session.execute(text("UPDATE assets SET location_id=:location WHERE location_id IS NULL"), {"location": default_location_id})
        if "infrastructure_items" in tables:
            db.session.execute(text("UPDATE infrastructure_items SET location_id=:location WHERE location_id IS NULL"), {"location": default_location_id})
        if "infrastructure_locations" in tables:
            db.session.execute(text(
                "UPDATE infrastructure_locations SET company_location_id=COALESCE("
                "(SELECT location_id FROM infrastructure_items WHERE location_node_id=infrastructure_locations.id "
                "AND location_id IS NOT NULL GROUP BY location_id ORDER BY COUNT(*) DESC LIMIT 1),:location) "
                "WHERE company_location_id IS NULL"
            ), {"location": default_location_id})
        if "audit_logs" in tables:
            db.session.execute(text(
                "UPDATE audit_logs SET location_id=(SELECT location_id FROM assets WHERE assets.id=CAST(audit_logs.entity_id AS INTEGER)) "
                "WHERE location_id IS NULL AND LOWER(entity) IN ('asset','asset qr')"
            ))
            db.session.execute(text(
                "UPDATE audit_logs SET location_id=(SELECT location_id FROM employees WHERE employees.id=CAST(audit_logs.entity_id AS INTEGER)) "
                "WHERE location_id IS NULL AND LOWER(entity)='employee'"
            ))
            db.session.execute(text(
                "UPDATE audit_logs SET location_id=(SELECT location_id FROM infrastructure_items WHERE infrastructure_items.id=CAST(audit_logs.entity_id AS INTEGER)) "
                "WHERE location_id IS NULL AND LOWER(entity)='infrastructure'"
            ))
        if "approval_requests" in tables:
            db.session.execute(text(
                "UPDATE approval_requests SET location_id=(SELECT location_id FROM assets WHERE assets.id=CAST(approval_requests.entity_id AS INTEGER)) "
                "WHERE location_id IS NULL AND LOWER(module) LIKE '%asset%'"
            ))
    if "user_locations" in tables and "users" in tables and "roles" in tables:
        # Seed legacy users only once.  This must never run as an unconditional
        # startup operation: doing so would silently add every newly-created
        # location to every restricted user and defeat location isolation.
        if "settings" in tables:
            location_seeded = db.session.execute(text(
                "SELECT 1 FROM settings WHERE key='lbac_user_locations_v1'"
            )).first()
            if not location_seeded:
                db.session.execute(text(
                    "INSERT OR IGNORE INTO user_locations(user_id,location_id,assigned_by,assigned_at) "
                    "SELECT u.id,l.id,'LBAC Legacy Migration',CURRENT_TIMESTAMP FROM users u "
                    "JOIN roles r ON r.id=u.role_id CROSS JOIN locations l "
                    "WHERE r.name<>'Super Admin' AND l.active=1 "
                    "AND NOT EXISTS (SELECT 1 FROM user_locations ul WHERE ul.user_id=u.id)"
                ))
                db.session.execute(text(
                    "INSERT INTO settings(key,value) VALUES('lbac_user_locations_v1','completed')"
                ))
        db.session.execute(text(
            "UPDATE users SET location_scope_enabled=1, "
            "approval_level=CASE WHEN approval_level IS NULL OR approval_level='' THEN "
            "CASE WHEN role_id IN (SELECT id FROM roles WHERE name='Admin') THEN 'Location Admin' ELSE 'Location User' END "
            "ELSE approval_level END "
            "WHERE role_id NOT IN (SELECT id FROM roles WHERE name='Super Admin')"
        ))
        db.session.execute(text(
            "UPDATE users SET approval_level='Super Admin',location_scope_enabled=0 "
            "WHERE role_id IN (SELECT id FROM roles WHERE name='Super Admin')"
        ))
        if "settings" in tables:
            approval_migrated = db.session.execute(text(
                "SELECT 1 FROM settings WHERE key='lbac_approval_levels_v1'"
            )).first()
            if not approval_migrated:
                db.session.execute(text(
                    "UPDATE users SET approval_level='Location Admin' "
                    "WHERE role_id IN (SELECT id FROM roles WHERE name='Admin')"
                ))
                db.session.execute(text(
                    "INSERT INTO settings(key,value) VALUES('lbac_approval_levels_v1','completed')"
                ))
    if "settings" in tables:
        db.session.execute(text(
            "UPDATE settings SET value='IT Asset Management' "
            "WHERE key='company_name' AND value<>'IT Asset Management'"
        ))
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_assets_location_id ON assets(location_id)",
        "CREATE INDEX IF NOT EXISTS ix_employees_location_id ON employees(location_id)",
        "CREATE INDEX IF NOT EXISTS ix_infrastructure_items_location_id ON infrastructure_items(location_id)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_location_id ON audit_logs(location_id)",
        "CREATE INDEX IF NOT EXISTS ix_approval_requests_location_id ON approval_requests(location_id)",
    ):
        try:
            db.session.execute(text(statement))
        except Exception:
            db.session.rollback()
    if "asset_assignments" in tables:
        duplicates=db.session.execute(text("SELECT asset_id FROM asset_assignments WHERE active=1 GROUP BY asset_id HAVING COUNT(*)>1 LIMIT 1")).first()
        if not duplicates:db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_assignment_asset ON asset_assignments(asset_id) WHERE active=1"))
    if "asset_blocks" in tables:
        duplicates=db.session.execute(text("SELECT asset_id FROM asset_blocks WHERE active=1 GROUP BY asset_id HAVING COUNT(*)>1 LIMIT 1")).first()
        if not duplicates:db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_asset_block ON asset_blocks(asset_id) WHERE active=1"))
    if "assets" in tables:
        asset_ids = db.session.execute(text(
            "SELECT id FROM assets WHERE system_asset_reference IS NULL OR TRIM(system_asset_reference) = '' ORDER BY id"
        )).scalars().all()
        for asset_id in asset_ids:
            db.session.execute(
                text("UPDATE assets SET system_asset_reference=:reference WHERE id=:asset_id"),
                {"reference": f"ITAM-AST-{asset_id:06d}", "asset_id": asset_id},
            )
        duplicate_reference = db.session.execute(text(
            "SELECT system_asset_reference FROM assets GROUP BY system_asset_reference HAVING COUNT(*)>1 LIMIT 1"
        )).first()
        if not duplicate_reference:
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_system_asset_reference ON assets(system_asset_reference)"
            ))
        next_reference = db.session.execute(text(
            "SELECT COALESCE(MAX(CAST(SUBSTR(system_asset_reference, 9) AS INTEGER)), 0) + 1 FROM assets "
            "WHERE system_asset_reference LIKE 'ITAM-AST-%'"
        )).scalar_one()
        db.session.execute(text(
            "INSERT INTO system_counters(name,next_value) VALUES('asset_reference',:next_value) "
            "ON CONFLICT(name) DO UPDATE SET next_value=MAX(system_counters.next_value,excluded.next_value)"
        ), {"next_value": next_reference})
        # Add a location-specific business reference without changing the
        # original permanent reference used by existing profile URLs and QR codes.
        import re
        def location_prefix(code, name):
            source=(code or name or "LOC").upper()
            if source=="HO" or "CORPORATE OFFICE" in source:return "ITAM"
            if "OPERATIONS" in source:return "HIN"
            if source=="DEV" or source.startswith("DEV-"):return "DEV"
            if "OPERATIONS" in source or "Operations Site" in source:return "SITE41"
            return re.sub(r"[^A-Z0-9]+","",source)[:12] or "LOC"
        location_rows=db.session.execute(text(
            "SELECT a.id,l.code,l.name FROM assets a LEFT JOIN locations l ON l.id=a.location_id "
            "WHERE a.location_asset_reference IS NULL OR TRIM(a.location_asset_reference)='' "
            "ORDER BY a.location_id,a.id"
        )).all()
        next_by_prefix={}
        for row in location_rows:
            prefix=location_prefix(row.code,row.name)
            if prefix not in next_by_prefix:
                current=db.session.execute(text(
                    "SELECT COALESCE(MAX(CAST(SUBSTR(location_asset_reference,LENGTH(:prefix)+6) AS INTEGER)),0)+1 "
                    "FROM assets WHERE location_asset_reference LIKE :pattern"
                ),{"prefix":prefix,"pattern":f"{prefix}-AST-%"}).scalar_one()
                next_by_prefix[prefix]=current
            number=next_by_prefix[prefix];next_by_prefix[prefix]+=1
            db.session.execute(text(
                "UPDATE assets SET location_asset_reference=:reference WHERE id=:asset_id"
            ),{"reference":f"{prefix}-AST-{number:06d}","asset_id":row.id})
        duplicate_location_reference=db.session.execute(text(
            "SELECT location_asset_reference FROM assets GROUP BY location_asset_reference HAVING COUNT(*)>1 LIMIT 1"
        )).first()
        if not duplicate_location_reference:
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_location_asset_reference ON assets(location_asset_reference)"
            ))
        for prefix,next_value in next_by_prefix.items():
            db.session.execute(text(
                "INSERT INTO system_counters(name,next_value) VALUES(:name,:next_value) "
                "ON CONFLICT(name) DO UPDATE SET next_value=MAX(system_counters.next_value,excluded.next_value)"
            ),{"name":f"asset_location_reference::{prefix}","next_value":next_value})
    if "asset_qr_identities" in tables:
        duplicates=db.session.execute(text("SELECT asset_id FROM asset_qr_identities WHERE active=1 GROUP BY asset_id HAVING COUNT(*)>1 LIMIT 1")).first()
        if not duplicates:db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_qr_identity_asset ON asset_qr_identities(asset_id) WHERE active=1"))
    if "asset_qr_history" in tables:
        duplicates=db.session.execute(text("SELECT asset_id FROM asset_qr_history WHERE active=1 GROUP BY asset_id HAVING COUNT(*)>1 LIMIT 1")).first()
        if not duplicates:db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_qr_history_asset ON asset_qr_history(asset_id) WHERE active=1"))
    if "infrastructure_qr_identities" in tables:
        duplicates=db.session.execute(text("SELECT infrastructure_id FROM infrastructure_qr_identities WHERE active=1 GROUP BY infrastructure_id HAVING COUNT(*)>1 LIMIT 1")).first()
        if not duplicates:db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_infrastructure_qr_identity ON infrastructure_qr_identities(infrastructure_id) WHERE active=1"))
    if "infrastructure_qr_history" in tables:
        duplicates=db.session.execute(text("SELECT infrastructure_id FROM infrastructure_qr_history WHERE active=1 GROUP BY infrastructure_id HAVING COUNT(*)>1 LIMIT 1")).first()
        if not duplicates:db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_infrastructure_qr_history ON infrastructure_qr_history(infrastructure_id) WHERE active=1"))
    if "infrastructure_items" in tables:
        duplicate_ip=db.session.execute(text(
            "SELECT LOWER(TRIM(COALESCE(management_ip,ip_address))) value FROM infrastructure_items "
            "WHERE TRIM(COALESCE(management_ip,ip_address,''))<>'' GROUP BY value HAVING COUNT(*)>1 LIMIT 1"
        )).first()
        if not duplicate_ip:
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_infrastructure_effective_management_ip "
                "ON infrastructure_items(LOWER(TRIM(COALESCE(management_ip,ip_address)))) "
                "WHERE TRIM(COALESCE(management_ip,ip_address,''))<>''"
            ))
    db.session.commit()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config.setdefault("APP_VERSION","1.0.0")
    app.config.setdefault("EXTERNAL_NOTIFICATIONS_ENABLED",False)
    app.config.setdefault("SINGLE_ACTIVE_SESSION_ENABLED",not app.config.get("TESTING",False))
    app.config.setdefault("LOG_DIR",Path(app.root_path).parent/"logs")
    app.config.setdefault("QR_CODE_DIR",Path(app.root_path)/"static"/"generated"/"qr_codes")
    app.config.setdefault("DOCUMENT_DIR",Path(app.root_path).parent/"documents")
    app.config.setdefault("TEMP_DIR",Path(app.root_path).parent/"tmp")
    app.config.setdefault("ENV_INSTANCE_DIR",Path(app.root_path).parent/"instance")
    app.config.setdefault("PID_FILE",Path(app.config["ENV_INSTANCE_DIR"])/"itams.pid")
    app.config.setdefault("ASSET_PROFILE_BASE_URL","http://localhost:5000")
    for key in ("UPLOAD_DIR","DOCUMENT_DIR","LOG_DIR","QR_CODE_DIR","TEMP_DIR","ENV_INSTANCE_DIR"):
        Path(app.config[key]).mkdir(parents=True,exist_ok=True)
    log_file=Path(app.config["LOG_DIR"])/"itams.log"
    file_handler=RotatingFileHandler(log_file,maxBytes=5*1024*1024,backupCount=10,encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(remote_addr)s %(message)s"))
    class RequestContextFilter(logging.Filter):
        def filter(self,record):
            record.remote_addr=request.remote_addr if has_request_context() else "-"
            return True
    file_handler.addFilter(RequestContextFilter())
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    local_zone = ZoneInfo("Asia/Kolkata")
    def local_datetime(value, fmt="%d %b %Y %I:%M:%S %p"):
        if not value: return "—"
        if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
        return value.astimezone(local_zone).strftime(fmt)
    app.jinja_env.filters["local_datetime"] = local_datetime
    Path(app.instance_path).mkdir(exist_ok=True)
    db.init_app(app)
    csrf.init_app(app)
    from .services.lbac import install_lbac_listener, refresh_request_scope
    from .services.approval_workflow import intercept_location_user_mutation
    install_lbac_listener()
    app.before_request(refresh_request_scope)
    app.before_request(intercept_location_user_mutation)
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        return response
    @app.errorhandler(CSRFError)
    def handle_csrf_error(_error):
        if session.get("user_id"):
            flash("The form expired. Please try again.","warning"); return redirect(request.referrer or url_for("main.dashboard"))
        flash("Your session has expired. Please login again.","warning"); return redirect(url_for("auth.login"))
    @app.errorhandler(400)
    def invalid_request(error):
        app.logger.warning("Invalid request for %s %s: %s", request.method, request.path, error)
        return render_template("error.html",title="Invalid Request",message="The request was invalid or expired. Please check the information and try again."),400
    @app.errorhandler(403)
    def access_denied(_error):
        app.logger.warning("Access denied for %s %s", request.method, request.path)
        return render_template("access_denied.html",permission=getattr(g,"required_permission",None)),403
    @app.errorhandler(404)
    def not_found(_error):
        app.logger.info("Page not found: %s %s", request.method, request.path)
        return render_template("error.html",title="Page Not Found",message="The requested page could not be found."),404
    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        app.logger.exception("Unhandled application error: %s", error)
        return render_template("error.html",title="Something Went Wrong",message="The request could not be completed. Please contact the ITAMS administrator."),500
    @app.errorhandler(503)
    def service_unavailable(error):
        app.logger.warning("Service unavailable for %s %s: %s",request.method,request.path,error)
        return render_template("error.html",title="Service Temporarily Unavailable",message="This feature is temporarily unavailable. Please contact the ITAMS administrator."),503
    from .routes import main_bp, auth_bp, masters_bp, assets_bp, operations_bp, reports_bp, admin_bp, requests_bp, infrastructure_bp, public_bp
    blueprints = [main_bp, public_bp, auth_bp, masters_bp, assets_bp, infrastructure_bp, operations_bp, reports_bp, admin_bp, requests_bp]
    try:
        from .routes import invoices_bp
        blueprints.append(invoices_bp)
    except ImportError:
        pass
    for blueprint in blueprints:
        app.register_blueprint(blueprint)
    from .routes.helpers import can, normalized_role
    from .services.lbac import active_locations, allowed_location_ids, workspace_location_id, workspace_locations, workspace_label
    from .services.ui_configuration import application_setting, current_ui_module, field_configuration_payload
    app.jinja_env.globals.update(
        can=can, current_role=normalized_role,
        current_location_ids=allowed_location_ids, accessible_locations=active_locations,
        workspace_location_id=workspace_location_id,
        workspace_locations=workspace_locations,
        workspace_label=workspace_label,
        app_version=app.config["APP_VERSION"],
        app_setting=application_setting,
        ui_module=current_ui_module,
        ui_field_configuration=field_configuration_payload,
    )
    with app.app_context():
        from .models import User, Role
        db.create_all()
        migrate_sqlite_schema()
        if app.config.get("TESTING") and app.config["SINGLE_ACTIVE_SESSION_ENABLED"]:
            User.query.update({
                User.active_session_token_hash: None,
                User.active_session_started_at: None,
                User.active_session_last_seen_at: None,
                User.active_session_ip: None,
                User.active_session_user_agent: None,
            }, synchronize_session=False)
            db.session.commit()
        if not User.query.first():
            from .services.seed import seed_database
            seed_database()
        else:
            from .services.seed import generate_sample_import
            generate_sample_import()
        from .services.permissions import seed_permissions
        seed_permissions()
        from .services.infrastructure import seed_infrastructure_categories
        seed_infrastructure_categories()
        from .services.ui_configuration import seed_ui_configuration
        seed_ui_configuration()
        from .services.simple_import import reconcile_imported_assignments
        reconciled = reconcile_imported_assignments()
        if reconciled:
            app.logger.info("Reconciled %s imported asset assignments", reconciled)
    app.logger.info("ITAMS application initialized")
    return app
