"""Database-backed single active login enforcement."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import secrets
import socket

from flask import current_app, flash, request, session
from sqlalchemy import func, or_, update

from ..extensions import db
from ..models import Asset, User, utc_now


SESSION_TOKEN_KEY = "active_session_token"
TOUCH_INTERVAL = timedelta(seconds=60)


def enabled():
    return bool(current_app.config.get("SINGLE_ACTIVE_SESSION_ENABLED", True))


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clear_values():
    return {
        "active_session_token_hash": None,
        "active_session_started_at": None,
        "active_session_last_seen_at": None,
        "active_session_ip": None,
        "active_session_user_agent": None,
    }


def active_session_location(user):
    """Describe the workstation holding a user's current login lock."""
    raw_ip = (user.active_session_ip or "").strip()
    ip_address = raw_ip.removeprefix("::ffff:") if raw_ip else "Not available"
    candidates = {raw_ip, ip_address} - {"", "Not available"}
    hostname = None
    asset = None
    if candidates:
        asset = (
            Asset.query
            .filter(Asset.ip_address.in_(candidates))
            .order_by(Asset.updated_at.desc(), Asset.id.desc())
            .first()
        )
    if ip_address != "Not available":
        try:
            hostname = socket.gethostbyaddr(ip_address)[0].strip()
        except (OSError, socket.error):
            hostname = None
    if not asset and hostname:
        names = {hostname.lower(), hostname.split(".", 1)[0].lower()}
        asset = (
            Asset.query
            .filter(func.lower(Asset.hostname).in_(names))
            .order_by(Asset.updated_at.desc(), Asset.id.desc())
            .first()
        )
    if not asset:
        return {
            "asset_tag": "Not identified in asset inventory",
            "ip_address": ip_address,
            "hostname": hostname or "Not available",
            "location": "Not identified in asset inventory",
        }
    return {
        "asset_tag": (
            asset.display_asset_tag
            or asset.asset_tag
            or asset.system_asset_reference
            or "Not identified in asset inventory"
        ),
        "ip_address": ip_address,
        "hostname": hostname or asset.hostname or "Not available",
        "location": asset.location.name if asset.location else "Not recorded",
    }


def active_session_warning(user):
    details = active_session_location(user)
    return (
        "This user is already logged in. "
        f"Asset Tag: {details['asset_tag']} | "
        f"IP Address: {details['ip_address']} | "
        f"System Name: {details['hostname']} | "
        f"Location: {details['location']}. "
        "Log out from that system or wait 5 minutes without activity."
    )


def claim_active_session(user):
    """Atomically claim a free/expired lock or replace one from the same workstation."""
    if not enabled():
        return "testing-session"
    now = utc_now()
    cutoff = now - current_app.permanent_session_lifetime
    token = secrets.token_urlsafe(32)
    statement = (
        update(User)
        .where(
            User.id == user.id,
            or_(
                User.active_session_token_hash.is_(None),
                User.active_session_last_seen_at < cutoff,
                User.active_session_ip == request.remote_addr,
            ),
        )
        .values(
            active_session_token_hash=_token_hash(token),
            active_session_started_at=now,
            active_session_last_seen_at=now,
            active_session_ip=request.remote_addr,
            active_session_user_agent=(request.user_agent.string or "")[:255] or None,
        )
        .execution_options(synchronize_session=False)
    )
    claimed = db.session.execute(statement).rowcount == 1
    return token if claimed else None


def validate_active_session(user):
    """Validate and periodically touch the current request's login lock."""
    if not enabled():
        return True
    token = session.get(SESSION_TOKEN_KEY)
    expected = user.active_session_token_hash or ""
    actual = _token_hash(token) if token else ""
    if not token or not expected or not hmac.compare_digest(actual, expected):
        session.clear()
        flash("This login session is no longer active. Please login again.", "warning")
        return False
    now = utc_now()
    cutoff = now - current_app.permanent_session_lifetime
    if not user.active_session_last_seen_at or user.active_session_last_seen_at < cutoff:
        release_active_session(user.id, token)
        db.session.commit()
        session.clear()
        flash("Your session has expired. Please login again.", "warning")
        return False
    if user.active_session_last_seen_at < now - TOUCH_INTERVAL:
        statement = (
            update(User)
            .where(
                User.id == user.id,
                User.active_session_token_hash == expected,
            )
            .values(active_session_last_seen_at=now)
            .execution_options(synchronize_session=False)
        )
        db.session.execute(statement)
        db.session.commit()
    return True


def release_active_session(user_id, token=None):
    if not enabled() or not user_id:
        return
    statement = update(User).where(User.id == user_id)
    if token:
        statement = statement.where(User.active_session_token_hash == _token_hash(token))
    db.session.execute(
        statement.values(**_clear_values()).execution_options(synchronize_session=False)
    )


def revoke_active_session(user):
    for field, value in _clear_values().items():
        setattr(user, field, value)
