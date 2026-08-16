from __future__ import annotations
import re
from datetime import datetime, timezone
import pandas as pd
from sqlalchemy import func
from ..extensions import db
from ..models import Asset, AssetAssignment, AssetCategory, Department, Employee, Location, Manufacturer, Vendor
from .audit import audit
from .lifecycle import record_event
from .qr_management import ensure_qr_code
def utc_now():return datetime.now(timezone.utc).replace(tzinfo=None)

ALIASES = {
    "asset_tag": ("asset tag","assest tag","asset id","asset no","asset number","system id"),
    "serial_number": ("serial number","serial no","serialnumber","serial","s no","s.no","sn"),
    "hostname": ("hostname","host name","computer name","system name","computer","device name"),
    "model": ("model","model name","model no","model number"),
    "manufacturer": ("make","manufacturer","brand"),
    "category": ("category","asset category","asset type","device type","type"),
    "employee_name": ("user name","username","employee name","assigned to","user"),
    "employee_code": ("employee id","employee code","emp id","staff id"),
    "department": ("department","dept","department name"),
    "location": ("location","branch","site","location name"),
    "ip_address": ("ip","ip address","ipaddress"),
    "mac_address": ("mac","mac address","macaddress"),
    "operating_system": ("operating system","operatingsystem","os"),
    "os_version": ("os version","osversion"),
    "processor": ("processor","cpu"),
    "ram": ("ram","memory","ram size","ram size gb","ramsizegb"),
    "storage": ("storage","hdd","hard disk","harddisk","ssd","hdd ssd gb","hdd/ssd gb"),
    "purchase_date": ("purchase date","purchased date"),
    "warranty_expiry": ("warranty expiry","warranty date","warranty end date"),
    "purchase_cost": ("purchase cost","cost","asset cost"),
    "invoice_number": ("invoice number","invoice no","invoice"),
    "vendor": ("vendor","supplier","supplier name"),
    "condition": ("condition","asset condition"),
    "remarks": ("remarks","remark","comments","notes"),
    "mail_domain": ("system domain","mail access domain","mail domain","email domain"),
    "monitor_make": ("monitor make","monitormake"),
    "monitor_serial_number": ("monitor serial no","monitor serial number","monitorserialno"),
    "monitor_model": ("monitor model","monitormodel"),
    "office_mobile": ("office mobile","office phone","office device"),
    "mobile_number": ("official number","mobile number","phone number","contact number"),
}

def normalize(value):
    return re.sub(r"[^a-z0-9]+","",str(value or "").strip().lower())

LOOKUP = {normalize(alias):field for field,aliases in ALIASES.items() for alias in aliases}

# A person's name, department, location, operating system, or phone number does
# not prove that an asset exists.  At least one hardware-identifying field must
# be present before an imported row is allowed to create an Asset record.
ASSET_EVIDENCE_FIELDS = (
    "asset_tag", "serial_number", "hostname", "model", "manufacturer",
    "ip_address", "mac_address", "processor", "ram", "storage",
    "monitor_make", "monitor_model", "monitor_serial_number", "office_mobile",
)

def clean(value):
    if value is None or pd.isna(value):return None
    value=str(value).strip()
    return value or None

def has_asset_details(values):
    return any(clean(values.get(field)) for field in ASSET_EVIDENCE_FIELDS)

def asset_has_details(asset):
    return any(clean(getattr(asset, field, None)) for field in (
        "source_asset_tag", "source_serial_number", "serial_number", "hostname",
        "model", "ip_address", "mac_address", "processor", "ram", "storage",
        "monitor_make", "monitor_model", "monitor_serial_number", "office_mobile",
    )) or asset.manufacturer_id is not None

def map_columns(columns):
    return {column:LOOKUP.get(normalize(column)) for column in columns}

def parse_date(value, warnings, row_number, field):
    if not value:return None
    parsed=pd.to_datetime(value,errors="coerce")
    if pd.isna(parsed):
        warnings.append(f"Row {row_number}: invalid {field} ignored")
        return None
    return parsed.date()

def master(model, value):
    value=clean(value)
    if not value:return None
    existing=model.query.filter(func.lower(model.name)==value.lower()).first()
    if existing:return existing
    created=model(name=value);db.session.add(created);db.session.flush();return created

def generated_tag(values, row_number):
    source=values.get("serial_number") or values.get("hostname")
    if not source:
        source="-".join(filter(None,(values.get("manufacturer"),values.get("model"),values.get("ip_address"))))
    base=re.sub(r"[^A-Za-z0-9]+","-",source or f"ROW-{row_number}").strip("-").upper()[:42]
    candidate=f"IMPORTED-{base or row_number}"
    suffix=1
    while Asset.query.filter(func.lower(Asset.asset_tag)==candidate.lower()).first():
        suffix+=1;candidate=f"IMPORTED-{base or row_number}-{suffix}"
    return candidate

def unique_value(model_column, original, fallback, warnings, sheet_name, row_number, label):
    if not original:return fallback
    candidate=original;suffix=1
    while Asset.query.filter(func.lower(model_column)==candidate.lower()).first():
        suffix+=1;candidate=f"{original}-{suffix}"
    if candidate!=original:warnings.append(f"{sheet_name} row {row_number}: repeated {label} retained as source value")
    return candidate

def import_workbook(source, filename="workbook.xlsx"):
    result={"filename":filename,"total_rows":0,"assets_added":0,"employees_added":0,"employee_only_rows":0,"duplicates":0,"empty_rows":0,"unusable_rows":0,"warnings":[],"skipped":[],"sheets":[]}
    workbook=pd.read_excel(source,sheet_name=None,dtype=object)
    for sheet_name,frame in workbook.items():
        result["sheets"].append(str(sheet_name));mapping=map_columns(frame.columns)
        for index,row in frame.iterrows():
            row_number=int(index)+2;result["total_rows"]+=1
            raw=[clean(row.get(column)) for column in frame.columns]
            if not any(raw):
                result["empty_rows"]+=1;result["skipped"].append(f"{sheet_name} row {row_number}: empty row");continue
            values={}
            for column,target in mapping.items():
                value=clean(row.get(column))
                if target and value and not values.get(target):values[target]=value
            try:
                record_type="employee"
                employee_created=False
                with db.session.begin_nested():
                    location=master(Location,values.get("location"));department=master(Department,values.get("department"))
                    employee=None
                    if values.get("employee_code"):employee=Employee.query.filter(func.lower(Employee.employee_code)==values["employee_code"].lower()).first()
                    if not employee and values.get("employee_name"):employee=Employee.query.filter(func.lower(Employee.name)==values["employee_name"].lower()).first()
                    if values.get("employee_name") and not employee:
                        department=department or master(Department,"Unspecified")
                        location=location or master(Location,"Unspecified")
                        code=values.get("employee_code") or f"IMP-{normalize(values['employee_name'])[:24].upper()}"
                        base=code;suffix=1
                        while Employee.query.filter(func.lower(Employee.employee_code)==code.lower()).first():
                            suffix+=1;code=f"{base}-{suffix}"
                        employee=Employee(employee_code=code,name=values["employee_name"],department=department,location=location,manually_created=False)
                        db.session.add(employee);db.session.flush();employee_created=True
                    if employee and values.get("mobile_number") and not employee.phone:
                        employee.phone=values["mobile_number"]
                    if not has_asset_details(values):
                        if not employee:
                            raise ValueError("No employee identity or asset details were found")
                        audit("Employee",employee.id,"Excel Import",new={"employee_code":employee.employee_code,"name":employee.name,"source":filename,"record_type":"Employee only"})
                        result["warnings"].append(f"{sheet_name} row {row_number}: retained as employee information only")
                    else:
                        record_type="asset"
                        category_name=values.get("category")
                        identity=" ".join(filter(None,(values.get("asset_tag"),values.get("model"),values.get("office_mobile")))).lower()
                        if not category_name:
                            if "lap" in identity:category_name="Laptop"
                            elif "desk" in identity or values.get("asset_tag") or values.get("hostname") or values.get("manufacturer") or values.get("model"):category_name="Desktop"
                            elif values.get("office_mobile"):category_name="Mobile"
                            else:category_name="Unspecified"
                        category=master(AssetCategory,category_name)
                        manufacturer=master(Manufacturer,values.get("manufacturer"));vendor=master(Vendor,values.get("vendor"))
                        purchase_cost=None
                        if values.get("purchase_cost"):
                            try:purchase_cost=float(str(values["purchase_cost"]).replace(",",""))
                            except ValueError:result["warnings"].append(f"{sheet_name} row {row_number}: invalid purchase cost ignored")
                        generated=generated_tag(values,row_number)
                        internal_tag=unique_value(Asset.asset_tag,values.get("asset_tag"),generated,result["warnings"],sheet_name,row_number,"Asset Tag")
                        internal_serial=unique_value(Asset.serial_number,values.get("serial_number"),None,result["warnings"],sheet_name,row_number,"Serial Number")
                        unsupported={str(column):clean(row.get(column)) for column,target in mapping.items() if not target and clean(row.get(column))}
                        uploaded_remarks=values.get("remarks")
                        if unsupported:
                            extras="; ".join(f"{key}: {value}" for key,value in unsupported.items())
                            uploaded_remarks=f"{uploaded_remarks}; {extras}" if uploaded_remarks else extras
                        assigned=employee is not None
                        asset=Asset(
                            asset_tag=internal_tag,source_asset_tag=values.get("asset_tag"),serial_number=internal_serial,
                            source_serial_number=values.get("serial_number"),hostname=values.get("hostname"),
                            model=values.get("model"),manufacturer=manufacturer,category=category,
                            location=location,department=department,vendor=vendor,username=values.get("employee_name"),
                            ip_address=values.get("ip_address"),mac_address=values.get("mac_address"),
                            operating_system=values.get("operating_system"),os_version=values.get("os_version"),mail_domain=values.get("mail_domain"),
                            processor=values.get("processor"),ram=values.get("ram"),storage=values.get("storage"),
                            monitor_make=values.get("monitor_make"),monitor_serial_number=values.get("monitor_serial_number"),
                            monitor_model=values.get("monitor_model"),office_mobile=values.get("office_mobile"),mobile_number=values.get("mobile_number"),
                            purchase_date=parse_date(values.get("purchase_date"),result["warnings"],row_number,"purchase date"),
                            warranty_expiry=parse_date(values.get("warranty_expiry"),result["warnings"],row_number,"warranty date"),
                            purchase_cost=purchase_cost,invoice_number=values.get("invoice_number"),
                            condition=values.get("condition") or "Good",remarks=uploaded_remarks,
                            status="Assigned" if assigned else "Available",employee=employee if assigned else None,
                            assigned_date=utc_now().date() if assigned else None,manually_created=False,
                            updated_by=f"Excel Import: {filename}",
                        )
                        db.session.add(asset);db.session.flush()
                        record_event(asset,"Asset Added",condition_after=asset.condition,remarks="Added through Excel import")
                        if assigned:
                            db.session.add(AssetAssignment(asset=asset,employee=employee,assigned_at=utc_now(),condition_out=asset.condition,remarks="Existing assignment reflected from Excel import",active=True))
                            record_event(asset,"Assigned",to_employee=employee,condition_after=asset.condition,reason="Existing assignment reflected from uploaded workbook")
                        audit("Asset",asset.id,"Excel Import",new={"asset_tag":asset.asset_tag})
                        ensure_qr_code(asset,reason="Automatic QR generation for imported asset")
                db.session.commit()
                result["employees_added"]+=int(employee_created)
                if record_type=="asset":result["assets_added"]+=1
                else:result["employee_only_rows"]+=1
            except Exception as exc:
                db.session.rollback();result["unusable_rows"]+=1
                result["skipped"].append(f"{sheet_name} row {row_number}: {str(exc)[:160]}")
    return result

def reconcile_imported_assignments():
    """Repair rows imported by the former importer that discarded their owner.

    The reconciliation is deliberately narrow and idempotent: only Excel-imported
    assets that are still Available, have no owner/history, and have a source
    username matching an existing employee are changed.
    """
    candidates = Asset.query.filter(
        Asset.manually_created.is_(False),
        Asset.status == "Available",
        Asset.employee_id.is_(None),
        Asset.username.isnot(None),
        func.trim(Asset.username) != "",
    ).all()
    changed = 0
    for asset in candidates:
        if not asset_has_details(asset):
            continue
        if AssetAssignment.query.filter_by(asset_id=asset.id).first():
            continue
        employee = Employee.query.filter(
            func.lower(func.trim(Employee.name)) == asset.username.strip().lower()
        ).order_by(Employee.id).first()
        if not employee:
            department=asset.department or master(Department,"Unspecified")
            location=asset.location or master(Location,"Unspecified")
            base=f"IMP-{normalize(asset.username)[:24].upper()}";code=base;suffix=1
            while Employee.query.filter(func.lower(Employee.employee_code)==code.lower()).first():
                suffix+=1;code=f"{base}-{suffix}"
            employee=Employee(employee_code=code,name=asset.username.strip(),department=department,location=location,manually_created=False)
            db.session.add(employee);db.session.flush()
        now = utc_now()
        asset.employee = employee
        asset.department = employee.department
        asset.location = employee.location
        asset.status = "Assigned"
        asset.assigned_date = now.date()
        asset.state_version = (asset.state_version or 0) + 1
        db.session.add(AssetAssignment(
            asset=asset, employee=employee, assigned_at=now,
            condition_out=asset.condition,
            remarks="Existing assignment reconciled from Excel source",
            active=True,
        ))
        record_event(
            asset, "Assigned", to_employee=employee,
            condition_after=asset.condition,
            reason="Existing assignment reconciled from uploaded workbook",
            occurred_at=now,
        )
        audit(
            "Asset", asset.id, "Import Assignment Reconciled",
            old={"status": "Available", "employee": None},
            new={"status": "Assigned", "employee": employee.name},
        )
        changed += 1
    if changed:
        db.session.commit()
    return changed
