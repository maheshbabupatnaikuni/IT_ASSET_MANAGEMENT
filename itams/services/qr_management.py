from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import secrets

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models import AssetQRHistory, AssetQRIdentity
from .audit import audit, current_user_name


REFERENCE_PATTERN = re.compile(r"^ITAM-AST-\d{6,}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,100}$")


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def configured_base_url() -> str:
    return str(current_app.config.get("ASSET_PROFILE_BASE_URL", "")).strip().rstrip("/")


def asset_profile_url(asset) -> str:
    base_url = configured_base_url()
    if not base_url:
        raise RuntimeError(
            "Asset Profile Base URL is not configured. Set ITAMS_ASSET_PROFILE_BASE_URL "
            "or application.asset_profile_base_url in itams.ini."
        )
    reference = asset.system_asset_reference
    if not reference or not REFERENCE_PATTERN.fullmatch(reference):
        raise RuntimeError("This asset does not have a valid permanent system reference.")
    return f"{base_url}/assets/profile/{reference}"


def qr_destination_url(identity: AssetQRIdentity) -> str:
    base_url = configured_base_url()
    if not base_url:
        raise RuntimeError("Asset Profile Base URL is not configured.")
    return f"{base_url}/q/{identity.token}"


def qr_file_path(asset) -> Path:
    reference = asset.system_asset_reference
    if not reference or not REFERENCE_PATTERN.fullmatch(reference):
        raise RuntimeError("This asset does not have a valid permanent system reference.")
    root = Path(current_app.config["QR_CODE_DIR"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{reference}.png"


def active_identity(asset_id: int):
    return AssetQRIdentity.query.filter_by(asset_id=asset_id, active=True).first()


def active_history(asset_id: int):
    return AssetQRHistory.query.filter_by(asset_id=asset_id, active=True).first()


def qr_history(asset_id: int):
    return AssetQRHistory.query.filter_by(asset_id=asset_id).order_by(AssetQRHistory.version.desc()).all()


def _next_version(asset_id: int) -> int:
    return (db.session.query(func.max(AssetQRHistory.version)).filter_by(asset_id=asset_id).scalar() or 0) + 1


def _new_identity(asset, actor: str):
    identity = AssetQRIdentity(asset_id=asset.id, token=secrets.token_urlsafe(32), created_by=actor, active=True)
    db.session.add(identity)
    db.session.flush()
    return identity


def _archive_current_history(asset_id: int):
    current = active_history(asset_id)
    if current:
        current.active = False
        current.archived_at = utc_now()
    return current


def _add_history(asset, identity, actor: str, reason: str, action: str):
    history = AssetQRHistory(
        asset_id=asset.id, identity_id=identity.id, version=_next_version(asset.id),
        action=action, generated_by=actor, reason=reason, active=True,
    )
    db.session.add(history)
    db.session.flush()
    return history


def _write_qr_image(asset, identity):
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:
        raise RuntimeError("QR support is unavailable. Install the qrcode[pil] package.") from exc
    path = qr_file_path(asset)
    encoded_url = qr_destination_url(identity)
    code = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    code.add_data(encoded_url)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    temporary = path.with_suffix(".tmp.png")
    image.save(temporary, format="PNG")
    temporary.replace(path)
    return path, encoded_url


def ensure_qr_code(asset, actor: str | None = None, reason: str = "Automatic QR generation"):
    """Ensure metadata and image exist; return (path, URL, generated_now)."""
    actor = (actor or current_user_name() or "System").strip()
    identity = active_identity(asset.id)
    history = active_history(asset.id)
    created = False
    if not identity:
        identity = _new_identity(asset, actor)
        history = _add_history(asset, identity, actor, reason, "Generated")
        audit("Asset", asset.id, "QR Generated", new={
            "system_asset_reference": asset.system_asset_reference,
            "qr_version": history.version, "qr_identity_id": identity.id,
        }, reason=reason)
        created = True
    elif not history:
        history = _add_history(asset, identity, actor, reason, "Generated")
        audit("Asset", asset.id, "QR History Initialized", new={"qr_version": history.version}, reason=reason)
        created = True

    path = qr_file_path(asset)
    if not path.exists() or created:
        if not created:
            _archive_current_history(asset.id)
            history = _add_history(asset, identity, actor, reason, "Image Restored")
            audit("Asset", asset.id, "QR Image Restored", new={"qr_version": history.version}, reason=reason)
        path, encoded_url = _write_qr_image(asset, identity)
        return path, encoded_url, True
    return path, qr_destination_url(identity), False


def regenerate_qr_code(asset, mode: str, reason: str, actor: str | None = None):
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Regeneration reason is required.")
    if mode not in ("image", "identity"):
        raise ValueError("Choose a valid QR regeneration mode.")
    actor = (actor or current_user_name() or "System").strip()
    identity = active_identity(asset.id)
    if not identity:
        ensure_qr_code(asset, actor=actor, reason="Initial QR identity created before regeneration")
        identity = active_identity(asset.id)

    previous_history = _archive_current_history(asset.id)
    old_identity_id, old_token = identity.id, identity.token
    if mode == "identity":
        identity.active = False
        identity.invalidated_at = utc_now()
        identity.invalidated_by = actor
        identity.invalidation_reason = reason
        identity = _new_identity(asset, actor)

    action = "Identity Regenerated" if mode == "identity" else "Image Regenerated"
    history = _add_history(asset, identity, actor, reason, action)
    path, encoded_url = _write_qr_image(asset, identity)
    audit("Asset", asset.id, f"QR {action}", old={
        "qr_version": previous_history.version if previous_history else None,
        "qr_identity_id": old_identity_id, "qr_token": old_token,
    }, new={
        "qr_version": history.version, "qr_identity_id": identity.id,
        "qr_token": identity.token, "system_asset_reference": asset.system_asset_reference,
    }, reason=reason)
    return path, encoded_url, history, old_token


def qr_metadata(asset):
    identity, current = active_identity(asset.id), active_history(asset.id)
    try:
        path_exists = qr_file_path(asset).exists()
    except RuntimeError:
        path_exists = False
    return {
        "status": "Active" if identity and current and path_exists else "Missing",
        "identity": identity, "history": current,
        "version": current.version if current else None,
        "generated_at": identity.created_at if identity else None,
        "generated_by": identity.created_by if identity else None,
        "last_regenerated_at": current.generated_at if current and current.action != "Generated" else None,
        "last_regenerated_by": current.generated_by if current and current.action != "Generated" else None,
        "reason": current.reason if current else None,
    }


def resolve_active_token(token: str):
    if not token or not TOKEN_PATTERN.fullmatch(token):
        return None
    return AssetQRIdentity.query.filter_by(token=token, active=True).first()


def generate_for_assets(assets, action: str, reason: str, actor: str | None = None):
    result = {"generated": 0, "existing": 0, "failed": []}
    for asset in assets:
        try:
            with db.session.begin_nested():
                if action == "missing":
                    _path, _url, created = ensure_qr_code(asset, actor=actor, reason=reason)
                    result["generated" if created else "existing"] += 1
                elif action == "image":
                    regenerate_qr_code(asset, "image", reason, actor=actor)
                    result["generated"] += 1
                else:
                    raise ValueError("Unsupported bulk QR action.")
        except (RuntimeError, ValueError) as exc:
            result["failed"].append((asset.system_asset_reference or str(asset.id), str(exc)))
    return result
