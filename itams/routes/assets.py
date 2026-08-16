from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, send_from_directory, abort, session, current_app
import io
import uuid
from pathlib import Path
import pandas as pd
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from datetime import date
from sqlalchemy import or_, func, select
from ..extensions import db
from ..models import Asset, Department, Location, Employee, AssetCategory, Manufacturer, Vendor, AssetLifecycle, AssetBlock, AssetAssignment, MovementLog, RepairLog, ScrapLog, AssetRequest, DynamicFieldValue, ImportSourceLink, FieldProvenance, CustomAttribute, AssetDocument, AuditLog, ImportBatch, HiddenProfileField, AssetSpecificationChange, AssetQRIdentity
from ..services.audit import audit, movement
from ..services.lifecycle import record_event
from ..services.uploads import validate_upload
from ..services.qr_management import asset_profile_url, ensure_qr_code, generate_for_assets, qr_file_path, qr_metadata, qr_history, regenerate_qr_code, resolve_active_token
from ..services.asset_deletion import permanently_delete_asset
from ..services.lbac import validate_location, can_access_location
from ..services.master_values import resolve_master_id
from .helpers import login_required, required_reason, normalized_role, can, current_password_valid
from ..services.lifecycle import record_event
bp=Blueprint("assets",__name__,url_prefix="/assets")
@bp.before_request
def restrict_asset_routes():
 if not normalized_role():return redirect(url_for("auth.login",next=request.full_path if request.query_string else request.path))
 permissions={"assets.list_assets":"asset.view","assets.available":"asset.available.view","assets.lost":"lost.view","assets.detail":"asset.view","assets.profile":"asset.view","assets.qr_resolve":"asset.view","assets.qr_image":"asset.view","assets.qr_download":"asset.view","assets.qr_label":"asset.view","assets.qr_regenerate":"asset.qr.manage","assets.qr_manage":"asset.qr.manage","assets.status_page":"asset.view","assets.quick_status":"asset.view","assets.history":"asset.history.view","assets.employee_data":"employee.view","assets.new":"asset.add","assets.edit":"asset.edit","assets.custom_field":"asset.custom.manage","assets.hide_field":"asset.custom.manage","assets.restore_field":"asset.custom.manage","assets.replace_specification":"asset.specification.replace","assets.upload_document":"asset.edit","assets.document":"asset.view","assets.delete":"asset.delete","assets.blocked":"asset.blocked.view","assets.block_asset":"asset.block","assets.unblock":"asset.unblock","assets.state_change":"asset.view"}
 if request.endpoint=="assets.blocked" and request.method=="POST":permissions["assets.blocked"]="asset.block"
 if request.endpoint=="assets.available" and request.args.get("format"):permissions["assets.available"]="asset.export"
 required=permissions.get(request.endpoint,"asset.view")
 if not can(required):abort(403)
@bp.route("/")
@login_required
def list_assets():
 q=request.args.get("q",""); status=request.args.get("status",""); location=request.args.get("location",type=int);category_name=request.args.get("category_name","").strip();department_name=request.args.get("department_name","").strip();sort=request.args.get("sort","reference");direction=request.args.get("direction","asc");query=Asset.query
 if q: query=query.outerjoin(Asset.employee).outerjoin(Asset.department).outerjoin(Asset.location).filter(or_(Asset.location_asset_reference.contains(q),Asset.system_asset_reference.contains(q),Asset.asset_tag.contains(q),Asset.source_asset_tag.contains(q),Asset.serial_number.contains(q),Asset.hostname.contains(q),Asset.ip_address.contains(q),Employee.name.contains(q),Department.name.contains(q),Location.name.contains(q)))
 if status: query=query.filter(Asset.status==status)
 if location: query=query.filter(Asset.location_id==location)
 if category_name:query=query.filter(Asset.category.has(func.lower(AssetCategory.name)==category_name.lower()))
 if department_name:query=query.filter(Asset.status=="Assigned",Asset.employee.has(Employee.department.has(func.lower(Department.name)==department_name.lower())))
 sort_keys={
  "reference":lambda a:(a.display_reference or "").casefold(),"asset_tag":lambda a:(a.display_asset_tag or "").casefold(),
  "category":lambda a:(a.category.name if a.category else "").casefold(),"model":lambda a:(a.model or "").casefold(),
  "employee":lambda a:(a.employee.name if a.employee else "").casefold(),
  "department":lambda a:((a.employee.department.name if a.employee and a.employee.department else a.department.name if a.department else "").casefold()),
  "location":lambda a:(a.location.name if a.location else "").casefold(),"status":lambda a:(a.status or "").casefold(),
 }
 if sort not in sort_keys:sort="reference"
 if direction not in ("asc","desc"):direction="asc"
 assets=sorted(query.all(),key=lambda a:(sort_keys[sort](a),a.id),reverse=direction=="desc")
 return render_template("assets_reflected_v2.html",assets=assets,q=q,status=status,location=location,category_name=category_name,department_name=department_name,sort=sort,direction=direction,locations=Location.query.order_by(Location.name).all())
def refs(): return dict(categories=AssetCategory.query.filter_by(active=True).all(),locations=Location.query.filter_by(active=True).all(),departments=Department.query.filter_by(active=True).all(),employees=Employee.query.filter_by(active=True).all(),manufacturers=Manufacturer.query.filter_by(active=True).all(),vendors=Vendor.query.filter_by(active=True).all())

ASSET_EDIT_TEXT_FIELDS=(
 "model","condition","remarks","invoice_number","hostname","ip_address","mac_address",
 "mail_domain","operating_system","os_version","processor","ram","storage",
 "monitor_make","monitor_model","monitor_serial_number",
)

def edit_snapshot(a):
 return {
  "asset_tag":a.display_asset_tag,"category_id":a.category_id,"manufacturer_id":a.manufacturer_id,
  "model":a.model,"serial_number":a.source_serial_number or a.serial_number,
  "vendor_id":a.vendor_id,"purchase_date":str(a.purchase_date or ""),
  "purchase_cost":a.purchase_cost,"warranty_expiry":str(a.warranty_expiry or ""),
  "invoice_number":a.invoice_number,"location_id":a.location_id,"department_id":a.department_id,
  "condition":a.condition,"remarks":a.remarks,
 **{field:getattr(a,field) for field in ASSET_EDIT_TEXT_FIELDS if field not in ("model","condition","remarks","invoice_number")},
 }

def employee_edit_snapshot(employee):
 return {
  "employee_code":employee.employee_code,"name":employee.name,"designation":employee.designation,
  "email":employee.email,"phone":employee.phone,"department_id":employee.department_id,
  "location_id":employee.location_id,"employment_status":employee.employment_status,
  "blocked":employee.blocked,
 }

def populate_employee(employee,form):
 employee_code=(form.get("employee_code") or "").strip()
 name=(form.get("employee_name") or "").strip()
 department_id=form.get("employee_department_id")
 location_id=form.get("employee_location_id",type=int)
 if not employee_code or not name or not department_id or not location_id:
  raise ValueError("Employee ID, name, department, and location are required.")
 duplicate=Employee.query.filter(func.lower(Employee.employee_code)==employee_code.lower(),Employee.id!=employee.id).first()
 if duplicate:raise ValueError("Employee ID already exists.")
 employee.employee_code=employee_code;employee.name=name
 employee.designation=(form.get("employee_designation") or "").strip() or None
 employee.email=(form.get("employee_email") or "").strip() or None
 employee.phone=(form.get("employee_phone") or "").strip() or None
 location=validate_location(location_id)
 if employee.id and location.id!=employee.location_id and Asset.query.filter_by(employee_id=employee.id).count():
  raise ValueError("An employee holding assets cannot be moved to another location here. Use the transfer workflow.")
 employee.department_id=resolve_master_id(Department,form,"employee_department_id",required=True,label="Department");employee.location_id=location.id

def populate(a,form):
 raw_tag=(form.get("asset_tag") or "").strip()
 a.asset_tag=raw_tag or (f"SYS-{a.system_asset_reference}" if a.system_asset_reference else None)
 a.source_asset_tag=raw_tag or None
 raw_serial=(form.get("serial_number") or "").strip() or None
 a.serial_number=raw_serial
 a.source_serial_number=raw_serial
 for field in ASSET_EDIT_TEXT_FIELDS:
  setattr(a,field,(form.get(field) or "").strip() or None)
 a.category_id=resolve_master_id(AssetCategory,form,"category_id",required=True,label="Category")
 a.department_id=resolve_master_id(Department,form,"department_id",label="Department")
 a.manufacturer_id=resolve_master_id(Manufacturer,form,"manufacturer_id",label="Brand")
 a.vendor_id=resolve_master_id(Vendor,form,"vendor_id",label="Vendor")
 a.location_id=int(form["location_id"]) if form.get("location_id") else None
 for f in ["purchase_date","warranty_expiry"]: setattr(a,f,datetime.strptime(form[f],"%Y-%m-%d").date() if form.get(f) else None)
 a.purchase_cost=request.form.get("purchase_cost",type=float)
 if not a.category_id: raise ValueError("Category is required")
 a.location_id=validate_location(a.location_id).id
 if a.employee_id and a.employee and a.location_id!=a.employee.location_id:
  raise ValueError("Assigned asset location must match its employee. Use Transfer Asset for a location change.")
def validate_unique(a):
 if a.asset_tag:
  duplicate_tag=Asset.query.filter(func.lower(Asset.asset_tag)==a.asset_tag.lower())
  if a.id:duplicate_tag=duplicate_tag.filter(Asset.id!=a.id)
  if duplicate_tag.first():raise ValueError("Asset Tag already exists.")
 if a.serial_number:
  duplicate_serial=Asset.query.filter(func.lower(Asset.serial_number)==a.serial_number.lower())
  if a.id:duplicate_serial=duplicate_serial.filter(Asset.id!=a.id)
  if duplicate_serial.first():raise ValueError("Serial Number already exists.")
@bp.route("/new",methods=["GET","POST"])
@login_required
def new():
 if request.method=="POST":
  try:
   a=Asset(status="Available",employee_id=None,department_id=None);populate(a,request.form);validate_unique(a);a.updated_by="IT Admin";db.session.add(a);db.session.flush();record_event(a,"Asset Added",condition_after=a.condition,remarks=a.remarks);audit("Asset",a.id,"Create",new={"system_asset_reference":a.system_asset_reference,"asset_tag":a.display_asset_tag,"status":"Available"});ensure_qr_code(a,actor=session.get("user_name"),reason="Automatic QR generation for new asset");db.session.commit();flash("Asset created and marked Available.","success");return redirect(url_for("assets.profile",system_asset_reference=a.system_asset_reference))
  except Exception as e: db.session.rollback();flash(str(e) if isinstance(e,ValueError) else "Could not save asset. Asset tag and serial number must be unique.","danger")
 return render_template("asset_form_qr.html",asset=None,**refs())
@bp.route("/<int:asset_id>/edit",methods=["GET","POST"])
@login_required
def edit(asset_id):
 a=db.session.get(Asset,asset_id)
 if not a:return "Not found",404
 if request.method=="POST":
  edit_employee=bool(request.form.get("edit_employee_details"))
  if edit_employee and not can("employee.edit"):abort(403)
  reason=required_reason("Reason for edit")
  if not reason: return render_template("asset_form_qr.html",asset=a,**refs())
  try:
   old=edit_snapshot(a);employee=a.employee if edit_employee else None;old_employee=employee_edit_snapshot(employee) if employee else None
   populate(a,request.form);validate_unique(a)
   if employee:populate_employee(employee,request.form)
   a.updated_by=session.get("user_name") or "IT Admin";new=edit_snapshot(a)
   audit("Asset",a.id,"Update",old=old,new=new,reason=reason)
   new_employee=employee_edit_snapshot(employee) if employee else None;employee_changed=bool(employee and old_employee!=new_employee)
   if employee_changed:audit("Employee",employee.id,"Update",old=old_employee,new=new_employee,reason=f"Updated from Asset {a.system_asset_reference}: {reason}")
   db.session.commit();flash("Asset and current employee details updated." if employee_changed else "Asset details and technical specifications updated.","success");return redirect(url_for("assets.profile",system_asset_reference=a.system_asset_reference))
  except Exception as e:db.session.rollback();flash(str(e) if isinstance(e,ValueError) else "Could not update asset.","danger")
 return render_template("asset_form_qr.html",asset=a,**refs())
@bp.route("/<int:asset_id>")
@login_required
def detail(asset_id):
 a=db.session.get(Asset,asset_id)
 if not a:abort(404)
 return render_asset_profile(a)

def profile_context(a):
 assignments=AssetAssignment.query.filter_by(asset_id=a.id).order_by(AssetAssignment.assigned_at.desc()).all()
 active_assignment=next((row for row in assignments if row.active),None)
 active_block=AssetBlock.query.filter_by(asset_id=a.id,active=True).first()
 events=AssetLifecycle.query.filter_by(asset_id=a.id).order_by(AssetLifecycle.occurred_at.desc()).all()
 import_links=ImportSourceLink.query.filter_by(asset_id=a.id).order_by(ImportSourceLink.created_at.desc()).all()
 batch_ids={link.batch_id for link in import_links}
 batches={b.id:b for b in ImportBatch.query.filter(ImportBatch.id.in_(batch_ids)).all()} if batch_ids else {}
 return dict(
  asset=a,
  repairs=RepairLog.query.filter_by(asset_id=a.id).order_by(RepairLog.created_at.desc()).all(),
  assignments=assignments,
  previous_employees=[x for x in assignments if not x.active],
  transfers=MovementLog.query.filter_by(asset_id=a.id,action="Transfer").order_by(MovementLog.date.desc()).all(),
  events=events,
  lost_events=[x for x in events if x.event_type in ("Lost","Recovered")],
  movements=MovementLog.query.filter_by(asset_id=a.id).order_by(MovementLog.date.desc()).all(),
  blocks=AssetBlock.query.filter_by(asset_id=a.id).order_by(AssetBlock.blocked_date.desc()).all(),
  scrap=ScrapLog.query.filter_by(asset_id=a.id).first(),
  dynamic_values=DynamicFieldValue.query.filter_by(asset_id=a.id).all(),
  custom_fields=CustomAttribute.query.filter_by(asset_id=a.id).order_by(CustomAttribute.name).all(),
  sources=FieldProvenance.query.filter_by(asset_id=a.id).order_by(FieldProvenance.updated_at.desc()).all(),
  import_links=import_links,batches=batches,
  audit_rows=AuditLog.query.filter_by(entity="Asset",entity_id=str(a.id)).order_by(AuditLog.timestamp.desc()).all(),
  documents=AssetDocument.query.filter_by(asset_id=a.id).order_by(AssetDocument.created_at.desc()).all(),
  hidden_fields=HiddenProfileField.query.filter_by(asset_id=a.id).order_by(HiddenProfileField.field_label).all(),
  hidden_codes={x.field_code for x in HiddenProfileField.query.filter_by(asset_id=a.id).all()},
  specification_changes=AssetSpecificationChange.query.filter_by(asset_id=a.id).order_by(AssetSpecificationChange.created_at.desc()).all(),
  status_restore_target="Assigned" if a.employee_id and active_assignment else ("Lost" if active_block and active_block.previous_status=="Lost" else "Available"),
 )

@bp.route("/profile/<string:system_asset_reference>")
@login_required
def profile(system_asset_reference):
 a=Asset.query.filter_by(system_asset_reference=system_asset_reference).first()
 if not a:abort(404)
 return render_asset_profile(a)

def render_asset_profile(a):
 qr_error=None
 try:
  _path,_url,created=ensure_qr_code(a)
  if created:db.session.commit()
 except RuntimeError as exc:qr_error=str(exc)
 return render_template("asset_profile_v2.html",qr_error=qr_error,profile_url=asset_profile_url(a) if not qr_error else None,qr_meta=qr_metadata(a),qr_history_rows=qr_history(a.id),**profile_context(a))

@bp.route("/qr/<string:token>")
@login_required
def qr_resolve(token):
 identity=resolve_active_token(token)
 if identity:return redirect(url_for("assets.profile",system_asset_reference=identity.asset.system_asset_reference))
 outside=db.session.execute(select(AssetQRIdentity,Asset.location_id).join(Asset,Asset.id==AssetQRIdentity.asset_id).where(AssetQRIdentity.token==token,AssetQRIdentity.active==True).execution_options(lbac_bypass=True)).first()
 if outside and not can_access_location(outside[1]):
  return render_template("access_denied.html",permission="location.view.assigned",message="You do not have permission to access this asset."),403
 archived=AssetQRIdentity.query.filter_by(token=token).first()
 if archived:return render_template("error.html",title="QR Code Archived",message="This QR identity has been replaced and is no longer valid. Please use the latest asset label."),410
 abort(404)

@bp.route("/profile/<string:system_asset_reference>/qr.png")
@login_required
def qr_image(system_asset_reference):
 asset=Asset.query.filter_by(system_asset_reference=system_asset_reference).first()
 if not asset:abort(404)
 try:
  path,_url,created=ensure_qr_code(asset)
  if created:db.session.commit()
 except RuntimeError as exc:current_app.logger.warning("QR unavailable for %s: %s",system_asset_reference,exc);abort(503)
 return send_file(path,mimetype="image/png",conditional=True,max_age=3600)

@bp.route("/profile/<string:system_asset_reference>/qr/download")
@login_required
def qr_download(system_asset_reference):
 asset=Asset.query.filter_by(system_asset_reference=system_asset_reference).first()
 if not asset:abort(404)
 try:
  path,_url,created=ensure_qr_code(asset)
  if created:db.session.commit()
 except RuntimeError as exc:current_app.logger.warning("QR unavailable for %s: %s",system_asset_reference,exc);abort(503)
 return send_file(path,mimetype="image/png",as_attachment=True,download_name=f"{system_asset_reference}-QR.png")

@bp.route("/profile/<string:system_asset_reference>/label")
@login_required
def qr_label(system_asset_reference):
 asset=Asset.query.filter_by(system_asset_reference=system_asset_reference).first()
 if not asset:abort(404)
 try:
  _path,_url,created=ensure_qr_code(asset)
  if created:db.session.commit()
 except RuntimeError as exc:flash(str(exc),"warning")
 return render_template("asset_qr_label.html",asset=asset)

@bp.route("/profile/<string:system_asset_reference>/qr/regenerate",methods=["POST"])
@login_required
def qr_regenerate(system_asset_reference):
 asset=Asset.query.filter_by(system_asset_reference=system_asset_reference).first()
 if not asset:abort(404)
 reason=request.form.get("reason","").strip();mode=request.form.get("mode","image")
 if not reason:flash("Regeneration reason is required.","danger");return redirect(url_for("assets.profile",system_asset_reference=system_asset_reference))
 if mode=="identity":
  if normalized_role()!="Super Admin":abort(403)
  if request.form.get("identity_confirmation","").strip()!="REGENERATE IDENTITY":
   flash('Type "REGENERATE IDENTITY" to confirm secure identity replacement.',"danger")
   return redirect(url_for("assets.profile",system_asset_reference=system_asset_reference))
 try:
  regenerate_qr_code(asset,mode,reason,actor=session.get("user_name"))
  db.session.commit();flash("QR identity regenerated; the previous QR is now invalid." if mode=="identity" else "QR image regenerated. The permanent reference and existing link remain unchanged.","success")
 except (RuntimeError,ValueError) as exc:
  db.session.rollback();flash(str(exc),"warning")
 return redirect(url_for("assets.profile",system_asset_reference=system_asset_reference))

@bp.route("/qr-codes",methods=["GET","POST"])
@login_required
def qr_manage():
 assets=Asset.query.order_by(Asset.system_asset_reference).all()
 if request.method=="POST":
  action=request.form.get("action")
  if action not in ("generate_missing","regenerate_selected","regenerate_all"):
   flash("Choose a QR action and try again.","warning")
   return redirect(url_for("assets.qr_manage"))
  reason=(request.form.get("reason") or "").strip()
  if action!="generate_missing" and not reason:
   flash("A regeneration reason is required.","danger");return redirect(url_for("assets.qr_manage"))
  selected_ids={int(value) for value in request.form.getlist("asset_ids") if value.isdigit()}
  targets=assets if action!="regenerate_selected" else [asset for asset in assets if asset.id in selected_ids]
  if action=="regenerate_selected" and not targets:
   flash("Select at least one asset.","danger");return redirect(url_for("assets.qr_manage"))
  result=generate_for_assets(targets,action="missing" if action=="generate_missing" else "image",reason=reason or "Bulk generation of missing QR codes",actor=session.get("user_name"))
  audit("Asset QR","Bulk",action.replace("_"," ").title(),new={"requested":len(targets),"generated":result["generated"],"existing":result["existing"],"failed":len(result["failed"])},reason=reason or "Generate missing QR codes")
  db.session.commit()
  if result["failed"]:flash(f"QR processing completed with {len(result['failed'])} warning(s). Check the configured base URL.","warning")
  else:flash(f"QR processing complete: {result['generated']} generated, {result['existing']} already present.","success")
 qr_ready_ids={asset.id for asset in assets if qr_metadata(asset)["status"]=="Active"}
 missing=len(assets)-len(qr_ready_ids)
 return render_template("asset_qr_manage.html",assets=assets,missing=missing,qr_ready_ids=qr_ready_ids,base_url=current_app.config.get("ASSET_PROFILE_BASE_URL",""))

@bp.route("/<int:asset_id>/custom-fields",methods=["POST"])
@login_required
def custom_field(asset_id):
 asset=db.session.get(Asset,asset_id)
 if not asset:abort(404)
 field_id=request.form.get("field_id",type=int)
 item=db.session.get(CustomAttribute,field_id) if field_id else None
 if item and item.asset_id!=asset.id:abort(400)
 action=request.form.get("action","save")
 if action=="delete":
  if not item:abort(400)
  old={"name":item.name,"value":item.value};db.session.delete(item)
  audit("Asset",asset.id,"Custom Field Removed",old=old,reason=request.form.get("reason"))
  db.session.commit();flash("Custom field removed.","success")
  return redirect(url_for("assets.detail",asset_id=asset.id))
 name=request.form.get("name","").strip();value=request.form.get("value","").strip()
 if not name:flash("Custom field name is required.","danger")
 else:
  duplicate=CustomAttribute.query.filter(CustomAttribute.asset_id==asset.id,db.func.lower(CustomAttribute.name)==name.lower())
  if item:duplicate=duplicate.filter(CustomAttribute.id!=item.id)
  if duplicate.first():flash("This asset already has a custom field with that name.","danger")
  else:
   old={"name":item.name,"value":item.value} if item else None
   if not item:item=CustomAttribute(asset=asset,created_by=session.get("user_name"));db.session.add(item)
   item.name=name;item.value=value;db.session.flush()
   audit("Asset",asset.id,"Custom Field Updated" if old else "Custom Field Added",old=old,new={"name":name,"value":value},reason=request.form.get("reason"))
   db.session.commit();flash("Custom field saved.","success")
 return redirect(url_for("assets.detail",asset_id=asset.id))

ASSET_REMOVABLE_FIELDS={"storage":"Storage","ram":"RAM","processor":"Processor","operating_system":"Operating System","os_version":"OS Version","monitor_make":"Monitor Make","monitor_model":"Monitor Model","monitor_serial_number":"Monitor Serial Number","ip_address":"IP Address","mac_address":"MAC Address","hostname":"Hostname","mail_domain":"System Domain"}
@bp.route("/<int:asset_id>/fields/hide",methods=["POST"])
@login_required
def hide_field(asset_id):
 asset=db.session.get(Asset,asset_id);field_code=request.form.get("field_code","")
 if not asset:abort(404)
 if field_code not in ASSET_REMOVABLE_FIELDS:abort(400)
 if HiddenProfileField.query.filter_by(asset_id=asset.id,field_code=field_code).first():flash("That field is already removed from this asset.","warning")
 else:
  old_value=getattr(asset,field_code);reason=request.form.get("reason","").strip() or "Field no longer required"
  db.session.add(HiddenProfileField(asset=asset,field_code=field_code,field_label=ASSET_REMOVABLE_FIELDS[field_code],removed_value=str(old_value) if old_value is not None else None,reason=reason,removed_by=session.get("user_name")))
  setattr(asset,field_code,None);audit("Asset",asset.id,"Standard Field Removed",old={field_code:old_value},new={field_code:None},reason=reason);db.session.commit();flash(f"{ASSET_REMOVABLE_FIELDS[field_code]} removed from this asset. The old value remains in audit history.","success")
 return redirect(url_for("assets.detail",asset_id=asset.id))

@bp.route("/<int:asset_id>/fields/<int:field_id>/restore",methods=["POST"])
@login_required
def restore_field(asset_id,field_id):
 asset=db.session.get(Asset,asset_id);item=db.session.get(HiddenProfileField,field_id)
 if not asset or not item:abort(404)
 if item.asset_id!=asset.id or item.field_code not in ASSET_REMOVABLE_FIELDS:abort(400)
 label=item.field_label;restored=request.form.get("value",item.removed_value or "").strip() or None;setattr(asset,item.field_code,restored)
 audit("Asset",asset.id,"Standard Field Restored",old={item.field_code:None},new={item.field_code:restored},reason=request.form.get("reason") or "Field restored");db.session.delete(item);db.session.commit();flash(f"{label} restored.","success")
 return redirect(url_for("assets.detail",asset_id=asset.id))

SPECIFICATION_FIELDS={"storage":"Storage","ram":"RAM","processor":"Processor","monitor_model":"Monitor Model","monitor_make":"Monitor Make","monitor_serial_number":"Monitor Serial Number"}
@bp.route("/<int:asset_id>/specification",methods=["POST"])
@login_required
def replace_specification(asset_id):
 asset=db.session.get(Asset,asset_id);field_code=request.form.get("field_code","");new_value=request.form.get("new_value","").strip();disposition=request.form.get("disposition","").strip()
 if not asset:abort(404)
 if field_code not in SPECIFICATION_FIELDS or not new_value or not disposition:flash("Specification, replacement value, and removed-item disposition are required.","danger");return redirect(url_for("assets.detail",asset_id=asset.id))
 old_value=getattr(asset,field_code);setattr(asset,field_code,new_value)
 hidden=HiddenProfileField.query.filter_by(asset_id=asset.id,field_code=field_code).first()
 if hidden:db.session.delete(hidden)
 change=AssetSpecificationChange(asset=asset,field_code=field_code,field_label=SPECIFICATION_FIELDS[field_code],old_value=str(old_value) if old_value is not None else None,new_value=new_value,disposition=disposition,remarks=request.form.get("remarks","").strip(),performed_by=session.get("user_name"));db.session.add(change)
 record_event(asset,"Specification Updated",condition_before=asset.condition,condition_after=asset.condition,reason=f"{SPECIFICATION_FIELDS[field_code]} changed from {old_value or '-'} to {new_value}",remarks=f"Removed item: {disposition}. {change.remarks or ''}")
 audit("Asset",asset.id,"Specification Replaced",old={field_code:old_value},new={field_code:new_value,"removed_item_disposition":disposition},reason=request.form.get("remarks"));db.session.commit();flash(f"{SPECIFICATION_FIELDS[field_code]} updated and the removed value was recorded.","success")
 return redirect(url_for("assets.detail",asset_id=asset.id))

@bp.route("/<int:asset_id>/documents",methods=["POST"])
@login_required
def upload_document(asset_id):
 asset=db.session.get(Asset,asset_id)
 if not asset:abort(404)
 upload=request.files.get("document")
 if not upload or not upload.filename:flash("Choose a document to upload.","danger");return redirect(url_for("assets.detail",asset_id=asset.id))
 try:original=validate_upload(upload)
 except ValueError as exc:
  flash(str(exc),"danger");return redirect(url_for("assets.detail",asset_id=asset.id))
 folder=Path(current_app.config["DOCUMENT_DIR"])/"asset_documents";folder.mkdir(parents=True,exist_ok=True)
 stored=f"{uuid.uuid4().hex}_{original}";target=folder/stored;upload.save(target)
 doc=AssetDocument(asset=asset,original_name=original,stored_name=stored,content_type=upload.mimetype,size_bytes=target.stat().st_size,uploaded_by=session.get("user_name"))
 db.session.add(doc);db.session.flush();audit("Asset",asset.id,"Document Uploaded",new={"document":original});db.session.commit()
 flash("Document uploaded.","success");return redirect(url_for("assets.detail",asset_id=asset.id))

@bp.route("/documents/<int:document_id>")
@login_required
def document(document_id):
 doc=db.session.get(AssetDocument,document_id)
 if not doc:abort(404)
 folder=Path(current_app.config["DOCUMENT_DIR"])/"asset_documents"
 if not (folder/doc.stored_name).exists():folder=Path(current_app.config["UPLOAD_DIR"])/"asset_documents"
 return send_from_directory(folder,doc.stored_name,as_attachment=True,download_name=doc.original_name)
@bp.route("/employee/<int:employee_id>")
@login_required
def employee_data(employee_id):
 e=db.session.get(Employee,employee_id)
 return jsonify({"name":e.name,"department_id":e.department_id,"location_id":e.location_id,"department":e.department.name,"location":e.location.name}) if e else (jsonify({}),404)

def available_query():
 q=Asset.query.filter(Asset.status=="Available",Asset.employee_id.is_(None))
 mapping={"category":Asset.category_id,"brand":Asset.manufacturer_id,"location":Asset.location_id,"condition":Asset.condition}
 for key,column in mapping.items():
  if request.args.get(key):q=q.filter(column==request.args[key])
 for key,column in [("model",Asset.model),("asset_tag",Asset.asset_tag),("serial",Asset.serial_number),("invoice",Asset.invoice_number)]:
  if request.args.get(key):q=q.filter(column.contains(request.args[key]))
 if request.args.get("purchase_from"):q=q.filter(Asset.purchase_date>=date.fromisoformat(request.args["purchase_from"]))
 if request.args.get("purchase_to"):q=q.filter(Asset.purchase_date<=date.fromisoformat(request.args["purchase_to"]))
 if request.args.get("warranty") == "expiring":q=q.filter(Asset.warranty_expiry.between(date.today(),date.today()+__import__('datetime').timedelta(days=30)))
 return q.order_by(Asset.asset_tag)

@bp.route("/available")
@login_required
def available():
 assets=available_query().all(); fmt=request.args.get("format")
 if fmt in ("csv","excel","pdf"):
  columns=["Asset Tag","Category","Brand","Model","Serial Number","Location","Condition","Warranty","Purchase Date"];rows=[{"Asset Tag":a.asset_tag,"Category":a.category.name,"Brand":a.manufacturer.name if a.manufacturer else "","Model":a.model,"Serial Number":a.serial_number,"Location":a.location.name if a.location else "","Condition":a.condition,"Warranty":a.warranty_expiry,"Purchase Date":a.purchase_date} for a in assets];df=pd.DataFrame(rows,columns=columns);out=io.BytesIO()
  if fmt=="csv":out.write(df.to_csv(index=False).encode());mime="text/csv";name="available_assets.csv"
  elif fmt=="excel":
   with pd.ExcelWriter(out,engine="openpyxl") as writer:df.to_excel(writer,index=False,sheet_name="Available Assets")
   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";name="available_assets.xlsx"
  else:
   data=[["Asset Tag","Category","Brand","Model","Serial","Location","Condition"]]+[[str(row.get(k) or "") for k in ["Asset Tag","Category","Brand","Model","Serial Number","Location","Condition"]] for row in rows];doc=SimpleDocTemplate(out,pagesize=A4);doc.build([Paragraph("Available Assets",getSampleStyleSheet()["Title"]),Spacer(1,12),Table(data,repeatRows=1)]);mime="application/pdf";name="available_assets.pdf"
  out.seek(0);return send_file(out,as_attachment=True,download_name=name,mimetype=mime)
 return render_template("available_assets_v2.html",assets=assets,**refs())

@bp.route("/lost")
@login_required
def lost():
 return render_template("lost_assets.html",assets=Asset.query.filter_by(status="Lost").order_by(Asset.asset_tag).all())

@bp.route("/<int:asset_id>/status")
@login_required
def status_page(asset_id):
 asset=db.session.get(Asset,asset_id)
 if not asset:abort(404)
 active_block=AssetBlock.query.filter_by(asset_id=asset.id,active=True).first()
 active_assignment=AssetAssignment.query.filter_by(asset_id=asset.id,active=True).first()
 restore_target="Assigned" if asset.employee_id and active_assignment else ("Lost" if active_block and active_block.previous_status=="Lost" else "Available")
 return render_template("asset_status.html",asset=asset,active_block=active_block,active_repair=RepairLog.query.filter_by(asset_id=asset.id,status="Sent").order_by(RepairLog.created_at.desc()).first(),status_restore_target=restore_target)

@bp.route("/<int:asset_id>/status/update",methods=["POST"])
@login_required
def quick_status(asset_id):
 asset=db.session.get(Asset,asset_id)
 if not asset:abort(404)
 target=(request.form.get("status") or "").strip()
 reason=required_reason("Status change reason")
 remarks=(request.form.get("remarks") or "").strip() or None
 valid={"Available","Assigned","Blocked","Under Repair","Lost","Scrapped","Disposed"}
 if target not in valid:
  flash("Select a valid asset status.","danger")
  return redirect(request.referrer or url_for("assets.status_page",asset_id=asset.id))
 if target==asset.status:
  flash(f"Asset is already {target}.","info")
  return redirect(request.referrer or url_for("assets.status_page",asset_id=asset.id))
 if not reason:
  return redirect(request.referrer or url_for("assets.status_page",asset_id=asset.id))
 old=asset.status
 active_assignment=AssetAssignment.query.filter_by(asset_id=asset.id,active=True).first()
 active_block=AssetBlock.query.filter_by(asset_id=asset.id,active=True).first()
 active_repair=RepairLog.query.filter_by(asset_id=asset.id,status="Sent").first()
 try:
  if target=="Lost" and old in ("Available","Assigned"):
   if not can("lost.mark"):abort(403)
   asset.status="Lost";event_type="Lost"
   record_event(asset,event_type,from_employee=asset.employee,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=remarks)
  elif old=="Lost" and target in ("Available","Assigned"):
   if not can("lost.recover"):abort(403)
   restored="Assigned" if asset.employee_id and active_assignment else "Available"
   if target!=restored:raise ValueError(f"This asset must recover to {restored} based on its current assignment.")
   asset.status=restored;event_type="Recovered"
   record_event(asset,event_type,to_employee=asset.employee if restored=="Assigned" else None,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=remarks)
  elif target=="Blocked" and old in ("Available","Assigned","Lost"):
   if not can("asset.block"):abort(403)
   if active_block:raise ValueError("This asset is already blocked.")
   block=AssetBlock(asset=asset,reason=reason,blocked_date=date.today(),remarks=remarks,blocked_by=session.get("user_name") or "IT Admin",previous_status=old)
   db.session.add(block);asset.status="Blocked";event_type="Blocked"
   record_event(asset,event_type,from_employee=asset.employee,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=remarks)
  elif old=="Blocked" and target in ("Available","Assigned","Lost"):
   if not can("asset.unblock"):abort(403)
   if not active_block:raise ValueError("The active block record is missing. The status was not changed.")
   restored="Assigned" if asset.employee_id and active_assignment else ("Lost" if active_block.previous_status=="Lost" else "Available")
   if target!=restored:raise ValueError(f"Unblocking restores this asset to {restored}. Select {restored}.")
   active_block.active=False;active_block.unblocked_date=date.today();asset.status=restored;event_type="Unblocked"
   record_event(asset,event_type,to_employee=asset.employee if restored=="Assigned" else None,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=remarks)
  elif old=="Scrapped" and target=="Disposed":
   if not can("asset.scrap"):abort(403)
   asset.status="Disposed";event_type="Disposed"
   record_event(asset,event_type,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=remarks)
  elif target=="Under Repair" and old in ("Available","Assigned"):
   if not can("repair.add"):abort(403)
   if active_repair:raise ValueError("This asset already has an active repair record.")
   prior=(asset.employee,asset.department,asset.location)
   repair=RepairLog(asset=asset,issue_description=reason,repair_date=date.today(),condition_before=asset.condition,remarks=remarks)
   db.session.add(repair);asset.status="Under Repair";event_type="Sent for Repair"
   record_event(asset,event_type,from_employee=asset.employee,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=remarks)
   movement(asset,"Send for Repair",*prior,reason)
  elif target=="Assigned":
   raise ValueError("Use the assignment approval workflow. Assigned status is applied only after an assignment becomes active.")
  elif target=="Available" and old=="Assigned":
   raise ValueError("Return the asset first. The return workflow closes the assignment and marks it Available.")
  elif target=="Under Repair":
   raise ValueError("Only Available or Assigned assets can be sent for repair.")
  elif target=="Scrapped":
   raise ValueError("Use the Scrap workflow. Scrapping requires disposal and approval details.")
  elif old=="Under Repair":
   raise ValueError("Complete the active repair first. Its completion will restore the correct status.")
  elif old=="Disposed":
   raise ValueError("Disposed assets cannot be returned to an operational status.")
  else:
   raise ValueError(f"Changing status from {old} to {target} is not allowed.")
  asset.state_version=(asset.state_version or 0)+1
  audit("Asset",asset.id,event_type,old={"status":old},new={"status":asset.status},reason=reason)
  db.session.commit()
  flash(f"Asset status updated from {old} to {asset.status}.","success")
 except ValueError as exc:
  db.session.rollback();flash(str(exc),"danger")
 return redirect(request.referrer or url_for("assets.profile",system_asset_reference=asset.system_asset_reference))

@bp.route("/<int:asset_id>/history")
@login_required
def history(asset_id):
 asset=db.session.get(Asset,asset_id)
 if not asset:return "Not found",404
 return render_template("asset_history.html",asset=asset,events=AssetLifecycle.query.filter_by(asset_id=asset_id).order_by(AssetLifecycle.occurred_at.desc()).all())

@bp.route("/<int:asset_id>/state",methods=["POST"])
@login_required
def state_change(asset_id):
 asset=db.session.get(Asset,asset_id); action=request.form.get("action");reason=required_reason("Reason")
 recovery_status="Assigned" if asset and asset.employee_id and AssetAssignment.query.filter_by(asset_id=asset.id,active=True).first() else "Available"
 transitions={("Available","lost"):("Lost","Lost"),("Assigned","lost"):("Lost","Lost"),("Lost","recover"):(recovery_status,"Recovered"),("Scrapped","dispose"):("Disposed","Disposed")}
 target=transitions.get((asset.status,action)) if asset else None
 required={"lost":"lost.mark","recover":"lost.recover","dispose":"asset.scrap"}.get(action)
 if not required or not can(required):abort(403)
 if asset and reason and target:
  old=asset.status;asset.status=target[0];asset.state_version=(asset.state_version or 0)+1;record_event(asset,target[1],from_employee=asset.employee if action=="lost" else None,to_employee=asset.employee if action=="recover" and target[0]=="Assigned" else None,condition_before=asset.condition,condition_after=asset.condition,reason=reason,remarks=request.form.get("remarks"));audit("Asset",asset.id,target[1],old={"status":old},new={"status":target[0]},reason=reason);db.session.commit();flash(f"Asset marked {target[0]}.","success")
 else:flash("That lifecycle transition is not allowed.","danger")
 return redirect(url_for("assets.status_page",asset_id=asset_id))

@bp.route("/<int:asset_id>/delete",methods=["POST"])
@login_required
def delete(asset_id):
 asset=db.session.get(Asset,asset_id);reason=required_reason("Deletion reason")
 if not asset:return "Not found",404
 dependencies=AssetAssignment.query.filter_by(asset_id=asset.id).count()+MovementLog.query.filter_by(asset_id=asset.id).count()+RepairLog.query.filter_by(asset_id=asset.id).count()+ScrapLog.query.filter_by(asset_id=asset.id).count()+AssetRequest.query.filter_by(asset_id=asset.id).count()+AssetBlock.query.filter_by(asset_id=asset.id).count()
 lifecycle=AssetLifecycle.query.filter_by(asset_id=asset.id).all()
 if not current_password_valid():flash("Current password is required and was not accepted.","danger")
 elif asset.employee_id:flash("Return or transfer the asset before deletion.","danger")
 elif (dependencies or any(event.event_type!="Asset Added" for event in lifecycle)) and normalized_role()!="Super Admin":abort(403)
 elif (dependencies or any(event.event_type!="Asset Added" for event in lifecycle)) and request.form.get("confirm_reference","").strip()!=asset.display_reference:flash(f"Type {asset.display_reference} exactly to confirm permanent deletion with history.","danger")
 elif reason:
  try:
   had_history=bool(dependencies or any(event.event_type!="Asset Added" for event in lifecycle));permanently_delete_asset(asset,reason);flash("Asset and all related history deleted permanently." if had_history else "Asset deleted.","success");return redirect(url_for("assets.list_assets"))
  except Exception:
   db.session.rollback();current_app.logger.exception("Permanent asset deletion failed for asset id %s",asset_id);flash("The asset could not be deleted. No database changes were saved.","danger")
 return redirect(url_for("assets.detail",asset_id=asset_id))

@bp.route("/blocked",methods=["GET","POST"])
@login_required
def blocked():
 if request.method=="POST":
  asset=db.session.get(Asset,request.form.get("asset_id",type=int)); reason=required_reason("Blocking reason")
  eligible=("Available","Lost","Assigned")
  if asset and reason and asset.status in eligible and not AssetBlock.query.filter_by(asset_id=asset.id,active=True).first():
   try:
    blocked_date=date.fromisoformat(request.form.get("blocked_date") or str(date.today()))
    expected=date.fromisoformat(request.form["expected_unblock_date"]) if request.form.get("expected_unblock_date") else None
    previous=asset.status
    claimed=Asset.query.filter(Asset.id==asset.id,Asset.status==previous,Asset.state_version==asset.state_version).update({"status":"Blocked","state_version":Asset.state_version+1},synchronize_session=False)
    if not claimed:raise ValueError("Another user already changed this asset.")
    db.session.expire_all();asset=db.session.get(Asset,asset.id)
    block=AssetBlock(asset=asset,reason=reason,blocked_date=blocked_date,expected_unblock_date=expected,remarks=request.form.get("remarks"),blocked_by=request.form.get("blocked_by") or session.get("user_name") or "IT Admin",previous_status=previous)
    db.session.add(block);record_event(asset,"Blocked",from_employee=asset.employee,reason=reason,remarks=block.remarks);audit("Asset",asset.id,"Block",old={"status":previous},new={"status":"Blocked","current_employee":asset.employee.name if asset.employee else None},reason=reason);db.session.commit();flash("Asset blocked. Existing ownership is retained; assignment and transfer are disabled until it is unblocked.","success")
   except (ValueError,TypeError) as exc:
    db.session.rollback();flash(str(exc) if str(exc) else "Enter valid block dates.","danger")
  else:flash("Available, Assigned, or Lost assets can be blocked. Under Repair, Scrapped, and Disposed assets are not eligible.","danger")
  return redirect(url_for("assets.blocked"))
 return render_template("blocked_assets_v2.html",available_assets=Asset.query.filter(Asset.status.in_(("Available","Lost","Assigned"))).order_by(Asset.asset_tag).all(),blocks=AssetBlock.query.filter_by(active=True).order_by(AssetBlock.blocked_date.desc()).all())

@bp.route("/<int:asset_id>/block",methods=["POST"])
@login_required
def block_asset(asset_id):
 asset=db.session.get(Asset,asset_id);reason=required_reason("Blocking reason")
 if not asset:return "Not found",404
 if asset.status not in ("Available","Lost","Assigned"):flash("This asset is not eligible for blocking. Complete repair first; scrapped and disposed assets cannot be blocked.","danger")
 elif AssetBlock.query.filter_by(asset_id=asset.id,active=True).first():flash("This asset is already blocked.","danger")
 elif reason:
  try:blocked_date=date.fromisoformat(request.form.get("blocked_date") or str(date.today()))
  except ValueError:flash("Enter a valid blocked date.","danger");return redirect(url_for("assets.detail",asset_id=asset.id))
  previous=asset.status;block=AssetBlock(asset=asset,reason=reason,blocked_date=blocked_date,remarks=request.form.get("remarks","").strip(),blocked_by=session.get("user_name") or "Super Admin",previous_status=previous);asset.status="Blocked";asset.state_version=(asset.state_version or 0)+1;db.session.add(block);record_event(asset,"Blocked",from_employee=asset.employee,reason=reason,remarks=block.remarks);audit("Asset",asset.id,"Block",old={"status":previous},new={"status":"Blocked","current_employee":asset.employee.name if asset.employee else None},reason=reason);db.session.commit();flash("Asset blocked. Existing ownership is retained; assignment and transfer are disabled until it is unblocked.","success")
 return redirect(url_for("assets.status_page",asset_id=asset.id))

@bp.route("/blocked/<int:block_id>/unblock",methods=["POST"])
@login_required
def unblock(block_id):
 block=db.session.get(AssetBlock,block_id);reason=required_reason("Unblock reason")
 if block and block.active and reason:
  active_assignment=AssetAssignment.query.filter_by(asset_id=block.asset_id,active=True).first();restored="Assigned" if block.asset.employee_id and active_assignment else ("Lost" if block.previous_status=="Lost" else "Available")
  block.active=False;block.unblocked_date=date.today();block.asset.status=restored;block.asset.state_version=(block.asset.state_version or 0)+1;record_event(block.asset,"Unblocked",to_employee=block.asset.employee if restored=="Assigned" else None,reason=reason);audit("Asset",block.asset_id,"Unblock",old={"status":"Blocked"},new={"status":restored},reason=reason);db.session.commit();flash(f"Asset unblocked and restored to {restored}.","success")
 return redirect(request.referrer or url_for("assets.blocked"))
