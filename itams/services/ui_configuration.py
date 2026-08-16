from __future__ import annotations

from flask import request

from ..extensions import db
from ..models import Setting, UIFieldConfiguration


FIELD_CATALOG = {
    "assets": [
        ("asset_tag", "Asset Tag", False), ("category_id", "Category", True),
        ("manufacturer_id", "Brand", False), ("model", "Model", False),
        ("serial_number", "Serial Number", False), ("condition", "Condition", False),
        ("hostname", "Hostname", False), ("ip_address", "IP Address", False),
        ("mac_address", "MAC Address", False), ("operating_system", "Operating System", False),
        ("os_version", "OS Version", False), ("processor", "Processor", False),
        ("ram", "RAM", False), ("storage", "Storage", False),
        ("mail_domain", "System Domain", False), ("monitor_make", "Monitor Make", False),
        ("monitor_model", "Monitor Model", False), ("monitor_serial_number", "Monitor Serial Number", False),
        ("vendor_id", "Vendor", False), ("purchase_date", "Purchase Date", False),
        ("purchase_cost", "Purchase Cost", False), ("warranty_expiry", "Warranty Expiry", False),
        ("invoice_number", "Invoice Number", False), ("location_id", "Location", True),
        ("department_id", "Asset Department", False), ("remarks", "Remarks", False),
    ],
    "employees": [
        ("employee_code", "Employee ID", False), ("name", "Employee Name", True),
        ("designation", "Designation", False), ("email", "Email", False),
        ("phone", "Phone", False), ("department_id", "Department", False),
        ("location_id", "Location", True), ("employment_status", "Employee Status", True),
    ],
    "infrastructure": [
        ("category_id", "Category", True), ("location_id", "Location", False),
        ("room", "Room", False), ("serial_number", "Serial Number", False),
        ("name", "Equipment Name", False), ("status", "Status", True),
        ("manufacturer_id", "Manufacturer", False), ("vendor_id", "Vendor", False),
        ("model", "Model", False), ("asset_tag", "Asset / Internal Tag", False),
        ("department_id", "Department", False), ("site", "Site", False),
        ("floor", "Floor", False), ("condition", "Condition", False),
        ("management_ip", "Management IP", False), ("subnet", "Subnet / CIDR", False),
        ("gateway", "Gateway", False), ("dns", "DNS", False),
        ("network_vlan", "VLAN", False), ("mac_address", "MAC Address", False),
        ("ip_assignment", "Address Assignment", False), ("purchase_date", "Purchase Date", False),
        ("installation_date", "Installation Date", False), ("warranty_expiry", "Warranty Expiry", False),
        ("amc_start_date", "AMC Start Date", False), ("amc_expiry", "AMC Expiry", False),
        ("purchase_cost", "Purchase Cost", False), ("invoice_number", "Invoice Number", False),
        ("remarks", "Remarks", False),
    ],
    "assignments": [
        ("asset_id", "Asset", True), ("employee_id", "Employee", True),
        ("assignment_date", "Assignment Date", False), ("condition", "Condition", False),
        ("accessories", "Accessories", False), ("expected_return_date", "Expected Return Date", False),
        ("reason", "Reason", False), ("remarks", "Remarks", False),
    ],
    "repairs": [
        ("asset_id", "Asset", True), ("vendor_id", "Vendor", False),
        ("issue_description", "Complaint / Issue", True), ("repair_cost", "Repair Cost", False),
        ("invoice_number", "Invoice Number", False), ("parts_changed", "Parts Changed", False),
        ("payment_type", "Warranty / Paid", False), ("condition_before", "Condition Before", False),
        ("condition_after", "Condition After", False), ("remarks", "Remarks", False),
    ],
}

DEFAULT_SETTINGS = {
    "ui.application_name": "IT Asset Management",
    "ui.login_heading": "Welcome to ITAMS",
    "ui.login_help": "Need access or a password reset? Contact the Super Admin.",
    "ui.primary_color": "#203f78",
    "ui.accent_color": "#76b82a",
}


def seed_ui_configuration():
    for module, fields in FIELD_CATALOG.items():
        for order, (code, label, protected) in enumerate(fields, start=1):
            row = UIFieldConfiguration.query.filter_by(module=module, field_code=code).first()
            if not row:
                db.session.add(UIFieldConfiguration(
                    module=module, field_code=code, default_label=label, label=label,
                    protected=protected, required=protected, sort_order=order * 10,
                ))
    for key, value in DEFAULT_SETTINGS.items():
        if not Setting.query.filter_by(key=key).first():
            db.session.add(Setting(key=key, value=value))
    db.session.commit()


def application_setting(key, default=""):
    row = Setting.query.filter_by(key=key).first()
    return row.value if row and row.value is not None else default


def current_ui_module():
    endpoint = request.endpoint or ""
    blueprint = request.blueprint or ""
    if blueprint == "masters" and "employee" in endpoint:
        return "employees"
    if blueprint == "requests":
        return "assignments"
    if blueprint == "operations":
        if "repair" in endpoint:
            return "repairs"
        return "assignments"
    return {"assets": "assets", "infrastructure": "infrastructure"}.get(blueprint, blueprint)


def field_configuration_payload():
    rows = UIFieldConfiguration.query.order_by(
        UIFieldConfiguration.module, UIFieldConfiguration.sort_order, UIFieldConfiguration.id
    ).all()
    return {
        module: {
            row.field_code: {
                "label": row.label,
                "visible": row.visible,
                "required": row.required,
                "order": row.sort_order,
                "placeholder": row.placeholder or "",
                "help": row.help_text or "",
            }
            for row in rows if row.module == module
        }
        for module in FIELD_CATALOG
    }
