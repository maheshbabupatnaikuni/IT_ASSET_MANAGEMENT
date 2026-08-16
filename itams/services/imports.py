from __future__ import annotations
import json
import re
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from ipaddress import ip_address
from pathlib import Path
import pandas as pd
from sqlalchemy import func
from ..extensions import db
from ..models import (
    Asset, Employee, Department, Location, AssetCategory, Manufacturer, Vendor,
    ImportBatch, ImportSheet, ImportStagingRow, ImportColumnMapping,
    ImportValidationError, ImportCommitLog, DynamicField, DynamicFieldValue,
    ImportSourceLink, FieldProvenance, AssetAssignment, MovementLog, RepairLog,
    AssetLifecycle, AssetRequest, AssetBlock, ScrapLog,
)
from .audit import audit
from .lifecycle import record_event
from .qr_management import ensure_qr_code
def utc_now():return datetime.now(timezone.utc).replace(tzinfo=None)

SYSTEM_FIELDS = {
    "asset_tag":"Asset Tag", "serial_number":"Serial Number", "hostname":"Hostname",
    "employee_code":"Employee ID", "employee_name":"Employee Name",
    "department":"Department", "location":"Location", "category":"Category",
    "manufacturer":"Brand", "model":"Model", "vendor":"Vendor",
    "ip_address":"IP Address", "email":"Email", "status":"Status",
    "condition":"Condition", "purchase_date":"Purchase Date",
    "warranty_expiry":"Warranty Expiry", "purchase_cost":"Purchase Cost",
    "invoice_number":"Invoice Number", "remarks":"Remarks",
}
SYNONYMS = {
    "asset_tag":["asset tag","assest tag","asset id","system id","asset no","asset number"],
    "serial_number":["serial number","serial no","serialnumber","s/n","sn"],
    "hostname":["hostname","host name","computer name","device name"],
    "employee_code":["employee id","employee code","emp id","staff id"],
    "employee_name":["employee name","user name","username","user","assigned to"],
    "department":["department","dept","department name"],
    "location":["location","location name","site","branch"],
    "category":["category","asset category","asset type","device type"],
    "manufacturer":["manufacturer","brand","make"],
    "model":["model","model name","model no"],
    "vendor":["vendor","supplier","supplier name"],
    "ip_address":["ip","ip address","ipaddress"],
    "email":["email","email address","mail id"],
    "status":["status","asset status"],
    "condition":["condition","asset condition"],
    "purchase_date":["purchase date","purchased date","invoice date"],
    "warranty_expiry":["warranty expiry","warranty date","warranty end date"],
    "purchase_cost":["purchase cost","cost","asset cost"],
    "invoice_number":["invoice number","invoice no","invoice"],
    "remarks":["remarks","remark","comments","notes"],
}
VALID_STATUSES = {"Available","Assigned","Blocked","Under Repair","Lost","Scrapped","Disposed"}

def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()

def clean(value):
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)): return ""
    if isinstance(value, (pd.Timestamp, datetime)): return value.isoformat()
    return str(value).strip()

def detect_mapping(column, signature="", batch_id=None):
    normalized = normalize(column)
    saved = ImportColumnMapping.query.filter_by(batch_id=batch_id,source_signature=signature,source_column=column).order_by(ImportColumnMapping.updated_at.desc()).first() if batch_id else None
    if not saved:saved = ImportColumnMapping.query.filter_by(source_signature=signature, source_column=column, reusable=True).order_by(ImportColumnMapping.updated_at.desc()).first()
    if saved:return ("__ignore__" if saved.action=="ignore" else saved.target_field),1.0
    best, score = None, 0.0
    for target, names in SYNONYMS.items():
        for name in names:
            candidate = SequenceMatcher(None, normalized, normalize(name)).ratio()
            if normalized == normalize(name): candidate = 1.0
            if candidate > score: best, score = target, candidate
    return (best, score) if score >= .86 else (None, score)

def parse_date(value):
    if not value: return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed): raise ValueError("invalid date")
    return parsed.date()

def validate(mapped):
    errors, warnings = [], []
    employee_only = not mapped.get("asset_tag") and (mapped.get("employee_code") or mapped.get("employee_name"))
    if not mapped.get("asset_tag") and not employee_only: errors.append("Empty asset tag or employee identity")
    if mapped.get("ip_address"):
        try: ip_address(mapped["ip_address"])
        except ValueError: errors.append("Invalid IP address")
    if mapped.get("email") and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", mapped["email"]): errors.append("Invalid email address")
    if mapped.get("status") and mapped["status"].title() not in VALID_STATUSES: errors.append("Invalid status")
    for field in ("purchase_date","warranty_expiry"):
        if mapped.get(field):
            try: parse_date(mapped[field])
            except ValueError: errors.append(f"Invalid {SYSTEM_FIELDS[field]}")
    if mapped.get("purchase_cost"):
        try: float(str(mapped["purchase_cost"]).replace(",",""))
        except ValueError: errors.append("Invalid purchase cost")
    if mapped.get("status","").title()=="Assigned" and not (mapped.get("employee_name") or mapped.get("employee_code")):
        errors.append("Asset marked Assigned without employee")
    if not mapped.get("category"): warnings.append("Missing category; Unspecified will be used")
    return errors, warnings

def stage_workbook(batch, workbook_path):
    sheets = pd.read_excel(workbook_path, sheet_name=None, dtype=object)
    batch.sheet_count, batch.total_rows = len(sheets), 0
    seen_tags, seen_serials, seen_employees = set(), set(), set()
    for sheet_name, frame in sheets.items():
        columns = [str(c).strip() for c in frame.columns]
        signature = "|".join(sorted(normalize(c) for c in columns))
        sheet = ImportSheet(batch=batch, name=str(sheet_name), row_count=len(frame), columns_json=json.dumps(columns))
        db.session.add(sheet); db.session.flush()
        mappings = {}
        for column in columns:
            target, confidence = detect_mapping(column, signature, batch.id)
            mappings[column] = (target, confidence)
            existing=ImportColumnMapping.query.filter_by(batch_id=batch.id,source_signature=signature,source_column=column).first()
            if not existing:db.session.add(ImportColumnMapping(batch_id=batch.id,source_signature=signature,source_column=column,target_field=None if target=="__ignore__" else target,action="ignore" if target=="__ignore__" else ("map" if target else "dynamic"),confidence=confidence))
        for idx, source in frame.iterrows():
            original = {c:clean(source.get(c)) for c in columns}
            mapped, dynamic, scores = {}, {}, []
            for column, value in original.items():
                target, confidence = mappings[column]; scores.append(confidence)
                if target=="__ignore__":continue
                if target: mapped[target] = value
                elif value: dynamic[column] = value
            errors, warnings = validate(mapped)
            duplicate = None; live_asset = None; live_employee = None
            tag_key=normalize(mapped.get("asset_tag"));serial_key=normalize(mapped.get("serial_number"));employee_key=normalize(mapped.get("employee_code"))
            if tag_key and tag_key in seen_tags:errors.append("Duplicate asset tag within workbook");duplicate="staged"
            if serial_key and serial_key in seen_serials:errors.append("Duplicate serial number within workbook");duplicate="staged"
            if employee_key and not tag_key and employee_key in seen_employees:duplicate="staged"
            if tag_key:seen_tags.add(tag_key)
            if serial_key:seen_serials.add(serial_key)
            if employee_key:seen_employees.add(employee_key)
            if mapped.get("asset_tag"): live_asset = Asset.query.filter(func.lower(Asset.asset_tag)==mapped["asset_tag"].lower()).first()
            if not live_asset and mapped.get("serial_number"): live_asset = Asset.query.filter(func.lower(Asset.serial_number)==mapped["serial_number"].lower()).first()
            if mapped.get("employee_code"): live_employee = Employee.query.filter(func.lower(Employee.employee_code)==mapped["employee_code"].lower()).first()
            if live_asset: duplicate = "manual" if live_asset.manually_created else "imported"
            proposed = "skip" if live_asset else "insert"
            row = ImportStagingRow(batch=batch,sheet=sheet,row_number=int(idx)+2,original_json=json.dumps(original),mapped_json=json.dumps(mapped),dynamic_json=json.dumps(dynamic),validation_status="Invalid" if errors else "Valid",duplicate_status=duplicate,proposed_action=proposed,error_messages="\n".join(errors+warnings),mapping_confidence=sum(scores)/len(scores) if scores else 0,live_asset_id=live_asset.id if live_asset else None,live_employee_id=live_employee.id if live_employee else None)
            db.session.add(row); db.session.flush()
            for message in errors: db.session.add(ImportValidationError(batch_id=batch.id,staging_row_id=row.id,severity="error",message=message))
            for message in warnings: db.session.add(ImportValidationError(batch_id=batch.id,staging_row_id=row.id,severity="warning",message=message))
            batch.total_rows += 1
            batch.invalid_rows += bool(errors); batch.valid_rows += not errors; batch.duplicate_rows += bool(duplicate)
    batch.status = "Partially Valid" if batch.invalid_rows else "Ready for Review"
    batch.error_count = batch.invalid_rows
    db.session.commit()

def master(model, name):
    value = clean(name) or "Unspecified"
    found = model.query.filter(func.lower(model.name)==value.lower()).first()
    if found: return found
    found = model(name=value); db.session.add(found); db.session.flush(); return found

def commit_batch(batch, user_id):
    if batch.status not in ("Ready for Review","Partially Valid","Approved","Completed with Warnings"):
        raise ValueError("This batch is not ready to commit")
    batch.status = "Committing"; db.session.flush()
    inserted = updated = skipped = 0
    for row in batch.rows:
        if row.validation_status != "Valid" or row.proposed_action=="skip":
            skipped += 1; continue
        values=json.loads(row.mapped_json); dynamic=json.loads(row.dynamic_json or "{}")
        asset=db.session.get(Asset,row.live_asset_id) if row.live_asset_id else None
        if asset and asset.manually_created and row.proposed_action not in ("update","merge"):
            skipped += 1; continue
        department=master(Department,values.get("department")); location=master(Location,values.get("location"))
        category=master(AssetCategory,values.get("category")); manufacturer=master(Manufacturer,values.get("manufacturer")) if values.get("manufacturer") else None
        vendor=master(Vendor,values.get("vendor")) if values.get("vendor") else None
        employee=None
        if values.get("employee_code") or values.get("employee_name"):
            employee=Employee.query.filter(func.lower(Employee.employee_code)==clean(values.get("employee_code")).lower()).first() if values.get("employee_code") else None
            if not employee: employee=Employee.query.filter(func.lower(Employee.name)==clean(values.get("employee_name")).lower()).first()
            if not employee:
                code=values.get("employee_code") or f"IMP-{batch.id}-{row.id}"
                employee=Employee(employee_code=code,name=values.get("employee_name") or code,department=department,location=location,email=values.get("email") or None,manually_created=False)
                db.session.add(employee);db.session.flush()
        if not values.get("asset_tag"):
            row.live_employee_id=employee.id
            db.session.add(ImportSourceLink(batch_id=batch.id,sheet_name=row.sheet.name,source_row_number=row.row_number,employee_id=employee.id,exclusive_owner=not row.duplicate_status))
            for field_name,value in values.items():
                if value:db.session.add(FieldProvenance(batch_id=batch.id,employee_id=employee.id,field_name=field_name,field_value=str(value),source_sheet=row.sheet.name,source_row_number=row.row_number))
            inserted += 1
            continue
        if not asset:
            serial=values.get("serial_number") or None
            asset=Asset(asset_tag=values["asset_tag"],serial_number=serial,category=category,location=location,department=department,employee=employee,manufacturer=manufacturer,vendor=vendor,status="Assigned" if employee else "Available",manually_created=False,updated_by="Excel Import")
            db.session.add(asset); db.session.flush(); record_event(asset,"Asset Added",remarks=f"Imported from {batch.filename}");ensure_qr_code(asset,reason="Automatic QR generation for imported asset")
            inserted += 1
        else:
            updated += 1
        editable={"model":"model","hostname":"hostname","ip_address":"ip_address","condition":"condition","invoice_number":"invoice_number","remarks":"remarks"}
        for source,target in editable.items():
            if values.get(source) and (not asset.manually_created or row.proposed_action in ("update","merge")): setattr(asset,target,values[source])
        if values.get("purchase_date"): asset.purchase_date=parse_date(values["purchase_date"])
        if values.get("warranty_expiry"): asset.warranty_expiry=parse_date(values["warranty_expiry"])
        if values.get("purchase_cost"): asset.purchase_cost=float(str(values["purchase_cost"]).replace(",",""))
        row.live_asset_id=asset.id; row.live_employee_id=employee.id if employee else None
        db.session.add(ImportSourceLink(batch_id=batch.id,sheet_name=row.sheet.name,source_row_number=row.row_number,asset_id=asset.id,employee_id=employee.id if employee else None,exclusive_owner=not row.duplicate_status))
        for field_name, value in values.items():
            if value: db.session.add(FieldProvenance(batch_id=batch.id,asset_id=asset.id,employee_id=employee.id if field_name.startswith("employee") and employee else None,field_name=field_name,field_value=str(value),source_sheet=row.sheet.name,source_row_number=row.row_number))
        for name,value in dynamic.items():
            normalized=normalize(name)
            field=DynamicField.query.filter_by(normalized_name=normalized).first()
            if not field: field=DynamicField(name=name,normalized_name=normalized);db.session.add(field);db.session.flush()
            db.session.add(DynamicFieldValue(dynamic_field_id=field.id,asset_id=asset.id,value=str(value),import_batch_id=batch.id,source_sheet=row.sheet.name))
    batch.inserted_rows, batch.updated_rows, batch.skipped_rows = inserted, updated, skipped
    batch.status = "Completed with Warnings" if skipped or batch.invalid_rows else "Completed"
    batch.approved_by_id=user_id; batch.approved_at=utc_now(); batch.committed_at=utc_now()
    db.session.add(ImportCommitLog(batch_id=batch.id,committed_by_id=user_id,inserted_rows=inserted,updated_rows=updated,skipped_rows=skipped,status=batch.status))
    audit("Import",batch.id,"Commit",new={"inserted":inserted,"updated":updated,"skipped":skipped})
    db.session.commit()

def dependencies_for_batch(batch):
    asset_ids=[x.asset_id for x in ImportSourceLink.query.filter_by(batch_id=batch.id,detached=False).filter(ImportSourceLink.asset_id.isnot(None))]
    employee_ids=[x.employee_id for x in ImportSourceLink.query.filter_by(batch_id=batch.id,detached=False).filter(ImportSourceLink.employee_id.isnot(None))]
    return {
        "assignments": AssetAssignment.query.filter(AssetAssignment.asset_id.in_(asset_ids)).count() if asset_ids else 0,
        "movements": MovementLog.query.filter(MovementLog.asset_id.in_(asset_ids)).count() if asset_ids else 0,
        "repairs": RepairLog.query.filter(RepairLog.asset_id.in_(asset_ids)).count() if asset_ids else 0,
        "lifecycle": AssetLifecycle.query.filter(AssetLifecycle.asset_id.in_(asset_ids),AssetLifecycle.event_type!="Asset Added").count() if asset_ids else 0,
        "requests": AssetRequest.query.filter(AssetRequest.asset_id.in_(asset_ids)).count() if asset_ids else 0,
        "employees": len(set(employee_ids)),
        "assets": len(set(asset_ids)),
    }

def rollback_batch(batch):
    deps=dependencies_for_batch(batch)
    if sum(v for k,v in deps.items() if k not in ("assets","employees")): return False,deps
    links=ImportSourceLink.query.filter_by(batch_id=batch.id,detached=False).all()
    for link in links:
        if link.asset_id and link.exclusive_owner:
            other=ImportSourceLink.query.filter(ImportSourceLink.asset_id==link.asset_id,ImportSourceLink.batch_id!=batch.id,ImportSourceLink.detached==False).first()
            asset=db.session.get(Asset,link.asset_id)
            if asset and not asset.manually_created and not other:
                ImportStagingRow.query.filter_by(batch_id=batch.id,live_asset_id=asset.id).update({"live_asset_id":None})
                AssetLifecycle.query.filter_by(asset_id=asset.id,event_type="Asset Added").delete()
                DynamicFieldValue.query.filter_by(asset_id=asset.id,import_batch_id=batch.id).delete()
                FieldProvenance.query.filter_by(asset_id=asset.id,batch_id=batch.id).delete()
                link.asset_id=None
                db.session.flush()
                db.session.delete(asset)
        if link.employee_id and link.exclusive_owner:
            other=ImportSourceLink.query.filter(ImportSourceLink.employee_id==link.employee_id,ImportSourceLink.batch_id!=batch.id,ImportSourceLink.detached==False).first()
            employee=db.session.get(Employee,link.employee_id)
            used=AssetAssignment.query.filter_by(employee_id=link.employee_id).count() or Asset.query.filter_by(employee_id=link.employee_id).count() or AssetRequest.query.filter_by(requested_for_id=link.employee_id).count()
            if employee and not employee.manually_created and not other and not used:
                ImportStagingRow.query.filter_by(batch_id=batch.id,live_employee_id=employee.id).update({"live_employee_id":None})
                FieldProvenance.query.filter_by(employee_id=employee.id,batch_id=batch.id).delete()
                link.employee_id=None
                db.session.flush()
                db.session.delete(employee)
        link.detached=True
    DynamicFieldValue.query.filter_by(import_batch_id=batch.id).delete()
    FieldProvenance.query.filter_by(batch_id=batch.id).delete()
    batch.status="Rolled Back";audit("Import",batch.id,"Rollback",new=deps);db.session.commit()
    return True,deps
