import uuid
from datetime import datetime, timezone
from flask import session, has_request_context
from ..extensions import db
from ..models import AssetLifecycle

def record_event(asset, event_type, from_employee=None, to_employee=None, condition_before=None, condition_after=None, reason=None, remarks=None, occurred_at=None):
    event=AssetLifecycle(event_key=uuid.uuid4().hex,occurred_at=occurred_at or datetime.now(timezone.utc).replace(tzinfo=None),event_type=event_type,asset=asset,from_employee=from_employee,to_employee=to_employee,location=asset.location,condition_before=condition_before,condition_after=condition_after,reason=(reason or '').strip() or None,remarks=(remarks or '').strip() or None,performed_by=session.get('user_name','System') if has_request_context() else 'System')
    db.session.add(event); return event
