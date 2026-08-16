import json
from datetime import datetime, timezone
from flask import session, has_request_context
from ..extensions import db
from ..models import AuditLog, MovementLog, Asset, Employee, InfrastructureItem, ApprovalRequest, AssetRequest

def current_user_name(): return session.get("user_name", "System") if has_request_context() else "System"
def audit(entity, entity_id, action, old=None, new=None, reason=None, location_id=None):
    if location_id is None and entity_id is not None:
        model = None
        lowered = (entity or "").lower()
        if "infrastructure" in lowered:
            model = InfrastructureItem
        elif lowered == "employee":
            model = Employee
        elif lowered in {"asset", "asset qr"}:
            model = Asset
        elif lowered == "asset request":
            model = AssetRequest
        elif "approval" in lowered:
            model = ApprovalRequest
        try:
            record = db.session.get(model, int(entity_id)) if model else None
            location_id = getattr(record, "location_id", None)
            if isinstance(record, ApprovalRequest):
                location_id = record.location_id
            elif isinstance(record, AssetRequest):
                location_id = record.requested_for.location_id if record.requested_for else None
        except (TypeError, ValueError):
            location_id = None
    db.session.add(AuditLog(user=current_user_name(), entity=entity, entity_id=str(entity_id), action=action, old_value=json.dumps(old, default=str) if old is not None else None, new_value=json.dumps(new, default=str) if new is not None else None, reason=(reason or "").strip() or None, location_id=location_id))
def movement(asset, action, old_emp=None, old_dept=None, old_loc=None, remarks=""):
    log = MovementLog(movement_id=f"MOV-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}", asset=asset, action=action,
        previous_user=old_emp.name if old_emp else None, new_user=asset.employee.name if asset.employee else None,
        previous_department=old_dept.name if old_dept else None, new_department=asset.department.name if asset.department else None,
        previous_location=old_loc.name if old_loc else None, new_location=asset.location.name if asset.location else None,
        updated_by=current_user_name(), remarks=remarks)
    db.session.add(log)
