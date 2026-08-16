"""Atomic permanent deletion for an explicitly confirmed asset."""

import json
from pathlib import Path

from flask import current_app, session

from ..extensions import db
from ..models import (
    ApprovalRequest, AssetAssignment, AssetBlock, AssetDocument, AssetLifecycle,
    AssetQRHistory, AssetQRIdentity, AssetRequest, AssetSpecificationChange,
    AuditLog, CustomAttribute, DynamicFieldValue, FieldProvenance,
    HiddenProfileField, ImportSourceLink, ImportStagingRow, MovementLog,
    RepairLog, ScrapLog,
)
from .qr_management import qr_file_path


def _related_action_request(item, asset_id):
    if item.module == "Cross-Location Transfer" and item.entity_id == str(asset_id):
        return True
    try:
        payload = json.loads(item.payload or "{}")
    except (TypeError, ValueError):
        return False
    values = payload.get("view_args") or {}
    form = payload.get("form") or {}
    return str(values.get("asset_id", "")) == str(asset_id) or str(form.get("asset_id", "")) == str(asset_id)


def permanently_delete_asset(asset, reason):
    """Delete an asset and all dependent operational rows in one transaction."""
    asset_id = asset.id
    snapshot = {
        "location_asset_reference": asset.location_asset_reference,
        "system_asset_reference": asset.system_asset_reference,
        "asset_tag": asset.asset_tag,
        "source_asset_tag": asset.source_asset_tag,
        "status": asset.status,
        "location": asset.location.name if asset.location else None,
    }
    qr_path = qr_file_path(asset)
    document_paths = []
    for document in AssetDocument.query.filter_by(asset_id=asset_id).all():
        document_paths.extend([
            Path(current_app.config["DOCUMENT_DIR"]) / "asset_documents" / document.stored_name,
            Path(current_app.config["UPLOAD_DIR"]) / "asset_documents" / document.stored_name,
        ])

    counts = {}
    delete_models = (
        AssetQRHistory, AssetQRIdentity, AssetSpecificationChange,
        HiddenProfileField, AssetDocument, CustomAttribute, FieldProvenance,
        ImportSourceLink, DynamicFieldValue, AssetRequest, AssetBlock,
        AssetLifecycle, AssetAssignment, ScrapLog, RepairLog, MovementLog,
    )
    for model in delete_models:
        count = db.session.query(model).filter(model.asset_id == asset_id).delete(synchronize_session=False)
        if count:
            counts[model.__tablename__] = count

    detached = ImportStagingRow.query.filter_by(live_asset_id=asset_id).update(
        {ImportStagingRow.live_asset_id: None}, synchronize_session=False
    )
    if detached:
        counts["import_staging_rows_detached"] = detached

    for item in ApprovalRequest.query.all():
        if _related_action_request(item, asset_id):
            db.session.delete(item)
            counts["approval_requests"] = counts.get("approval_requests", 0) + 1

    db.session.delete(asset)
    db.session.flush()
    db.session.add(AuditLog(
        user=session.get("user_name", "System"), entity="Asset Deletion",
        entity_id=snapshot["location_asset_reference"], action="Permanent Delete",
        old_value=json.dumps(snapshot, default=str),
        new_value=json.dumps({"dependent_rows_removed": counts, "audit_history_retained": True}, default=str),
        reason=(reason or "").strip(), location_id=None,
    ))
    db.session.commit()

    qr_path.unlink(missing_ok=True)
    for path in document_paths:
        path.unlink(missing_ok=True)
    return counts
