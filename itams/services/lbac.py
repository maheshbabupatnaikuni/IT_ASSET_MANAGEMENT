"""Central location-based access control for every ORM read.

RBAC decides what an authenticated user may do.  LBAC limits which rows that
operation may see.  Super Admin is deliberately the only automatic bypass.
"""
from __future__ import annotations

from flask import abort, g, has_request_context, request, session
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from ..extensions import db
from ..models import (
    ApprovalRequest, Asset, AssetAssignment, AssetBlock, AssetDocument,
    AssetLifecycle, AssetQRHistory, AssetQRIdentity, AssetRequest,
    AssetSpecificationChange, AuditLog, CustomAttribute, DynamicFieldValue,
    Employee, FieldProvenance, HiddenProfileField, ImportSourceLink,
    InfrastructureCable, InfrastructureDocument, InfrastructureHistory,
    InfrastructureIncident, InfrastructureItem, InfrastructureLocation, InfrastructurePort,
    InfrastructureQRHistory, InfrastructureQRIdentity,
    InfrastructureRelationship, InfrastructureVerification, Location,
    MovementLog, RepairLog, Role, ScrapLog, User, UserLocation,
)
from .active_sessions import validate_active_session

_listener_installed = False

WORKSPACE_SCOPED_BLUEPRINTS = {
    "main", "assets", "masters", "operations", "requests",
    "infrastructure", "reports",
}


def refresh_request_scope():
    """Refresh identity and locations on every request; never trust stale session ACLs."""
    g.current_user = None
    g.location_ids = None
    g.account_location_ids = None
    g.workspace_location_id = None
    g.workspace_location = None
    user_id = session.get("user_id")
    if not user_id:
        return
    stmt = (
        select(User)
        .where(User.id == user_id)
        .execution_options(lbac_bypass=True)
    )
    user = db.session.execute(stmt).scalar_one_or_none()
    if not user or not user.active or not user.role or not user.role.is_active:
        session.clear()
        return
    if not validate_active_session(user):
        return
    g.current_user = user
    session["role"] = user.role.name
    account_ids = None
    if user.role.name != "Super Admin" and user.location_scope_enabled:
        location_stmt = (
            select(UserLocation.location_id)
            .where(UserLocation.user_id == user.id)
            .execution_options(lbac_bypass=True)
        )
        account_ids = tuple(db.session.execute(location_stmt).scalars().all())
    g.account_location_ids = account_ids

    selected_id = session.get("workspace_location_id")
    try:
        selected_id = int(selected_id) if selected_id else None
    except (TypeError, ValueError):
        selected_id = None
    if selected_id:
        allowed = account_ids is None or selected_id in account_ids
        selected = db.session.execute(
            select(Location)
            .where(Location.id == selected_id, Location.active == True)
            .execution_options(lbac_bypass=True)
        ).scalar_one_or_none() if allowed else None
        if selected:
            g.workspace_location_id = selected.id
            g.workspace_location = selected
        else:
            session.pop("workspace_location_id", None)

    if request.blueprint in WORKSPACE_SCOPED_BLUEPRINTS and g.workspace_location_id:
        g.location_ids = (g.workspace_location_id,)
    else:
        g.location_ids = account_ids


def current_user():
    return getattr(g, "current_user", None) if has_request_context() else None


def is_super_admin():
    user = current_user()
    return bool(user and user.role and user.role.name in ("Super Admin", "Administrator"))


def allowed_location_ids():
    """None means unrestricted; an empty tuple means intentionally no access."""
    return getattr(g, "location_ids", None) if has_request_context() else None


def account_location_ids():
    return getattr(g, "account_location_ids", None) if has_request_context() else None


def workspace_location_id():
    return getattr(g, "workspace_location_id", None) if has_request_context() else None


def workspace_location():
    return getattr(g, "workspace_location", None) if has_request_context() else None


def workspace_locations():
    """Active locations this account may select, regardless of current workspace."""
    if not has_request_context() or not current_user():
        return []
    stmt = select(Location).where(Location.active == True).order_by(Location.name).execution_options(lbac_bypass=True)
    ids = account_location_ids()
    if ids is not None:
        stmt = stmt.where(Location.id.in_(ids))
    return list(db.session.execute(stmt).scalars().all())


def workspace_label():
    selected = workspace_location()
    if selected:
        return selected.name
    locations = workspace_locations()
    if account_location_ids() is None:
        return "All Locations"
    if len(locations) == 1:
        return locations[0].name
    return "All Assigned Locations"


def can_access_location(location_id):
    ids = allowed_location_ids()
    if ids is None:
        return True
    return bool(location_id and int(location_id) in ids)


def require_location_access(location_id):
    if not can_access_location(location_id):
        g.required_permission = "location.view.assigned"
        abort(403)


def active_locations():
    return Location.query.filter_by(active=True).order_by(Location.name).all()


def default_location_id():
    locations = active_locations()
    return locations[0].id if locations else None


def validate_location(location_id, *, required=True):
    if not location_id:
        if required:
            location_id = default_location_id()
            if not location_id:
                raise ValueError("Location is required.")
        else:
            return None
    location = db.session.get(Location, int(location_id))
    if not location or not location.active:
        raise ValueError("Select an active location available to your account.")
    require_location_access(location.id)
    return location


def _asset_ids(ids):
    return select(Asset.id).where(Asset.location_id.in_(ids))


def _employee_ids(ids):
    return select(Employee.id).where(Employee.location_id.in_(ids))


def _infrastructure_ids(ids):
    return select(InfrastructureItem.id).where(InfrastructureItem.location_id.in_(ids))


def install_lbac_listener():
    global _listener_installed
    if _listener_installed:
        return

    @event.listens_for(Session, "do_orm_execute")
    def apply_location_scope(execute_state):
        if (
            not execute_state.is_select
            or execute_state.execution_options.get("lbac_bypass")
            or not has_request_context()
        ):
            return
        ids = allowed_location_ids()
        if ids is None:
            return
        asset_ids = _asset_ids(ids)
        employee_ids = _employee_ids(ids)
        infrastructure_ids = _infrastructure_ids(ids)
        options = [
            with_loader_criteria(Location, Location.id.in_(ids), include_aliases=True),
            with_loader_criteria(Asset, Asset.location_id.in_(ids), include_aliases=True),
            with_loader_criteria(Employee, Employee.location_id.in_(ids), include_aliases=True),
            with_loader_criteria(InfrastructureItem, InfrastructureItem.location_id.in_(ids), include_aliases=True),
            with_loader_criteria(InfrastructureLocation, InfrastructureLocation.company_location_id.in_(ids), include_aliases=True),
            with_loader_criteria(AssetRequest, AssetRequest.requested_for_id.in_(employee_ids), include_aliases=True),
            with_loader_criteria(ApprovalRequest, or_(
                ApprovalRequest.location_id.in_(ids),
                ApprovalRequest.target_location_id.in_(ids),
            ), include_aliases=True),
            with_loader_criteria(AuditLog, AuditLog.location_id.in_(ids), include_aliases=True),
        ]
        for model in (
            AssetAssignment, AssetBlock, AssetDocument, AssetLifecycle,
            AssetQRHistory, AssetQRIdentity, AssetSpecificationChange,
            MovementLog, RepairLog, ScrapLog,
        ):
            options.append(with_loader_criteria(
                model, model.asset_id.in_(asset_ids), include_aliases=True,
            ))
        for model in (
            InfrastructureDocument, InfrastructureHistory, InfrastructureIncident,
            InfrastructurePort, InfrastructureQRHistory, InfrastructureQRIdentity,
            InfrastructureVerification,
        ):
            options.append(with_loader_criteria(
                model, model.infrastructure_id.in_(infrastructure_ids), include_aliases=True,
            ))
        options.extend([
            with_loader_criteria(
                InfrastructureRelationship,
                or_(
                    InfrastructureRelationship.source_id.in_(infrastructure_ids),
                    InfrastructureRelationship.target_id.in_(infrastructure_ids),
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                InfrastructureCable,
                or_(
                    InfrastructureCable.source_device_id.in_(infrastructure_ids),
                    InfrastructureCable.destination_device_id.in_(infrastructure_ids),
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                DynamicFieldValue,
                or_(
                    DynamicFieldValue.asset_id.in_(asset_ids),
                    DynamicFieldValue.employee_id.in_(employee_ids),
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                ImportSourceLink,
                or_(
                    ImportSourceLink.asset_id.in_(asset_ids),
                    ImportSourceLink.employee_id.in_(employee_ids),
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                FieldProvenance,
                or_(
                    FieldProvenance.asset_id.in_(asset_ids),
                    FieldProvenance.employee_id.in_(employee_ids),
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                CustomAttribute,
                or_(
                    CustomAttribute.asset_id.in_(asset_ids),
                    CustomAttribute.employee_id.in_(employee_ids),
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                HiddenProfileField,
                or_(
                    HiddenProfileField.asset_id.in_(asset_ids),
                    HiddenProfileField.employee_id.in_(employee_ids),
                ),
                include_aliases=True,
            ),
        ])
        execute_state.statement = execute_state.statement.options(*options)

    _listener_installed = True
