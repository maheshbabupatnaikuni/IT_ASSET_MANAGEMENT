"""Complete, user-facing serializers for report and archive output.

The serializers intentionally derive standard fields from SQLAlchemy columns so
new additive fields automatically become exportable. Foreign-key identifiers
are replaced with readable master values, and custom/imported fields are added
as named columns.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from ..extensions import db
from ..models import (
    Asset, AssetDocument, CustomAttribute, DynamicFieldValue, Employee,
    InfrastructureDocument, InfrastructureItem,
)

LOCAL_TZ = ZoneInfo("Asia/Kolkata")


def display_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(LOCAL_TZ).strftime("%d %b %Y %I:%M:%S %p")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return value


def label_for(column_name):
    labels = {
        "created_at": "Created Date and Time", "updated_at": "Updated Date and Time",
        "system_asset_reference": "Permanent System Reference",
        "location_asset_reference": "Location Asset Reference",
        "manually_created": "Manually Created", "state_version": "Record Version",
        "ip_address": "IP Address", "mac_address": "MAC Address",
        "mail_domain": "System Domain", "operating_system": "Operating System",
        "os_version": "OS Version", "ram": "RAM", "dns": "DNS",
        "network_vlan": "VLAN", "amc_start_date": "AMC Start Date",
        "amc_expiry": "AMC Expiry", "qr": "QR",
    }
    return labels.get(column_name, column_name.replace("_", " ").title())


def model_columns(record, *, exclude=(), rename=None):
    rename = rename or {}
    result = OrderedDict()
    for column in record.__table__.columns:
        if column.name in exclude:
            continue
        result[rename.get(column.name, label_for(column.name))] = display_value(getattr(record, column.name))
    return result


def _extra_fields(*, asset_id=None, employee_id=None):
    result = OrderedDict()
    dynamic = DynamicFieldValue.query.filter_by(asset_id=asset_id).all() if asset_id else DynamicFieldValue.query.filter_by(employee_id=employee_id).all()
    for item in dynamic:
        if item.field and item.value not in (None, ""):
            result[f"Imported Field - {item.field.name}"] = item.value
    custom = CustomAttribute.query.filter_by(asset_id=asset_id).all() if asset_id else CustomAttribute.query.filter_by(employee_id=employee_id).all()
    for item in custom:
        if item.value not in (None, ""):
            result[f"Custom Field - {item.name}"] = item.value
    return result


def asset_row(asset: Asset):
    row = OrderedDict((
        ("Location Asset Reference", asset.display_reference),
        ("Permanent System Reference", asset.system_asset_reference),
        ("Asset Tag", asset.display_asset_tag or asset.asset_tag),
        ("Category", asset.category.name if asset.category else ""),
        ("Brand / Manufacturer", asset.manufacturer.name if asset.manufacturer else ""),
        ("Model", asset.model or ""),
        ("Serial Number", asset.source_serial_number or asset.serial_number or ""),
        ("Status", asset.status),
        ("Location", asset.location.name if asset.location else ""),
        ("Department", asset.employee.department.name if asset.employee and asset.employee.department else (asset.department.name if asset.department else "")),
        ("Vendor", asset.vendor.name if asset.vendor else ""),
    ))
    row.update(model_columns(asset, exclude={"id", "system_asset_reference", "location_asset_reference", "category_id", "location_id", "employee_id", "department_id", "manufacturer_id", "vendor_id", "asset_tag", "model", "serial_number", "status"}))
    if asset.employee:
        row["Current Employee Name"] = asset.employee.name
        row["Current Employee Code"] = asset.employee.employee_code
        row["Current Employee Designation"] = asset.employee.designation or ""
        row["Current Employee Email"] = asset.employee.email or ""
        row["Current Employee Phone"] = asset.employee.phone or ""
        row["Current Employee Status"] = asset.employee.employment_status
    row.update(_extra_fields(asset_id=asset.id))
    documents = AssetDocument.query.filter_by(asset_id=asset.id).order_by(AssetDocument.original_name).all()
    if documents:
        row["Uploaded Documents"] = "; ".join(document.original_name for document in documents)
    return row


def employee_row(employee: Employee):
    row = OrderedDict((("Employee Code", employee.employee_code),("Employee Name", employee.name),("Department", employee.department.name if employee.department else ""),("Designation",employee.designation or ""),("Location",employee.location.name if employee.location else ""),("Employment Status",employee.employment_status)))
    row.update(model_columns(employee, exclude={"id", "employee_code", "name", "department_id", "designation", "location_id", "employment_status"}))
    current_assets = Asset.query.filter_by(employee_id=employee.id).order_by(Asset.asset_tag).all()
    if current_assets:
        row["Current Asset References"] = "; ".join(asset.display_reference for asset in current_assets)
        row["Current Asset Tags"] = "; ".join(asset.display_asset_tag or asset.asset_tag for asset in current_assets)
    row.update(_extra_fields(employee_id=employee.id))
    return row


def infrastructure_row(item: InfrastructureItem):
    row = OrderedDict((("Infrastructure Reference",item.infrastructure_reference),("Name",item.display_name),("Category",item.category.name if item.category else ""),("Status",item.status),("Company Location",item.company_location.name if item.company_location else ""),("Location Hierarchy",item.location_path),("Manufacturer",item.manufacturer.name if item.manufacturer else ""),("Model",item.model or ""),("Serial Number",item.serial_number or ""),("Vendor",item.vendor.name if item.vendor else ""),("Department",item.department.name if item.department else "")))
    row.update(model_columns(item, exclude={"id", "infrastructure_reference", "name", "category_id", "status", "location_id", "manufacturer_id", "model", "serial_number", "vendor_id", "department_id", "location_node_id", "specifications_json"}))
    for name, value in item.specifications.items():
        if value not in (None, ""):
            row[f"Specification - {label_for(name)}"] = display_value(value)
    documents = InfrastructureDocument.query.filter_by(infrastructure_id=item.id).order_by(InfrastructureDocument.original_name).all()
    if documents:
        row["Uploaded Documents and Photos"] = "; ".join(document.original_name for document in documents)
    return row


def populated_columns(rows):
    """Keep every field populated by at least one selected record, in stable order."""
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return [key for key in columns if any(row.get(key) not in (None, "") for row in rows)]


def rectangular_rows(rows):
    columns = populated_columns(rows)
    return columns, [OrderedDict((column, row.get(column, "")) for column in columns) for row in rows]
