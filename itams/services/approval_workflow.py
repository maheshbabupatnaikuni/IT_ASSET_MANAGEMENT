"""Approval interception and safe replay for Location User mutations."""
from __future__ import annotations

import json
from flask import current_app, flash, g, redirect, request, session, url_for

from ..extensions import db
from ..models import (
    ApprovalRequest, Asset, AssetCategory, Department, Employee, InfrastructureCategory,
    InfrastructureItem, InfrastructureLocation, Location, Manufacturer, RepairLog, Role, User, Vendor,
)
from .audit import audit
from .lbac import can_access_location, current_user, default_location_id


# Only actions already granted by RBAC are queued. Unknown POST routes continue to
# their normal blueprint permission guard and can never use this as an escalation.
PROTECTED_ACTIONS = {
    "assets.new": "asset.add", "assets.edit": "asset.edit", "assets.delete": "asset.delete",
    "assets.block_asset": "asset.block", "assets.unblock": "asset.unblock",
    "assets.state_change": "asset.edit", "assets.quick_status": "asset.edit",
    "assets.custom_field": "asset.custom.manage", "assets.hide_field": "asset.custom.manage",
    "assets.restore_field": "asset.custom.manage", "assets.replace_specification": "asset.specification.replace",
    "assets.qr_regenerate": "asset.qr.manage", "assets.qr_manage": "asset.qr.manage",
    "masters.employees": "employee.add", "masters.employee_update": "employee.edit",
    "masters.employee_delete": "employee.delete", "masters.employee_toggle": "employee.edit",
    "masters.blocked_employees": "employee.block", "masters.employee_unblock": "employee.unblock",
    "masters.employee_custom_field": "employee.custom.manage", "masters.employee_hide_field": "employee.custom.manage",
    "masters.employee_restore_field": "employee.custom.manage", "masters.resign": "clearance.complete",
    "operations.return_asset": "assignment.return", "operations.transfer": "assignment.transfer",
    "operations.repairs": "repair.add", "operations.edit_repair": "repair.edit",
    "operations.receive_repair": "repair.close", "operations.scrap": "asset.scrap",
    "infrastructure.new": "infrastructure.add", "infrastructure.edit": "infrastructure.edit",
    "infrastructure.delete": "infrastructure.delete", "infrastructure.add_history": "infrastructure.history.manage",
    "infrastructure.locations": "infrastructure.location.manage", "infrastructure.location_toggle": "infrastructure.location.manage",
    "infrastructure.add_relationship": "infrastructure.relationship.manage",
    "infrastructure.update_relationship": "infrastructure.relationship.manage",
    "infrastructure.delete_relationship": "infrastructure.relationship.manage",
    "infrastructure.add_port": "infrastructure.port.manage",
    "infrastructure.update_port": "infrastructure.port.manage",
    "infrastructure.delete_port": "infrastructure.port.manage",
    "infrastructure.update_cable": "infrastructure.cable.manage",
    "infrastructure.delete_cable": "infrastructure.cable.manage",
    "infrastructure.add_incident": "infrastructure.incident.manage",
    "infrastructure.update_incident": "infrastructure.incident.manage",
    "infrastructure.add_verification": "infrastructure.verification.manage",
    "infrastructure.qr_regenerate": "infrastructure.qr.manage",
}

ACTION_LABELS = {
    "assets.new": "Create Asset",
    "assets.edit": "Edit Asset",
    "assets.delete": "Delete Asset",
    "assets.block_asset": "Block Asset",
    "assets.unblock": "Unblock Asset",
    "assets.state_change": "Change Asset Status",
    "assets.quick_status": "Change Asset Status",
    "assets.custom_field": "Change Asset Custom Field",
    "assets.replace_specification": "Replace Asset Specification",
    "masters.employees": "Create Employee",
    "masters.employee_update": "Edit Employee",
    "masters.employee_delete": "Delete Employee",
    "masters.employee_toggle": "Change Employee Status",
    "masters.blocked_employees": "Block Employee",
    "masters.employee_unblock": "Unblock Employee",
    "operations.return_asset": "Return Asset",
    "operations.transfer": "Transfer Asset",
    "operations.repairs": "Create Repair",
    "operations.edit_repair": "Edit Repair",
    "operations.receive_repair": "Complete Repair",
    "operations.scrap": "Scrap Asset",
    "infrastructure.new": "Create Infrastructure",
    "infrastructure.edit": "Edit Infrastructure",
    "infrastructure.delete": "Delete Infrastructure",
}

FIELD_LABELS = {
    "asset_tag": "Asset Tag", "serial_number": "Serial Number",
    "category_id": "Category", "manufacturer_id": "Brand / Manufacturer",
    "vendor_id": "Vendor", "department_id": "Department", "location_id": "Location",
    "employee_id": "Employee", "asset_id": "Asset", "employee_code": "Employee ID",
    "employee_department_id": "Employee Department", "employee_location_id": "Employee Location",
    "purchase_date": "Purchase Date", "purchase_cost": "Purchase Cost",
    "warranty_expiry": "Warranty Expiry", "invoice_number": "Invoice Number",
    "ip_address": "IP Address", "mac_address": "MAC Address",
    "mail_domain": "System Domain", "operating_system": "Operating System",
    "os_version": "OS Version", "ram": "RAM", "storage": "Storage",
    "expected_return_date": "Expected Return Date", "assignment_date": "Assignment Date",
    "company_location_id": "Company Location", "location_node_id": "Location Hierarchy",
    "role_id": "Role", "current_password": "Current Password",
}


def action_label(endpoint):
    return ACTION_LABELS.get(endpoint, endpoint.replace(".", " ").replace("_", " ").title())


def request_target(item):
    """Resolve the record affected by a queued request without exposing route internals."""
    try:
        payload = json.loads(item.payload or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    endpoint = payload.get("endpoint") or item.action or ""
    values = payload.get("view_args") or {}
    reference = (
        values.get("asset_id") or values.get("item_id") or values.get("reference")
        or values.get("repair_id") or item.entity_id
    )
    if not reference:
        return "New record"
    try:
        if endpoint.startswith("assets."):
            row = db.session.get(Asset, int(reference))
            return _display_reference(endpoint, "asset_id", reference) if row else f"Asset #{reference}"
        if endpoint.startswith("masters.employee"):
            row = db.session.get(Employee, int(reference))
            return _display_reference(endpoint, "employee_id", reference) if row else f"Employee #{reference}"
        if endpoint.startswith("infrastructure."):
            row = (
                InfrastructureItem.query.filter_by(infrastructure_reference=str(reference)).first()
                or (db.session.get(InfrastructureItem, int(reference)) if str(reference).isdigit() else None)
            )
            return f"{row.name} ({row.infrastructure_reference})" if row else f"Infrastructure {reference}"
        if endpoint.startswith("operations.") and values.get("repair_id"):
            repair = db.session.get(RepairLog, int(reference))
            return _display_reference(endpoint, "asset_id", repair.asset_id) if repair else f"Repair #{reference}"
    except (TypeError, ValueError):
        pass
    return f"Record {reference}"


def _display_reference(endpoint, key, value):
    try:
        item_id = int(value)
    except (TypeError, ValueError):
        return value
    model = None
    if key in ("asset_id",):
        model = Asset
    elif key in ("employee_id",):
        model = Employee
    elif key in ("location_id", "employee_location_id", "company_location_id"):
        model = Location
    elif key in ("department_id", "employee_department_id", "new_department_id"):
        model = Department
    elif key == "manufacturer_id":
        model = Manufacturer
    elif key == "vendor_id":
        model = Vendor
    elif key == "role_id":
        model = Role
    elif key == "category_id":
        model = InfrastructureCategory if endpoint.startswith("infrastructure.") else AssetCategory
    if not model:
        return value
    row = db.session.get(model, item_id)
    if not row:
        return f"Record #{item_id} (no longer available)"
    if isinstance(row, Asset):
        return f"{row.display_asset_tag or row.asset_tag or row.system_asset_reference} ({row.category.name})"
    if isinstance(row, Employee):
        return f"{row.name} ({row.employee_code})"
    if isinstance(row, Location):
        return f"{row.name} ({row.code or 'No code'})"
    return getattr(row, "name", str(value))


def submitted_form_rows(item):
    """Return a safe, human-readable rendering of a queued request payload."""
    try:
        payload = json.loads(item.payload or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    endpoint = payload.get("endpoint") or item.action or ""
    form = payload.get("form") or {}
    rows = []
    for key, values in form.items():
        if key in {"csrf_token", "current_password", "password"} or key.endswith("_other"):
            continue
        values = values if isinstance(values, list) else [values]
        rendered = []
        for value in values:
            if value == "__other__":
                custom = form.get(f"{key}_other") or []
                custom = custom[0] if isinstance(custom, list) and custom else custom
                rendered.append(f"Other: {custom or 'Not entered'}")
            elif value in ("", None):
                rendered.append("Not specified")
            elif key in {"active", "blocked", "edit_employee_details"}:
                rendered.append("Yes" if str(value).lower() in {"1", "true", "on", "yes"} else "No")
            else:
                rendered.append(str(_display_reference(endpoint, key, value)))
        label = FIELD_LABELS.get(key, key.replace("_", " ").title())
        rows.append({"field": key, "label": label, "value": ", ".join(rendered)})
    return rows


def _role_can(user, permission):
    if user.role.name in ("Super Admin", "Administrator"):
        return True
    return any(item.code == permission for item in user.role.permissions)


def _record_location():
    values = request.view_args or {}
    form = request.form
    asset_id = values.get("asset_id") or form.get("asset_id", type=int)
    if asset_id:
        asset = db.session.get(Asset, int(asset_id))
        return asset.location_id if asset else None
    employee_id = values.get("item_id") if request.endpoint and request.endpoint.startswith("masters.employee") else None
    employee_id = employee_id or form.get("employee_id", type=int)
    if employee_id:
        employee = db.session.get(Employee, int(employee_id))
        return employee.location_id if employee else None
    reference = values.get("reference")
    if reference:
        item = InfrastructureItem.query.filter_by(infrastructure_reference=reference).first()
        return item.location_id if item else None
    if request.endpoint == "infrastructure.location_toggle" and values.get("location_id"):
        node = db.session.get(InfrastructureLocation, int(values["location_id"]))
        return node.company_location_id if node else None
    infrastructure_id = values.get("item_id") if request.endpoint and request.endpoint.startswith("infrastructure.") else None
    infrastructure_id = infrastructure_id or form.get("infrastructure_id", type=int)
    if infrastructure_id:
        item = db.session.get(InfrastructureItem, int(infrastructure_id))
        return item.location_id if item else None
    repair_id = values.get("repair_id")
    if repair_id:
        repair = db.session.get(RepairLog, int(repair_id))
        return repair.asset.location_id if repair and repair.asset else None
    for key in ("company_location_id", "location_id", "employee_location_id"):
        if form.get(key, type=int):
            return form.get(key, type=int)
    return default_location_id()


def intercept_location_user_mutation():
    if request.method != "POST" or request.headers.get("X-ITAMS-APPROVAL-REPLAY") == "1":
        return None
    user = current_user()
    permission = PROTECTED_ACTIONS.get(request.endpoint)
    if not user or user.approval_level != "Location User" or not permission:
        return None
    if not _role_can(user, permission):
        return None
    if request.files:
        flash("This uploaded-file action cannot be queued. Ask a Location Admin to perform it.", "danger")
        return redirect(request.referrer or url_for("main.dashboard"))
    location_id = _record_location()
    if not location_id or not can_access_location(location_id):
        g.required_permission = "location.manage.own"
        from flask import abort
        abort(403)
    target_location_id = next((
        request.form.get(key, type=int)
        for key in ("company_location_id", "location_id", "employee_location_id")
        if request.form.get(key, type=int)
    ), None)
    if target_location_id and not can_access_location(target_location_id):
        g.required_permission = "location.transfer"
        from flask import abort
        abort(403)
    if target_location_id and target_location_id != location_id and not _role_can(user, "location.transfer"):
        g.required_permission = "location.transfer"
        from flask import abort
        abort(403)
    form_values = {
        key: values for key, values in request.form.lists()
        if key not in {"csrf_token", "current_password", "password"}
    }
    reason = (
        request.form.get("reason") or request.form.get("remarks")
        or f"Requested {request.endpoint}"
    ).strip()
    payload = {
        "endpoint": request.endpoint,
        "view_args": request.view_args or {},
        "form": form_values,
        "return_url": request.referrer or url_for("main.dashboard"),
        "required_permission": permission,
    }
    item = ApprovalRequest(
        module="Location Action", action=request.endpoint, requested_by=user.full_name,
        entity_id=str(next(iter((request.view_args or {}).values()), "") or ""),
        reason=reason, payload=json.dumps(payload), location_id=location_id,
        target_location_id=target_location_id if target_location_id != location_id else None,
    )
    db.session.add(item);db.session.flush()
    audit("Approval Request", item.id, "Submitted", new={
        "endpoint": request.endpoint, "location_id": location_id, "requested_values": form_values,
    }, reason=reason, location_id=location_id)
    db.session.commit()
    flash("Your change was submitted to Super Admin for approval. No live data changed.", "success")
    return redirect(payload["return_url"])


def replay_approved_request(item, approver_password=""):
    payload = json.loads(item.payload or "{}")
    endpoint = payload.get("endpoint")
    if endpoint not in PROTECTED_ACTIONS or endpoint != item.action:
        raise ValueError("This approval action is not supported.")
    view = current_app.view_functions.get(endpoint)
    if not view:
        raise ValueError("The original operation no longer exists.")
    form_values = dict(payload.get("form") or {})
    if approver_password:
        form_values["current_password"] = [approver_password]
    # Do not carry earlier decision-page flash messages into the replay.  A
    # previous validation error must not be mistaken for a failure of the
    # operation that is executing now.
    identity = {key: value for key, value in session.items() if key != "_flashes"}
    user_id = identity.get("user_id")
    approver = db.session.get(User, user_id) if user_id else None
    assigned_location_ids = {location.id for location in approver.locations} if approver else set()
    is_super_admin = bool(approver and approver.role and approver.role.name in ("Super Admin", "Administrator"))
    is_location_admin = bool(
        approver and approver.active and approver.role and approver.approval_level == "Location Admin"
        and item.location_id in assigned_location_ids and _role_can(approver, "location.approve.own")
    )
    if not approver or not approver.active or not approver.role or not (is_super_admin or is_location_admin):
        raise ValueError("An authorized Super Admin or this location's Location Admin is required.")
    identity["must_change_password"] = False
    path = url_for(endpoint, **(payload.get("view_args") or {}))
    with current_app.test_request_context(
        path, method="POST", data=form_values,
        headers={"X-ITAMS-APPROVAL-REPLAY": "1"},
    ):
        session.update(identity)
        # The outer decision route already authenticated, authorized, and
        # password-verified the Super Admin.  Reusing that trusted identity here
        # avoids treating this internal replay as a second browser login.
        g.current_user = approver
        g.location_ids = None if is_super_admin else tuple(sorted(assigned_location_ids))
        g.approval_replay_authorized = True
        response = view(**(payload.get("view_args") or {}))
        failures = [
            message for category, message in session.get("_flashes", [])
            if category in ("danger", "error")
        ]
        if failures:
            raise ValueError(failures[-1])
        status_code = getattr(response, "status_code", 200)
        location = getattr(response, "location", "") or ""
        if status_code in (401, 403) or (300 <= status_code < 400 and "/login" in location):
            raise ValueError("The approved operation did not execute. The request remains pending.")
        return response
