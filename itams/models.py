from __future__ import annotations
from datetime import datetime, timezone
import json
from sqlalchemy import event, insert, select, update
import re
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

def utc_now():
    """Timezone-safe UTC persisted as a naive value for SQLite compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, nullable=False)

class Role(db.Model, TimestampMixin):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.Text)
    is_system_role = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.String(120))
    permissions = db.relationship("Permission",secondary="role_permissions",back_populates="roles")

class Permission(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), nullable=False, index=True)
    description = db.Column(db.Text)
    roles = db.relationship("Role",secondary="role_permissions",back_populates="permissions")

class RolePermission(db.Model):
    __tablename__ = "role_permissions"
    role_id = db.Column(db.Integer,db.ForeignKey("roles.id",ondelete="CASCADE"),primary_key=True)
    permission_id = db.Column(db.Integer,db.ForeignKey("permissions.id",ondelete="CASCADE"),primary_key=True)

class User(db.Model, TimestampMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    last_login_at = db.Column(db.DateTime)
    approval_level = db.Column(db.String(40), default="Location Admin", nullable=False)
    location_scope_enabled = db.Column(db.Boolean, default=False, nullable=False)
    active_session_token_hash = db.Column(db.String(64), index=True)
    active_session_started_at = db.Column(db.DateTime)
    active_session_last_seen_at = db.Column(db.DateTime)
    active_session_ip = db.Column(db.String(80))
    active_session_user_agent = db.Column(db.String(255))
    role = db.relationship("Role",backref="users")
    locations = db.relationship(
        "Location", secondary="user_locations", back_populates="users",
        order_by="Location.name",
    )
    def set_password(self, password: str): self.password_hash = generate_password_hash(password)
    def check_password(self, password: str) -> bool: return check_password_hash(self.password_hash, password)

class LoginHistory(db.Model):
    __tablename__ = "login_history"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    logged_in_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    ip_address = db.Column(db.String(80))
    user = db.relationship("User")

class MasterMixin(TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

class Department(db.Model, MasterMixin): __tablename__ = "departments"
class Location(db.Model, MasterMixin):
    __tablename__ = "locations"
    code = db.Column(db.String(40), unique=True, index=True)
    short_name = db.Column(db.String(80))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    country = db.Column(db.String(100))
    remarks = db.Column(db.Text)
    users = db.relationship(
        "User", secondary="user_locations", back_populates="locations",
    )

class UserLocation(db.Model):
    __tablename__ = "user_locations"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True)
    assigned_by = db.Column(db.String(120))
    assigned_at = db.Column(db.DateTime, default=utc_now, nullable=False)
class Manufacturer(db.Model, MasterMixin): __tablename__ = "manufacturers"
class Vendor(db.Model, MasterMixin):
    __tablename__ = "vendors"
    contact_person = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    services = db.Column(db.Text)
class AssetCategory(db.Model, MasterMixin): __tablename__ = "asset_categories"

class Employee(db.Model, TimestampMixin):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)
    employee_code = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    designation = db.Column(db.String(120))
    blocked = db.Column(db.Boolean, default=False, nullable=False, index=True)
    blocked_reason = db.Column(db.Text); blocked_date = db.Column(db.Date); blocked_by = db.Column(db.String(120)); unblocked_date = db.Column(db.Date)
    employment_status = db.Column(db.String(30), default="Active", nullable=False, index=True)
    it_clearance_status = db.Column(db.String(30), default="Not Required", nullable=False)
    manually_created = db.Column(db.Boolean, default=True, nullable=False)
    department = db.relationship("Department")
    location = db.relationship("Location")

class SystemCounter(db.Model):
    """Monotonic counters used for identifiers that must never be reused."""
    __tablename__ = "system_counters"
    name = db.Column(db.String(80), primary_key=True)
    next_value = db.Column(db.Integer, nullable=False)

@event.listens_for(Employee, "before_insert")
def assign_permanent_employee_code(_mapper, connection, target):
    """Allocate a unique employee reference when the business ID is not supplied."""
    if target.employee_code and target.employee_code.strip():
        target.employee_code = target.employee_code.strip()
        return
    while True:
        current = connection.execute(
            select(SystemCounter.next_value).where(SystemCounter.name == "employee_reference")
        ).scalar_one_or_none()
        if current is None:
            allocated = 1
            connection.execute(insert(SystemCounter).values(name="employee_reference", next_value=2))
        else:
            allocated = connection.execute(
                update(SystemCounter)
                .where(SystemCounter.name == "employee_reference")
                .values(next_value=SystemCounter.next_value + 1)
                .returning(SystemCounter.next_value)
            ).scalar_one() - 1
        candidate = f"ITAM-EMP-{allocated:06d}"
        exists = connection.execute(
            select(Employee.id).where(db.func.lower(Employee.employee_code) == candidate.lower())
        ).first()
        if not exists:
            target.employee_code = candidate
            return

class Asset(db.Model, TimestampMixin):
    __tablename__ = "assets"
    id = db.Column(db.Integer, primary_key=True)
    system_asset_reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    location_asset_reference = db.Column(db.String(40), unique=True, nullable=False, index=True)
    asset_tag = db.Column(db.String(60), unique=True, nullable=False, index=True)
    source_asset_tag = db.Column(db.String(120), index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("asset_categories.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    manufacturer_id = db.Column(db.Integer, db.ForeignKey("manufacturers.id"))
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"))
    username = db.Column(db.String(100)); ip_address = db.Column(db.String(50)); mac_address = db.Column(db.String(50))
    hostname = db.Column(db.String(100), index=True); mail_domain = db.Column(db.String(100)); model = db.Column(db.String(120))
    serial_number = db.Column(db.String(100), unique=True, index=True); source_serial_number = db.Column(db.String(120), index=True); operating_system = db.Column(db.String(100)); os_version = db.Column(db.String(80))
    processor = db.Column(db.String(120)); ram = db.Column(db.String(60)); storage = db.Column(db.String(80))
    monitor_make = db.Column(db.String(100)); monitor_model = db.Column(db.String(100)); monitor_serial_number = db.Column(db.String(100))
    office_mobile = db.Column(db.String(40)); mobile_number = db.Column(db.String(40))
    purchase_date = db.Column(db.Date); warranty_expiry = db.Column(db.Date)
    purchase_cost = db.Column(db.Float); invoice_number = db.Column(db.String(100), index=True)
    status = db.Column(db.String(30), default="Available", nullable=False, index=True)
    assigned_date = db.Column(db.Date); returned_date = db.Column(db.Date); condition = db.Column(db.String(50), default="Good")
    updated_by = db.Column(db.String(100)); remarks = db.Column(db.Text)
    manually_created = db.Column(db.Boolean, default=True, nullable=False)
    state_version = db.Column(db.Integer, default=1, nullable=False)
    category = db.relationship("AssetCategory"); location = db.relationship("Location"); employee = db.relationship("Employee")
    department = db.relationship("Department"); manufacturer = db.relationship("Manufacturer"); vendor = db.relationship("Vendor")
    @property
    def display_asset_tag(self):
        if self.source_asset_tag:
            return self.source_asset_tag
        if self.asset_tag and not self.asset_tag.startswith("SYS-ITAM-AST-"):
            return self.asset_tag
        return None
    @property
    def display_reference(self):
        return self.location_asset_reference or self.system_asset_reference

def asset_reference_prefix(location_code, location_name):
    """Use one consistent prefix for generated asset references."""
    return "ITAM"

@event.listens_for(Asset, "before_insert")
def assign_permanent_asset_reference(_mapper, connection, target):
    """Allocate a stable reference and preserve the legacy non-null tag constraint."""
    if not target.system_asset_reference:
        current = connection.execute(
            select(SystemCounter.next_value).where(SystemCounter.name == "asset_reference")
        ).scalar_one_or_none()
        if current is None:
            allocated = 1
            connection.execute(insert(SystemCounter).values(name="asset_reference", next_value=2))
        else:
            allocated = connection.execute(
                update(SystemCounter)
                .where(SystemCounter.name == "asset_reference")
                .values(next_value=SystemCounter.next_value + 1)
                .returning(SystemCounter.next_value)
            ).scalar_one() - 1
        target.system_asset_reference = f"ITAM-AST-{allocated:06d}"
    if not target.location_asset_reference:
        location=connection.execute(
            select(Location.code,Location.name).where(Location.id==target.location_id)
        ).first()
        prefix=asset_reference_prefix(location.code if location else None,location.name if location else None)
        counter_name=f"asset_location_reference::{prefix}"
        current=connection.execute(select(SystemCounter.next_value).where(SystemCounter.name==counter_name)).scalar_one_or_none()
        if current is None:
            allocated=1
            connection.execute(insert(SystemCounter).values(name=counter_name,next_value=2))
        else:
            allocated=connection.execute(
                update(SystemCounter).where(SystemCounter.name==counter_name)
                .values(next_value=SystemCounter.next_value+1).returning(SystemCounter.next_value)
            ).scalar_one()-1
        target.location_asset_reference=f"{prefix}-AST-{allocated:06d}"
    if not target.asset_tag:
        target.asset_tag = f"SYS-{target.system_asset_reference}"

class AssetQRIdentity(db.Model, TimestampMixin):
    """Revocable public token used by a QR scan; the asset reference never changes."""
    __tablename__ = "asset_qr_identities"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    token = db.Column(db.String(100), unique=True, nullable=False, index=True)
    created_by = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    invalidated_at = db.Column(db.DateTime)
    invalidated_by = db.Column(db.String(120))
    invalidation_reason = db.Column(db.Text)
    asset = db.relationship("Asset")

class AssetQRHistory(db.Model):
    """Immutable QR generation history; only active/archive state is updated."""
    __tablename__ = "asset_qr_history"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    identity_id = db.Column(db.Integer, db.ForeignKey("asset_qr_identities.id", ondelete="CASCADE"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(40), nullable=False)
    generated_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)
    generated_by = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    archived_at = db.Column(db.DateTime)
    asset = db.relationship("Asset")
    identity = db.relationship("AssetQRIdentity")
    __table_args__ = (
        db.UniqueConstraint("asset_id", "version", name="uq_asset_qr_history_version"),
    )

class InfrastructureCategory(db.Model, MasterMixin):
    __tablename__ = "infrastructure_categories"

class InfrastructureItem(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_items"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("infrastructure_categories.id"), nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    name = db.Column(db.String(160), index=True)
    manufacturer_id = db.Column(db.Integer, db.ForeignKey("manufacturers.id"), index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    model = db.Column(db.String(160), index=True)
    serial_number = db.Column(db.String(160), index=True)
    asset_tag = db.Column(db.String(100), index=True)
    building = db.Column(db.String(120), index=True)
    floor = db.Column(db.String(80), index=True)
    room = db.Column(db.String(120), index=True)
    site = db.Column(db.String(120), index=True)
    rack = db.Column(db.String(120), index=True)
    rack_unit = db.Column(db.String(40), index=True)
    location_node_id = db.Column(db.Integer, db.ForeignKey("infrastructure_locations.id"), index=True)
    status = db.Column(db.String(40), default="Planned", nullable=False, index=True)
    condition = db.Column(db.String(80))
    purchase_date = db.Column(db.Date)
    installation_date = db.Column(db.Date)
    warranty_expiry = db.Column(db.Date, index=True)
    amc_start_date = db.Column(db.Date)
    amc_expiry = db.Column(db.Date, index=True)
    purchase_cost = db.Column(db.Float)
    invoice_number = db.Column(db.String(120), index=True)
    ip_address = db.Column(db.String(80), index=True)
    mac_address = db.Column(db.String(80), index=True)
    management_ip = db.Column(db.String(80), index=True)
    subnet = db.Column(db.String(80))
    gateway = db.Column(db.String(80))
    dns = db.Column(db.String(255))
    network_vlan = db.Column(db.String(80), index=True)
    ip_assignment = db.Column(db.String(20))
    last_verified_date = db.Column(db.Date, index=True)
    verified_by = db.Column(db.String(120))
    verification_condition = db.Column(db.String(80))
    next_verification_date = db.Column(db.Date, index=True)
    specifications_json = db.Column(db.Text)
    remarks = db.Column(db.Text)
    updated_by = db.Column(db.String(120))
    state_version = db.Column(db.Integer, default=1, nullable=False)
    category = db.relationship("InfrastructureCategory")
    company_location = db.relationship("Location")
    manufacturer = db.relationship("Manufacturer")
    vendor = db.relationship("Vendor")
    department = db.relationship("Department")
    location_node = db.relationship("InfrastructureLocation", backref="items")
    @property
    def specifications(self):
        try:
            value = json.loads(self.specifications_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}
    @property
    def display_name(self):
        return self.name or (self.category.name if self.category else "Infrastructure Equipment")
    @property
    def effective_management_ip(self):
        return self.management_ip or self.ip_address
    @property
    def location_path(self):
        if self.location_node:
            return self.location_node.full_path
        values = [self.site, self.building, self.floor, self.department.name if self.department else None, self.room, self.rack, self.rack_unit]
        return " / ".join(str(value) for value in values if value) or "Not Available"

@event.listens_for(InfrastructureItem, "before_insert")
def assign_permanent_infrastructure_reference(_mapper, connection, target):
    if target.infrastructure_reference:
        return
    current = connection.execute(
        select(SystemCounter.next_value).where(SystemCounter.name == "infrastructure_reference")
    ).scalar_one_or_none()
    if current is None:
        allocated = 1
        connection.execute(insert(SystemCounter).values(name="infrastructure_reference", next_value=2))
    else:
        allocated = connection.execute(
            update(SystemCounter)
            .where(SystemCounter.name == "infrastructure_reference")
            .values(next_value=SystemCounter.next_value + 1)
            .returning(SystemCounter.next_value)
        ).scalar_one() - 1
    target.infrastructure_reference = f"ITAM-INF-{allocated:06d}"

class InfrastructureLocation(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_locations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    location_type = db.Column(db.String(30), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("infrastructure_locations.id"), index=True)
    company_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_by = db.Column(db.String(120))
    parent = db.relationship("InfrastructureLocation", remote_side=[id], backref=db.backref("children", lazy="dynamic"))
    company_location = db.relationship("Location")
    __table_args__ = (
        db.UniqueConstraint("parent_id", "location_type", "name", name="uq_infrastructure_location_sibling"),
    )
    @property
    def full_path(self):
        values, current, seen = [], self, set()
        while current and current.id not in seen:
            seen.add(current.id)
            values.append(current.name)
            current = current.parent
        return " / ".join(reversed(values))

class InfrastructureRelationship(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_relationships"
    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type = db.Column(db.String(80), nullable=False, index=True)
    connection_status = db.Column(db.String(40), default="Connected", nullable=False, index=True)
    remarks = db.Column(db.Text)
    created_by = db.Column(db.String(120))
    updated_by = db.Column(db.String(120))
    source = db.relationship("InfrastructureItem", foreign_keys=[source_id])
    target = db.relationship("InfrastructureItem", foreign_keys=[target_id])
    __table_args__ = (
        db.CheckConstraint("source_id <> target_id", name="ck_infrastructure_relationship_distinct"),
        db.UniqueConstraint("source_id", "target_id", "relationship_type", name="uq_infrastructure_relationship"),
    )

class InfrastructurePort(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_ports"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    port_number = db.Column(db.String(40), nullable=False)
    port_name = db.Column(db.String(120))
    connected_device_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="SET NULL"), index=True)
    connected_port = db.Column(db.String(80))
    status = db.Column(db.String(40), default="Available", nullable=False, index=True)
    speed = db.Column(db.String(40))
    poe = db.Column(db.Boolean)
    vlan = db.Column(db.String(80), index=True)
    remarks = db.Column(db.Text)
    infrastructure = db.relationship("InfrastructureItem", foreign_keys=[infrastructure_id])
    connected_device = db.relationship("InfrastructureItem", foreign_keys=[connected_device_id])
    __table_args__ = (
        db.UniqueConstraint("infrastructure_id", "port_number", name="uq_infrastructure_port_number"),
    )

class InfrastructureCable(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_cables"
    id = db.Column(db.Integer, primary_key=True)
    cable_id = db.Column(db.String(80), unique=True, nullable=False, index=True)
    cable_type = db.Column(db.String(40), nullable=False, index=True)
    length = db.Column(db.String(40))
    source_device_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="SET NULL"), index=True)
    destination_device_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="SET NULL"), index=True)
    source_port = db.Column(db.String(80))
    destination_port = db.Column(db.String(80))
    installation_date = db.Column(db.Date)
    status = db.Column(db.String(40), default="Installed", nullable=False, index=True)
    remarks = db.Column(db.Text)
    created_by = db.Column(db.String(120))
    source_device = db.relationship("InfrastructureItem", foreign_keys=[source_device_id])
    destination_device = db.relationship("InfrastructureItem", foreign_keys=[destination_device_id])
    __table_args__ = (
        db.CheckConstraint(
            "source_device_id IS NULL OR destination_device_id IS NULL OR source_device_id <> destination_device_id",
            name="ck_infrastructure_cable_distinct_devices",
        ),
    )

class InfrastructureIncident(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_incidents"
    id = db.Column(db.Integer, primary_key=True)
    incident_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    reported_by = db.Column(db.String(120), nullable=False)
    problem = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default="Medium", nullable=False, index=True)
    assigned_engineer = db.Column(db.String(120))
    root_cause = db.Column(db.Text)
    resolution = db.Column(db.Text)
    downtime_minutes = db.Column(db.Integer)
    status = db.Column(db.String(30), default="Open", nullable=False, index=True)
    closed_date = db.Column(db.Date)
    infrastructure = db.relationship("InfrastructureItem")

@event.listens_for(InfrastructureIncident, "before_insert")
def assign_infrastructure_incident_number(_mapper, connection, target):
    if target.incident_number:
        return
    current = connection.execute(
        select(SystemCounter.next_value).where(SystemCounter.name == "infrastructure_incident")
    ).scalar_one_or_none()
    if current is None:
        allocated = 1
        connection.execute(insert(SystemCounter).values(name="infrastructure_incident", next_value=2))
    else:
        allocated = connection.execute(
            update(SystemCounter)
            .where(SystemCounter.name == "infrastructure_incident")
            .values(next_value=SystemCounter.next_value + 1)
            .returning(SystemCounter.next_value)
        ).scalar_one() - 1
    target.incident_number = f"INF-INC-{allocated:06d}"

class InfrastructureVerification(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_verifications"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    verified_date = db.Column(db.Date, nullable=False, index=True)
    verified_by = db.Column(db.String(120), nullable=False)
    condition = db.Column(db.String(80))
    next_verification_date = db.Column(db.Date, index=True)
    remarks = db.Column(db.Text)
    infrastructure = db.relationship("InfrastructureItem")

class InfrastructureHistory(db.Model):
    __tablename__ = "infrastructure_history"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    occurred_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)
    reason = db.Column(db.Text)
    remarks = db.Column(db.Text)
    details_json = db.Column(db.Text)
    performed_by = db.Column(db.String(120), nullable=False)
    infrastructure = db.relationship("InfrastructureItem")
    @property
    def details(self):
        try:
            value = json.loads(self.details_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

class InfrastructureDocument(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_documents"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), unique=True, nullable=False)
    content_type = db.Column(db.String(120))
    size_bytes = db.Column(db.Integer, default=0, nullable=False)
    is_photo = db.Column(db.Boolean, default=False, nullable=False)
    photo_type = db.Column(db.String(40), index=True)
    uploaded_by = db.Column(db.String(120))
    infrastructure = db.relationship("InfrastructureItem")

class InfrastructureQRIdentity(db.Model, TimestampMixin):
    __tablename__ = "infrastructure_qr_identities"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    token = db.Column(db.String(100), unique=True, nullable=False, index=True)
    created_by = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    invalidated_at = db.Column(db.DateTime)
    invalidated_by = db.Column(db.String(120))
    invalidation_reason = db.Column(db.Text)
    infrastructure = db.relationship("InfrastructureItem")

class InfrastructureQRHistory(db.Model):
    __tablename__ = "infrastructure_qr_history"
    id = db.Column(db.Integer, primary_key=True)
    infrastructure_id = db.Column(db.Integer, db.ForeignKey("infrastructure_items.id", ondelete="CASCADE"), nullable=False, index=True)
    identity_id = db.Column(db.Integer, db.ForeignKey("infrastructure_qr_identities.id", ondelete="CASCADE"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(40), nullable=False)
    generated_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)
    generated_by = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    archived_at = db.Column(db.DateTime)
    infrastructure = db.relationship("InfrastructureItem")
    identity = db.relationship("InfrastructureQRIdentity")
    __table_args__ = (
        db.UniqueConstraint("infrastructure_id", "version", name="uq_infrastructure_qr_history_version"),
    )

class MovementLog(db.Model):
    __tablename__ = "movement_logs"
    id = db.Column(db.Integer, primary_key=True); movement_id = db.Column(db.String(30), unique=True, nullable=False)
    date = db.Column(db.DateTime, default=utc_now, nullable=False); asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False)
    action = db.Column(db.String(40), nullable=False); previous_user = db.Column(db.String(120)); new_user = db.Column(db.String(120))
    previous_department = db.Column(db.String(120)); new_department = db.Column(db.String(120)); previous_location = db.Column(db.String(120)); new_location = db.Column(db.String(120))
    updated_by = db.Column(db.String(100)); remarks = db.Column(db.Text)
    asset = db.relationship("Asset")

class RepairLog(db.Model, TimestampMixin):
    __tablename__ = "repair_logs"
    id = db.Column(db.Integer, primary_key=True); asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id")); issue_description = db.Column(db.Text, nullable=False)
    repair_cost = db.Column(db.Float, default=0); repair_date = db.Column(db.Date, nullable=False); received_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="Sent", nullable=False); remarks = db.Column(db.Text)
    invoice_number = db.Column(db.String(100)); parts_changed = db.Column(db.Text); payment_type = db.Column(db.String(30)); condition_before = db.Column(db.String(50)); condition_after = db.Column(db.String(50))
    asset = db.relationship("Asset"); vendor = db.relationship("Vendor")

class Setting(db.Model):
    __tablename__ = "settings"; id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False); value = db.Column(db.Text)
class AuditLog(db.Model):
    __tablename__ = "audit_logs"; id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=utc_now, nullable=False); user = db.Column(db.String(100)); entity = db.Column(db.String(80)); entity_id = db.Column(db.String(80)); action = db.Column(db.String(40)); old_value = db.Column(db.Text); new_value = db.Column(db.Text)
    reason = db.Column(db.Text)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    location = db.relationship("Location")
class Backup(db.Model):
    __tablename__ = "backups"; id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False); created_at = db.Column(db.DateTime, default=utc_now, nullable=False); size_bytes = db.Column(db.Integer, default=0, nullable=False); verified = db.Column(db.Boolean, default=False, nullable=False); notes = db.Column(db.String(255))

class ScrapLog(db.Model, TimestampMixin):
    __tablename__ = "scrap_logs"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False, unique=True)
    scrap_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    disposal_method = db.Column(db.String(120), nullable=False)
    approved_by = db.Column(db.String(120), nullable=False)
    remarks = db.Column(db.Text)
    asset = db.relationship("Asset")

class ApprovalRequest(db.Model, TimestampMixin):
    """Disabled-by-default structure for a future approval workflow (ITAMS-021)."""
    __tablename__ = "approval_requests"
    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(80))
    requested_by = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    payload = db.Column(db.Text)
    status = db.Column(db.String(30), default="Pending", nullable=False, index=True)
    reviewed_by = db.Column(db.String(100))
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.Text)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    target_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    location = db.relationship("Location", foreign_keys=[location_id])
    target_location = db.relationship("Location", foreign_keys=[target_location_id])

class AssetAssignment(db.Model, TimestampMixin):
    __tablename__ = "asset_assignments"
    id = db.Column(db.Integer, primary_key=True); asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False, index=True); employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    assigned_at = db.Column(db.DateTime, nullable=False, default=utc_now); returned_at = db.Column(db.DateTime); expected_return_date = db.Column(db.Date)
    condition_out = db.Column(db.String(50)); condition_in = db.Column(db.String(50)); accessories = db.Column(db.Text); accessories_returned = db.Column(db.Text); remarks = db.Column(db.Text); active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    asset = db.relationship("Asset"); employee = db.relationship("Employee")

class AssetLifecycle(db.Model):
    __tablename__ = "asset_lifecycle"
    id = db.Column(db.Integer, primary_key=True); event_key = db.Column(db.String(80), unique=True, nullable=False, index=True); occurred_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True); asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False, index=True)
    from_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id")); to_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id")); location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))
    condition_before = db.Column(db.String(50)); condition_after = db.Column(db.String(50)); reason = db.Column(db.Text); remarks = db.Column(db.Text); performed_by = db.Column(db.String(120))
    asset = db.relationship("Asset"); from_employee = db.relationship("Employee",foreign_keys=[from_employee_id]); to_employee = db.relationship("Employee",foreign_keys=[to_employee_id]); location = db.relationship("Location")

class AssetBlock(db.Model, TimestampMixin):
    __tablename__ = "asset_blocks"
    id = db.Column(db.Integer, primary_key=True); asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False, index=True); reason = db.Column(db.Text, nullable=False); blocked_date = db.Column(db.Date, nullable=False); expected_unblock_date = db.Column(db.Date); remarks = db.Column(db.Text); blocked_by = db.Column(db.String(120)); unblocked_date = db.Column(db.Date); active = db.Column(db.Boolean, default=True, nullable=False, index=True); previous_status = db.Column(db.String(30))
    asset = db.relationship("Asset")

class AssetRequest(db.Model, TimestampMixin):
    __tablename__ = "asset_requests"
    id = db.Column(db.Integer, primary_key=True)
    requested_for_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("asset_categories.id"), nullable=False)
    request_reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default="HR Raised", nullable=False, index=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), index=True)
    processed_by_id = db.Column(db.Integer, db.ForeignKey("users.id")); processed_at = db.Column(db.DateTime); admin_remarks = db.Column(db.Text)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id")); decided_at = db.Column(db.DateTime); decision_remarks = db.Column(db.Text)
    assignment_date = db.Column(db.Date); expected_return_date = db.Column(db.Date); condition = db.Column(db.String(50)); accessories = db.Column(db.Text)
    state_version = db.Column(db.Integer, default=1, nullable=False)
    location_validated = db.Column(db.Boolean, default=False, nullable=False)
    requested_for = db.relationship("Employee"); category = db.relationship("AssetCategory"); asset = db.relationship("Asset")
    requested_by = db.relationship("User",foreign_keys=[requested_by_id]); processed_by = db.relationship("User",foreign_keys=[processed_by_id]); approved_by = db.relationship("User",foreign_keys=[approved_by_id])

class ImportBatch(db.Model, TimestampMixin):
    __tablename__ = "import_batches"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255))
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(40), default="Uploaded", nullable=False, index=True)
    duplicate_policy = db.Column(db.String(30), default="skip", nullable=False)
    sheet_count = db.Column(db.Integer, default=0, nullable=False)
    total_rows = db.Column(db.Integer, default=0, nullable=False)
    valid_rows = db.Column(db.Integer, default=0, nullable=False)
    invalid_rows = db.Column(db.Integer, default=0, nullable=False)
    duplicate_rows = db.Column(db.Integer, default=0, nullable=False)
    inserted_rows = db.Column(db.Integer, default=0, nullable=False)
    updated_rows = db.Column(db.Integer, default=0, nullable=False)
    skipped_rows = db.Column(db.Integer, default=0, nullable=False)
    error_count = db.Column(db.Integer, default=0, nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    committed_at = db.Column(db.DateTime)
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

class ImportSheet(db.Model):
    __tablename__ = "import_sheets"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    row_count = db.Column(db.Integer, default=0, nullable=False)
    columns_json = db.Column(db.Text)
    batch = db.relationship("ImportBatch", backref=db.backref("sheets", cascade="all, delete-orphan"))

class ImportStagingRow(db.Model):
    __tablename__ = "import_staging_rows"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    sheet_id = db.Column(db.Integer, db.ForeignKey("import_sheets.id"), nullable=False, index=True)
    row_number = db.Column(db.Integer, nullable=False)
    original_json = db.Column(db.Text, nullable=False)
    mapped_json = db.Column(db.Text, nullable=False)
    dynamic_json = db.Column(db.Text)
    validation_status = db.Column(db.String(30), nullable=False, index=True)
    duplicate_status = db.Column(db.String(50))
    proposed_action = db.Column(db.String(30), default="insert", nullable=False)
    error_messages = db.Column(db.Text)
    mapping_confidence = db.Column(db.Float, default=0)
    live_asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"))
    live_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    batch = db.relationship("ImportBatch", backref=db.backref("rows", cascade="all, delete-orphan"))
    sheet = db.relationship("ImportSheet")
    @property
    def mapped_values(self):
        try:return json.loads(self.mapped_json or "{}")
        except (TypeError,ValueError):return {}

class ImportColumnMapping(db.Model, TimestampMixin):
    __tablename__ = "import_column_mappings"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), index=True)
    source_signature = db.Column(db.String(255), nullable=False, index=True)
    source_column = db.Column(db.String(255), nullable=False)
    target_field = db.Column(db.String(120))
    action = db.Column(db.String(30), default="map", nullable=False)
    confidence = db.Column(db.Float, default=0)
    reusable = db.Column(db.Boolean, default=False, nullable=False)

class ImportValidationError(db.Model):
    __tablename__ = "import_validation_errors"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    staging_row_id = db.Column(db.Integer, db.ForeignKey("import_staging_rows.id"), index=True)
    field_name = db.Column(db.String(120))
    severity = db.Column(db.String(20), default="error", nullable=False)
    message = db.Column(db.Text, nullable=False)

class ImportCommitLog(db.Model):
    __tablename__ = "import_commit_logs"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    committed_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    committed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    inserted_rows = db.Column(db.Integer, default=0)
    updated_rows = db.Column(db.Integer, default=0)
    skipped_rows = db.Column(db.Integer, default=0)
    status = db.Column(db.String(40), nullable=False)
    details = db.Column(db.Text)

class DynamicField(db.Model, TimestampMixin):
    __tablename__ = "dynamic_fields"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    normalized_name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    entity_type = db.Column(db.String(30), default="Asset", nullable=False)
    data_type = db.Column(db.String(30), default="text", nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

class DynamicFieldValue(db.Model, TimestampMixin):
    __tablename__ = "dynamic_field_values"
    id = db.Column(db.Integer, primary_key=True)
    dynamic_field_id = db.Column(db.Integer, db.ForeignKey("dynamic_fields.id"), nullable=False, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), index=True)
    value = db.Column(db.Text)
    import_batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), index=True)
    source_sheet = db.Column(db.String(255))
    field = db.relationship("DynamicField")

class ImportSourceLink(db.Model, TimestampMixin):
    __tablename__ = "import_source_links"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    sheet_name = db.Column(db.String(255), nullable=False)
    source_row_number = db.Column(db.Integer)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), index=True)
    exclusive_owner = db.Column(db.Boolean, default=True, nullable=False)
    detached = db.Column(db.Boolean, default=False, nullable=False)

class FieldProvenance(db.Model, TimestampMixin):
    __tablename__ = "field_provenance"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), index=True)
    field_name = db.Column(db.String(120), nullable=False)
    field_value = db.Column(db.Text)
    source_sheet = db.Column(db.String(255))
    source_row_number = db.Column(db.Integer)
    manually_overridden = db.Column(db.Boolean, default=False, nullable=False)

class CustomAttribute(db.Model, TimestampMixin):
    """A user-defined field attached to exactly one asset or employee."""
    __tablename__ = "custom_attributes"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    name = db.Column(db.String(120), nullable=False)
    value = db.Column(db.Text)
    created_by = db.Column(db.String(120))
    asset = db.relationship("Asset")
    employee = db.relationship("Employee")
    __table_args__ = (
        db.CheckConstraint(
            "(asset_id IS NOT NULL AND employee_id IS NULL) OR "
            "(asset_id IS NULL AND employee_id IS NOT NULL)",
            name="ck_custom_attribute_one_owner",
        ),
        db.UniqueConstraint("asset_id", "name", name="uq_asset_custom_attribute"),
        db.UniqueConstraint("employee_id", "name", name="uq_employee_custom_attribute"),
    )

class AssetDocument(db.Model, TimestampMixin):
    __tablename__ = "asset_documents"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), unique=True, nullable=False)
    content_type = db.Column(db.String(120))
    size_bytes = db.Column(db.Integer, default=0, nullable=False)
    uploaded_by = db.Column(db.String(120))
    asset = db.relationship("Asset")

class HiddenProfileField(db.Model, TimestampMixin):
    """A standard field intentionally removed from one profile, with its old value retained."""
    __tablename__ = "hidden_profile_fields"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    field_code = db.Column(db.String(80), nullable=False)
    field_label = db.Column(db.String(120), nullable=False)
    removed_value = db.Column(db.Text)
    reason = db.Column(db.Text)
    removed_by = db.Column(db.String(120))
    asset = db.relationship("Asset")
    employee = db.relationship("Employee")
    __table_args__ = (
        db.CheckConstraint(
            "(asset_id IS NOT NULL AND employee_id IS NULL) OR "
            "(asset_id IS NULL AND employee_id IS NOT NULL)",
            name="ck_hidden_field_one_owner",
        ),
        db.UniqueConstraint("asset_id", "field_code", name="uq_asset_hidden_field"),
        db.UniqueConstraint("employee_id", "field_code", name="uq_employee_hidden_field"),
    )

class AssetSpecificationChange(db.Model, TimestampMixin):
    __tablename__ = "asset_specification_changes"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    field_code = db.Column(db.String(80), nullable=False)
    field_label = db.Column(db.String(120), nullable=False)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    disposition = db.Column(db.Text, nullable=False)
    remarks = db.Column(db.Text)
    performed_by = db.Column(db.String(120))
    asset = db.relationship("Asset")


class UIFieldConfiguration(db.Model, TimestampMixin):
    """Safe, database-backed presentation rules for existing application fields."""
    __tablename__ = "ui_field_configurations"
    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(40), nullable=False, index=True)
    field_code = db.Column(db.String(100), nullable=False)
    default_label = db.Column(db.String(160), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    visible = db.Column(db.Boolean, default=True, nullable=False)
    required = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=100, nullable=False)
    placeholder = db.Column(db.String(255))
    help_text = db.Column(db.Text)
    protected = db.Column(db.Boolean, default=False, nullable=False)
    updated_by = db.Column(db.String(120))
    __table_args__ = (
        db.UniqueConstraint("module", "field_code", name="uq_ui_field_module_code"),
    )


# Invoice tables remain in the shared schema for compatibility with the reused
# data model. This public version does not expose an invoice dashboard until
# that optional module has a complete, independently verified route layer.
class InvoiceBillCategory(db.Model, TimestampMixin):
    __tablename__ = "invoice_bill_categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class InvoiceExpenditureType(db.Model, TimestampMixin):
    __tablename__ = "invoice_expenditure_types"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class InvoiceRecord(db.Model):
    __tablename__ = "invoice_records"
    id = db.Column(db.Integer, primary_key=True)
    invoice_reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False, index=True)
    bill_category_id = db.Column(db.Integer, db.ForeignKey("invoice_bill_categories.id"), nullable=False)
    expenditure_type_id = db.Column(db.Integer, db.ForeignKey("invoice_expenditure_types.id"), nullable=False)
    utilized_for = db.Column(db.String(255), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), nullable=False)
    account_connection_number = db.Column(db.String(120))
    frequency = db.Column(db.String(30), nullable=False)
    invoice_number = db.Column(db.String(120), nullable=False, index=True)
    invoice_date = db.Column(db.Date, nullable=False, index=True)
    po_number = db.Column(db.String(120)); po_date = db.Column(db.Date)
    grn_number = db.Column(db.String(120)); grn_date = db.Column(db.Date)
    invoice_amount = db.Column(db.Numeric(14, 2), nullable=False)
    invoice_document_path = db.Column(db.String(255)); it_remarks = db.Column(db.Text)
    entered_by = db.Column(db.String(120), nullable=False)
    entered_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_by = db.Column(db.String(120)); updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    approved_amount = db.Column(db.Numeric(14, 2))
    management_approval_status = db.Column(db.String(30), default="Pending", nullable=False, index=True)
    management_approval_date = db.Column(db.Date); management_remarks = db.Column(db.Text); approved_by = db.Column(db.String(120))
    tds_amount = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    paid_amount = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    utr_number = db.Column(db.String(120)); paid_date = db.Column(db.Date)
    payment_status = db.Column(db.String(30), default="Payment Pending", nullable=False, index=True)
    accounts_remarks = db.Column(db.Text); payment_updated_by = db.Column(db.String(120))
    overall_status = db.Column(db.String(40), default="Pending Management Approval", nullable=False, index=True)
    cancelled_at = db.Column(db.DateTime); cancelled_by = db.Column(db.String(120)); cancellation_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    location = db.relationship("Location"); bill_category = db.relationship("InvoiceBillCategory")
    expenditure_type = db.relationship("InvoiceExpenditureType"); vendor = db.relationship("Vendor")
    documents = db.relationship("InvoiceDocument", back_populates="invoice", cascade="all, delete-orphan")

    @property
    def outstanding_amount(self):
        approved = self.approved_amount or 0
        return approved - (self.tds_amount or 0) - (self.paid_amount or 0)


class InvoiceDocument(db.Model, TimestampMixin):
    __tablename__ = "invoice_documents"
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice_records.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), unique=True, nullable=False)
    content_type = db.Column(db.String(100)); size_bytes = db.Column(db.Integer, default=0, nullable=False)
    uploaded_by = db.Column(db.String(120)); primary_document = db.Column(db.Boolean, default=False, nullable=False)
    invoice = db.relationship("InvoiceRecord", back_populates="documents")


class InfrastructureSpecificationField(db.Model, TimestampMixin):
    """Frontend-managed optional specification schema for an infrastructure category."""
    __tablename__ = "infrastructure_specification_fields"
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("infrastructure_categories.id", ondelete="CASCADE"), nullable=False, index=True)
    field_code = db.Column(db.String(100), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    sort_order = db.Column(db.Integer, default=100, nullable=False)
    created_by = db.Column(db.String(120)); updated_by = db.Column(db.String(120))
    category = db.relationship("InfrastructureCategory")
    __table_args__ = (
        db.UniqueConstraint("category_id", "field_code", name="uq_infrastructure_spec_category_code"),
    )
