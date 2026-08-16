from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import secrets

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models import (
    InfrastructureCategory, InfrastructureQRHistory, InfrastructureQRIdentity,
    InfrastructureSpecificationField, Manufacturer,
)
from .audit import audit, current_user_name


INFRASTRUCTURE_CATEGORIES = [
    "Routers", "Switches", "Firewalls", "Wireless Access Points", "Cisco Access Points", "Modems",
    "EPABX", "VoIP Phones", "Biometric Devices", "CCTV Cameras", "NVR/DVR",
    "Printers", "Scanners", "Copiers", "Projectors", "TVs/Digital Displays",
    "Fibre Cables", "LAN Cables", "Patch Panels", "Network Racks", "UPS",
    "Other infrastructure equipment",
]

NETWORK_FIELDS = [
    ("port_count", "Port Count"), ("wan_type", "WAN Type"), ("throughput", "Throughput"),
    ("wifi_standard", "Wi-Fi Standard"), ("frequency_band", "Frequency Band"),
    ("vlan_support", "VLAN Support"), ("poe_support", "PoE Support"),
    ("firmware_version", "Firmware Version"), ("management_url", "Management URL"),
]
CISCO_ACCESS_POINT_FIELDS = [
    ("ap_hostname", "AP Hostname"),
    ("controller_name", "Wireless Controller"),
    ("controller_ip", "Controller IP / Hostname"),
    ("ap_mode", "AP Mode"),
    ("wifi_standard", "Wi-Fi Standard"),
    ("frequency_band", "Frequency Band"),
    ("radio_count", "Radio Count"),
    ("ssid", "SSID / WLAN"),
    ("poe_support", "PoE"),
    ("firmware_version", "Firmware Version"),
    ("switch_name", "Connected Switch"),
    ("switch_port", "Switch Port"),
    ("coverage_area", "Coverage Area"),
]
TELEPHONY_FIELDS = [
    ("extension_number", "Extension Number"), ("extension_capacity", "Extension Capacity"),
    ("sip_provider", "SIP Provider"), ("line_count", "Line Count"),
    ("firmware_version", "Firmware Version"),
]
SURVEILLANCE_FIELDS = [
    ("device_type", "Device Type"), ("resolution", "Resolution"), ("lens", "Lens"),
    ("night_vision", "Night Vision"), ("channel_count", "Channel Count"),
    ("storage_capacity", "Storage Capacity"), ("camera_capacity", "Camera Capacity"),
    ("poe_support", "PoE Support"), ("firmware_version", "Firmware Version"),
]
CCTV_FIELDS = [("network_parameter", "Network Parameter"), *SURVEILLANCE_FIELDS]
PRINT_FIELDS = [
    ("technology", "Technology"), ("color_mode", "Colour Mode"), ("paper_size", "Paper Size"),
    ("duplex", "Duplex"), ("speed_ppm", "Speed (PPM)"), ("scan_resolution", "Scan Resolution"),
    ("network_support", "Network Support"), ("meter_reading", "Meter Reading"),
]
DISPLAY_FIELDS = [
    ("screen_size", "Screen Size"), ("resolution", "Resolution"), ("brightness", "Brightness"),
    ("input_ports", "Input Ports"), ("mount_type", "Mount Type"), ("lamp_hours", "Lamp Hours"),
]
CABLE_FIELDS = [
    ("cable_type", "Cable Type"), ("length", "Length"), ("core_count", "Core Count"),
    ("from_point", "From Point"), ("to_point", "To Point"),
    ("termination_type", "Termination Type"), ("test_result", "Test Result"),
]
RACK_FIELDS = [
    ("rack_units", "Rack Units"), ("port_count", "Port Count"), ("mount_type", "Mount Type"),
    ("earthing", "Earthing"), ("cooling", "Cooling"), ("patching_details", "Patching Details"),
]
UPS_FIELDS = [
    ("capacity_kva", "Capacity (kVA)"), ("battery_count", "Battery Count"),
    ("battery_rating", "Battery Rating"), ("backup_time", "Backup Time"),
    ("input_voltage", "Input Voltage"), ("output_voltage", "Output Voltage"),
]
BIOMETRIC_FIELDS = [
    ("biometric_method", "Biometric Method"), ("user_capacity", "User Capacity"),
    ("attendance_server", "Attendance Server"), ("firmware_version", "Firmware Version"),
]

SPECIFICATION_FIELDS = {
    "Routers": NETWORK_FIELDS, "Switches": NETWORK_FIELDS, "Firewalls": NETWORK_FIELDS,
    "Wireless Access Points": NETWORK_FIELDS, "Modems": NETWORK_FIELDS,
    "Cisco Access Points": CISCO_ACCESS_POINT_FIELDS,
    "EPABX": TELEPHONY_FIELDS, "VoIP Phones": TELEPHONY_FIELDS,
    "Biometric Devices": BIOMETRIC_FIELDS,
    "CCTV Cameras": CCTV_FIELDS, "NVR/DVR": SURVEILLANCE_FIELDS,
    "Printers": PRINT_FIELDS, "Scanners": PRINT_FIELDS, "Copiers": PRINT_FIELDS,
    "Projectors": DISPLAY_FIELDS, "TVs/Digital Displays": DISPLAY_FIELDS,
    "Fibre Cables": CABLE_FIELDS, "LAN Cables": CABLE_FIELDS,
    "Patch Panels": RACK_FIELDS, "Network Racks": RACK_FIELDS,
    "UPS": UPS_FIELDS,
    "Other infrastructure equipment": [("specification_notes", "Specification Notes")],
}

EVENT_TYPES = [
    "Installation", "Maintenance", "Firmware Update", "Relocation", "Repair",
    "Warranty", "AMC", "Decommissioning",
]


def seed_infrastructure_categories():
    for name in INFRASTRUCTURE_CATEGORIES:
        if not InfrastructureCategory.query.filter_by(name=name).first():
            db.session.add(InfrastructureCategory(name=name, active=True))
    # Cisco is a manufacturer, not a generic device specification. Keep it as
    # an additive master value so Cisco access points can be entered cleanly.
    if not Manufacturer.query.filter(func.lower(Manufacturer.name) == "cisco").first():
        db.session.add(Manufacturer(name="Cisco", active=True))
    db.session.flush()
    for category_name, fields in SPECIFICATION_FIELDS.items():
        category = InfrastructureCategory.query.filter_by(name=category_name).first()
        if not category:
            continue
        for order, (code, label) in enumerate(fields, start=1):
            if not InfrastructureSpecificationField.query.filter_by(category_id=category.id, field_code=code).first():
                db.session.add(InfrastructureSpecificationField(
                    category_id=category.id, field_code=code, label=label,
                    active=True, sort_order=order * 10, created_by="System",
                ))
    db.session.commit()


def specification_fields(category_name: str):
    category = InfrastructureCategory.query.filter_by(name=category_name).first()
    if category:
        rows = InfrastructureSpecificationField.query.filter_by(category_id=category.id, active=True).order_by(
            InfrastructureSpecificationField.sort_order, InfrastructureSpecificationField.id
        ).all()
        if rows:
            return [(row.field_code, row.label) for row in rows]
    return SPECIFICATION_FIELDS.get(category_name, SPECIFICATION_FIELDS["Other infrastructure equipment"])


def all_specification_fields():
    return {category.name: specification_fields(category.name) for category in InfrastructureCategory.query.filter_by(active=True).all()}


def specifications_from_form(category_name: str, form):
    return {
        code: form.get(f"spec_{code}", "").strip()
        for code, _label in specification_fields(category_name)
        if form.get(f"spec_{code}", "").strip()
    }


def infrastructure_qr_path(item):
    root = Path(current_app.config["QR_CODE_DIR"]).resolve() / "infrastructure"
    root.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"ITAM-INF-\d{6,}", item.infrastructure_reference or ""):
        raise RuntimeError("Infrastructure record does not have a valid permanent reference.")
    return root / f"{item.infrastructure_reference}.png"


def _base_url():
    value = str(current_app.config.get("ASSET_PROFILE_BASE_URL", "")).strip().rstrip("/")
    if not value:
        raise RuntimeError("Infrastructure QR base URL is not configured.")
    return value


def infrastructure_profile_url(item):
    return f"{_base_url()}/infrastructure/profile/{item.infrastructure_reference}"


def infrastructure_qr_url(identity):
    return f"{_base_url()}/q/{identity.token}"


def active_identity(item_id):
    return InfrastructureQRIdentity.query.filter_by(infrastructure_id=item_id, active=True).first()


def active_qr_history(item_id):
    return InfrastructureQRHistory.query.filter_by(infrastructure_id=item_id, active=True).first()


def qr_history(item_id):
    return InfrastructureQRHistory.query.filter_by(infrastructure_id=item_id).order_by(InfrastructureQRHistory.version.desc()).all()


def _next_version(item_id):
    return (db.session.query(func.max(InfrastructureQRHistory.version)).filter_by(infrastructure_id=item_id).scalar() or 0) + 1


def _write_qr(item, identity):
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:
        raise RuntimeError("QR support is unavailable.") from exc
    path = infrastructure_qr_path(item)
    destination = infrastructure_qr_url(identity)
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    code.add_data(destination)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    temporary = path.with_suffix(".tmp.png")
    image.save(temporary, format="PNG")
    temporary.replace(path)
    return path, destination


def _new_identity(item, actor):
    identity = InfrastructureQRIdentity(
        infrastructure_id=item.id, token=secrets.token_urlsafe(32),
        created_by=actor, active=True,
    )
    db.session.add(identity)
    db.session.flush()
    return identity


def _archive_history(item_id):
    row = active_qr_history(item_id)
    if row:
        row.active = False
        row.archived_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return row


def _add_qr_history(item, identity, actor, reason, action):
    row = InfrastructureQRHistory(
        infrastructure_id=item.id, identity_id=identity.id,
        version=_next_version(item.id), action=action, generated_by=actor,
        reason=reason, active=True,
    )
    db.session.add(row)
    db.session.flush()
    return row


def ensure_infrastructure_qr(item, actor=None, reason="Automatic infrastructure QR generation"):
    actor = (actor or current_user_name() or "System").strip()
    identity = active_identity(item.id)
    history = active_qr_history(item.id)
    created = False
    if not identity:
        identity = _new_identity(item, actor)
        history = _add_qr_history(item, identity, actor, reason, "Generated")
        audit("Infrastructure", item.id, "QR Generated", new={
            "reference": item.infrastructure_reference, "version": history.version,
            "identity_id": identity.id,
        }, reason=reason)
        created = True
    path = infrastructure_qr_path(item)
    if created or not path.exists():
        if not created:
            _archive_history(item.id)
            history = _add_qr_history(item, identity, actor, reason, "Image Restored")
            audit("Infrastructure", item.id, "QR Image Restored", new={"version": history.version}, reason=reason)
        path, destination = _write_qr(item, identity)
        return path, destination, True
    return path, infrastructure_qr_url(identity), False


def regenerate_infrastructure_qr(item, mode, reason, actor=None):
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Regeneration reason is required.")
    if mode not in ("image", "identity"):
        raise ValueError("Choose a valid regeneration mode.")
    actor = (actor or current_user_name() or "System").strip()
    identity = active_identity(item.id)
    if not identity:
        ensure_infrastructure_qr(item, actor=actor)
        identity = active_identity(item.id)
    previous = _archive_history(item.id)
    old_token, old_id = identity.token, identity.id
    if mode == "identity":
        identity.active = False
        identity.invalidated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        identity.invalidated_by = actor
        identity.invalidation_reason = reason
        identity = _new_identity(item, actor)
    action = "Identity Regenerated" if mode == "identity" else "Image Regenerated"
    history = _add_qr_history(item, identity, actor, reason, action)
    path, destination = _write_qr(item, identity)
    audit("Infrastructure", item.id, f"QR {action}", old={
        "version": previous.version if previous else None, "identity_id": old_id, "token": old_token,
    }, new={
        "version": history.version, "identity_id": identity.id, "token": identity.token,
        "reference": item.infrastructure_reference,
    }, reason=reason)
    return path, destination, history, old_token


def qr_metadata(item):
    identity, history = active_identity(item.id), active_qr_history(item.id)
    try:
        exists = infrastructure_qr_path(item).exists()
    except RuntimeError:
        exists = False
    return {
        "status": "Active" if identity and history and exists else "Missing",
        "version": history.version if history else None,
        "generated_at": identity.created_at if identity else None,
        "generated_by": identity.created_by if identity else None,
        "last_regenerated_at": history.generated_at if history and history.action != "Generated" else None,
        "last_regenerated_by": history.generated_by if history and history.action != "Generated" else None,
        "reason": history.reason if history else None,
    }


def resolve_infrastructure_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", token or ""):
        return None
    return InfrastructureQRIdentity.query.filter_by(token=token, active=True).first()


def serialize_details(values):
    return json.dumps({key: value for key, value in values.items() if value not in (None, "")}, default=str)
