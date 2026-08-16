from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
from ..extensions import db
from ..models import Department, Location, Manufacturer, Vendor, AssetCategory, Employee, Asset, AssetAssignment, AssetLifecycle, AssetRequest, CustomAttribute, AuditLog, MovementLog, FieldProvenance, ImportSourceLink, ImportBatch, HiddenProfileField
from ..services.audit import audit
from ..services.lbac import validate_location
from ..services.master_values import resolve_master_id
from .helpers import login_required, current_password_valid, required_reason, normalized_role, can
bp=Blueprint("masters",__name__,url_prefix="/masters")
@bp.before_request
def restrict_master_routes():
 if not normalized_role():return redirect(url_for("auth.login"))
 if request.view_args and request.view_args.get("kind")=="locations" and normalized_role()!="Super Admin":abort(403)
 permissions={"masters.employees":"employee.add" if request.method=="POST" else "employee.view","masters.employee_update":"employee.edit","masters.employee_status_update":"employee.edit","masters.employee_delete":"employee.delete","masters.employee_custom_field":"employee.custom.manage","masters.employee_hide_field":"employee.custom.manage","masters.employee_restore_field":"employee.custom.manage","masters.employee_toggle":"employee.edit","masters.employee_history":"employee.history.view","masters.without_assets":"employee.without_assets.view","masters.blocked_employees":"employee.block","masters.employee_unblock":"employee.unblock","masters.resign":"clearance.complete"}
 required=permissions.get(request.endpoint,"master.manage")
 if not can(required):abort(403)
MODELS={"departments":Department,"locations":Location,"categories":AssetCategory,"manufacturers":Manufacturer,"vendors":Vendor}

def employee_department_id(form):
 department_id=resolve_master_id(Department,form,"department_id",required=False,label="Department")
 if department_id:return department_id
 department=Department.query.filter(db.func.lower(db.func.trim(Department.name))=="unspecified").first()
 if not department:
  department=Department(name="Unspecified",active=True);db.session.add(department);db.session.flush()
  audit("Department",department.id,"Create",new={"name":"Unspecified"},reason="Default for an employee saved without a department")
 return department.id
@bp.route("/<kind>",methods=["GET","POST"])
@login_required
def master_list(kind):
 model=MODELS.get(kind)
 if not model: return "Not found",404
 if kind=="locations":return redirect(url_for("admin.locations"))
 if request.method=="POST":
  name=request.form.get("name","").strip()
  if not current_password_valid(): flash("Current password is required and was not accepted.","danger")
  elif not name: flash("Name is required.","danger")
  elif model.query.filter_by(name=name).first(): flash("This entry already exists.","danger")
  else:
   obj=model(name=name)
   if model is Vendor: obj.contact_person=request.form.get("contact_person"); obj.email=request.form.get("email"); obj.phone=request.form.get("phone"); obj.services=request.form.get("services")
   db.session.add(obj); db.session.flush(); audit(kind,obj.id,"Create",new={"name":name}); db.session.commit(); flash("Saved.","success")
  return redirect(url_for("masters.master_list",kind=kind))
 return render_template("master_list.html",kind=kind,title=kind.replace("_"," ").title(),items=model.query.order_by(model.name).all(),is_vendor=model is Vendor)
@bp.route("/<kind>/<int:item_id>/update",methods=["POST"])
@login_required
def master_update(kind,item_id):
 model=MODELS.get(kind); obj=db.session.get(model,item_id) if model else None
 if not obj: return "Not found",404
 if not current_password_valid(): flash("Current password is required and was not accepted.","danger"); return redirect(url_for("masters.master_list",kind=kind))
 reason=required_reason("Reason for update")
 if not reason:return redirect(url_for("masters.master_list",kind=kind))
 old=obj.name; obj.name=request.form.get("name",obj.name).strip(); obj.active=request.form.get("active")=="on"
 if model is Vendor: obj.contact_person=request.form.get("contact_person"); obj.email=request.form.get("email"); obj.phone=request.form.get("phone"); obj.services=request.form.get("services")
 audit(kind,item_id,"Update",old={"name":old},new={"name":obj.name},reason=reason); db.session.commit(); flash("Updated.","success")
 return redirect(url_for("masters.master_list",kind=kind))
@bp.route("/<kind>/<int:item_id>/delete",methods=["POST"])
@login_required
def master_delete(kind,item_id):
 model=MODELS.get(kind); obj=db.session.get(model,item_id) if model else None
 if not obj:return "Not found",404
 if not current_password_valid(): flash("Current password is required and was not accepted.","danger"); return redirect(url_for("masters.master_list",kind=kind))
 reason=required_reason("Reason for deletion")
 if not reason:return redirect(url_for("masters.master_list",kind=kind))
 try: audit(kind,item_id,"Delete",old={"name":obj.name},reason=reason); db.session.delete(obj); db.session.commit(); flash("Deleted.","success")
 except Exception: db.session.rollback(); flash("Cannot delete an entry currently in use; deactivate it instead.","danger")
 return redirect(url_for("masters.master_list",kind=kind))
@bp.route("/employees",methods=["GET","POST"])
@login_required
def employees():
 if request.method=="POST":
  if not current_password_valid(): flash("Current password is required and was not accepted.","danger"); return redirect(url_for("masters.employees"))
  try:
   code=request.form.get("employee_code","").strip() or None
   if code and Employee.query.filter(db.func.lower(Employee.employee_code)==code.lower()).first():raise ValueError("Employee ID already exists.")
   location=validate_location(request.form.get("location_id"))
   department_id=employee_department_id(request.form)
   e=Employee(employee_code=code,name=request.form["name"],email=request.form.get("email"),phone=request.form.get("phone"),designation=request.form.get("designation"),department_id=department_id,location_id=location.id); db.session.add(e); db.session.flush(); audit("Employee",e.id,"Create",new={"name":e.name,"employee_code":e.employee_code});db.session.commit();flash(f"Employee added. Employee ID: {e.employee_code}","success")
  except Exception as exc: db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else "Employee code must be unique and all required fields completed.","danger")
  return redirect(url_for("masters.employees"))
 q=request.args.get("q","");asset_filter=request.args.get("asset_filter","");status_filter=request.args.get("status","").title();location_id=request.args.get("location",type=int);query=Employee.query
 if q: query=query.filter((Employee.name.contains(q))|(Employee.employee_code.contains(q)))
 if location_id:query=query.filter(Employee.location_id==location_id)
 if status_filter in ("Active","Inactive"):query=query.filter(Employee.employment_status==status_filter,Employee.active==(status_filter=="Active"))
 if asset_filter=="without":
  query=query.outerjoin(Asset,(Asset.employee_id==Employee.id)&(Asset.status=="Assigned")).filter(Asset.id.is_(None))
 return render_template("employees.html",employees=query.order_by(Employee.name).all(),departments=Department.query.filter_by(active=True).all(),locations=Location.query.filter_by(active=True).all(),q=q,asset_filter=asset_filter,status_filter=status_filter,location_id=location_id)
@bp.route("/employees/<int:item_id>/toggle",methods=["POST"])
@login_required
def employee_toggle(item_id):
 e=db.session.get(Employee,item_id)
 if not e:return "Not found",404
 if not current_password_valid():flash("Current password is required and was not accepted.","danger");return redirect(url_for("masters.employees"))
 reason=required_reason("Reason for status change")
 if reason:
  new_status="Inactive" if e.active and e.employment_status=="Active" else "Active"
  old={"employment_status":e.employment_status,"active":e.active,"blocked":e.blocked}
  e.employment_status=new_status;e.active=new_status=="Active"
  if new_status=="Inactive":e.blocked=False;e.blocked_reason=None;e.blocked_date=None;e.blocked_by=None
  audit("Employee",e.id,"Employment Status Changed",old=old,new={"employment_status":new_status,"active":e.active,"blocked":e.blocked},reason=reason);db.session.commit()
 return redirect(url_for("masters.employees"))

@bp.route("/employees/<int:item_id>/update",methods=["POST"])
@login_required
def employee_update(item_id):
 e=db.session.get(Employee,item_id)
 if not e:return "Not found",404
 if not current_password_valid():
  flash("Current password is required and was not accepted.","danger")
  return redirect(url_for("masters.employees"))
 reason=required_reason("Reason for employee update")
 if not reason:return redirect(url_for("masters.employees"))
 try:
  old={"employee_code":e.employee_code,"name":e.name,"department":e.department.name,"location":e.location.name,"employment_status":e.employment_status,"active":e.active,"blocked":e.blocked}
  submitted_code=request.form.get("employee_code","").strip()
  e.employee_code=submitted_code or e.employee_code
  e.name=request.form.get("name","").strip()
  e.designation=request.form.get("designation","").strip() or None
  e.email=request.form.get("email","").strip() or None
  e.phone=request.form.get("phone","").strip() or None
  e.department_id=employee_department_id(request.form)
  new_location=validate_location(request.form.get("location_id",type=int))
  if new_location.id!=e.location_id and Asset.query.filter_by(employee_id=e.id).count():raise ValueError("An employee holding assets cannot be moved directly. Use the asset transfer workflow.")
  e.location_id=new_location.id
  new_status=request.form.get("employment_status",e.employment_status).strip().title()
  if new_status not in ("Active","Inactive"):raise ValueError("Employee status must be Active or Inactive.")
  e.employment_status=new_status;e.active=new_status=="Active"
  if new_status=="Inactive":
   e.blocked=False;e.blocked_reason=None;e.blocked_date=None;e.blocked_by=None;e.unblocked_date=__import__('datetime').date.today()
  if not e.name or not e.location_id:raise ValueError()
  if Employee.query.filter(db.func.lower(Employee.employee_code)==e.employee_code.lower(),Employee.id!=e.id).first():raise ValueError("Employee ID already exists.")
  audit("Employee",e.id,"Update",old=old,new={"employee_code":e.employee_code,"name":e.name,"department_id":e.department_id,"location_id":e.location_id,"employment_status":e.employment_status,"active":e.active,"blocked":e.blocked},reason=reason)
  db.session.commit();flash("Employee details updated.","success")
 except Exception as exc:
  db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) and str(exc) else "Could not update employee. Employee ID must be unique and required fields must be completed.","danger")
 return redirect(url_for("masters.employees"))

@bp.route("/employees/<int:item_id>/status",methods=["POST"])
@login_required
def employee_status_update(item_id):
 e=db.session.get(Employee,item_id)
 if not e:abort(404)
 if not current_password_valid():flash("Current password is required and was not accepted.","danger");return redirect(url_for("masters.employee_history",item_id=e.id))
 reason=required_reason("Reason for employment status change")
 if not reason:return redirect(url_for("masters.employee_history",item_id=e.id))
 new_status=request.form.get("employment_status","").strip().title()
 if new_status not in ("Active","Inactive"):flash("Employee status must be Active or Inactive.","danger");return redirect(url_for("masters.employee_history",item_id=e.id))
 old={"employment_status":e.employment_status,"active":e.active,"blocked":e.blocked,"blocked_reason":e.blocked_reason}
 e.employment_status=new_status;e.active=new_status=="Active"
 if new_status=="Inactive":
  e.blocked=False;e.blocked_reason=None;e.blocked_date=None;e.blocked_by=None;e.unblocked_date=__import__('datetime').date.today()
 audit("Employee",e.id,"Employment Status Changed",old=old,new={"employment_status":new_status,"active":e.active,"blocked":e.blocked},reason=reason)
 db.session.commit();flash(f"{e.name} is now {new_status}.","success")
 return redirect(url_for("masters.employee_history",item_id=e.id))

@bp.route("/employees/<int:item_id>/delete",methods=["POST"])
@login_required
def employee_delete(item_id):
 e=db.session.get(Employee,item_id)
 if not e:abort(404)
 if not current_password_valid():
  flash("Current password is required and was not accepted.","danger")
  return redirect(url_for("masters.employee_history",item_id=e.id))
 reason=required_reason("Reason for employee deletion")
 if not reason:return redirect(url_for("masters.employee_history",item_id=e.id))
 dependencies=(
  Asset.query.filter_by(employee_id=e.id).count()
  + AssetAssignment.query.filter_by(employee_id=e.id).count()
  + AssetLifecycle.query.filter((AssetLifecycle.from_employee_id==e.id)|(AssetLifecycle.to_employee_id==e.id)).count()
  + AssetRequest.query.filter_by(requested_for_id=e.id).count()
  + ImportSourceLink.query.filter_by(employee_id=e.id).count()
 )
 if dependencies:
  flash("Cannot delete an employee with asset, request, lifecycle, or import history. Deactivate the employee instead.","danger")
 else:
  old={"employee_code":e.employee_code,"name":e.name};CustomAttribute.query.filter_by(employee_id=e.id).delete();HiddenProfileField.query.filter_by(employee_id=e.id).delete();db.session.delete(e);audit("Employee",item_id,"Delete",old=old,reason=reason);db.session.commit();flash("Employee deleted.","success")
  return redirect(url_for("masters.employees"))
 return redirect(url_for("masters.employee_history",item_id=e.id))

@bp.route("/employees/without-assets")
@login_required
def without_assets():
 return redirect(url_for("masters.employees",asset_filter="without"))

@bp.route("/employees/blocked",methods=["GET","POST"])
@login_required
def blocked_employees():
 if request.method=="POST":
  e=db.session.get(Employee,request.form.get("employee_id",type=int));reason=required_reason("Blocking reason")
  if e and (not e.active or e.employment_status!="Active"):flash("Inactive employees cannot be blocked. Their employment status already prevents new assignments.","danger")
  elif e and reason and not e.blocked:e.blocked=True;e.blocked_reason=reason;e.blocked_date=__import__('datetime').date.today();e.blocked_by=request.form.get("blocked_by") or "IT Admin";audit("Employee",e.id,"Block",reason=reason);db.session.commit();flash("Employee blocked from new assignments.","success")
  return redirect(url_for("masters.blocked_employees"))
 return render_template("blocked_employees.html",eligible=Employee.query.filter_by(blocked=False,active=True,employment_status="Active").order_by(Employee.name).all(),employees=Employee.query.filter_by(blocked=True,active=True,employment_status="Active").order_by(Employee.name).all())

@bp.route("/employees/<int:item_id>/unblock",methods=["POST"])
@login_required
def employee_unblock(item_id):
 e=db.session.get(Employee,item_id);reason=required_reason("Unblock reason")
 if e and e.blocked and reason:e.blocked=False;e.unblocked_date=__import__('datetime').date.today();audit("Employee",e.id,"Unblock",reason=reason);db.session.commit();flash("Employee unblocked.","success")
 return redirect(url_for("masters.blocked_employees"))

@bp.route("/employees/<int:item_id>/history")
@login_required
def employee_history(item_id):
 e=db.session.get(Employee,item_id)
 if not e:abort(404)
 assignments=AssetAssignment.query.filter_by(employee_id=e.id).order_by(AssetAssignment.assigned_at.desc()).all();events=AssetLifecycle.query.filter((AssetLifecycle.from_employee_id==e.id)|(AssetLifecycle.to_employee_id==e.id)).order_by(AssetLifecycle.occurred_at.desc()).all()
 links=ImportSourceLink.query.filter_by(employee_id=e.id).order_by(ImportSourceLink.created_at.desc()).all();batch_ids={x.batch_id for x in links};batches={b.id:b for b in ImportBatch.query.filter(ImportBatch.id.in_(batch_ids)).all()} if batch_ids else {}
 hidden=HiddenProfileField.query.filter_by(employee_id=e.id).order_by(HiddenProfileField.field_label).all()
 return render_template("employee_profile_v2.html",employee=e,current=[a for a in assignments if a.active],previous=[a for a in assignments if not a.active],assignments=assignments,returns=[x for x in events if x.event_type=="Returned"],transfers=[x for x in events if x.event_type=="Transferred"],events=events,last_activity=events[0].occurred_at if events else None,pending_requests=AssetRequest.query.filter_by(requested_for_id=e.id).filter(AssetRequest.status.in_(("Pending Approval","Rejected"))).order_by(AssetRequest.created_at.desc()).all(),custom_fields=CustomAttribute.query.filter_by(employee_id=e.id).order_by(CustomAttribute.name).all(),audit_rows=AuditLog.query.filter_by(entity="Employee",entity_id=str(e.id)).order_by(AuditLog.timestamp.desc()).all(),sources=FieldProvenance.query.filter_by(employee_id=e.id).order_by(FieldProvenance.updated_at.desc()).all(),import_links=links,batches=batches,hidden_fields=hidden,hidden_codes={x.field_code for x in hidden})

@bp.route("/employees/<int:item_id>/custom-fields",methods=["POST"])
@login_required
def employee_custom_field(item_id):
 employee=db.session.get(Employee,item_id)
 if not employee:abort(404)
 field_id=request.form.get("field_id",type=int);item=db.session.get(CustomAttribute,field_id) if field_id else None
 if item and item.employee_id!=employee.id:abort(400)
 if request.form.get("action")=="delete":
  if not item:abort(400)
  old={"name":item.name,"value":item.value};db.session.delete(item);audit("Employee",employee.id,"Custom Field Removed",old=old,reason=request.form.get("reason"));db.session.commit();flash("Custom field removed.","success")
  return redirect(url_for("masters.employee_history",item_id=employee.id))
 name=request.form.get("name","").strip();value=request.form.get("value","").strip()
 if not name:flash("Custom field name is required.","danger")
 else:
  duplicate=CustomAttribute.query.filter(CustomAttribute.employee_id==employee.id,db.func.lower(CustomAttribute.name)==name.lower())
  if item:duplicate=duplicate.filter(CustomAttribute.id!=item.id)
  if duplicate.first():flash("This employee already has a custom field with that name.","danger")
  else:
   old={"name":item.name,"value":item.value} if item else None
   if not item:item=CustomAttribute(employee=employee,created_by=session.get("user_name"));db.session.add(item)
   item.name=name;item.value=value;db.session.flush();audit("Employee",employee.id,"Custom Field Updated" if old else "Custom Field Added",old=old,new={"name":name,"value":value},reason=request.form.get("reason"));db.session.commit();flash("Custom field saved.","success")
 return redirect(url_for("masters.employee_history",item_id=employee.id))

EMPLOYEE_REMOVABLE_FIELDS={"phone":"Phone Number","email":"Email Address","designation":"Designation"}
@bp.route("/employees/<int:item_id>/fields/hide",methods=["POST"])
@login_required
def employee_hide_field(item_id):
 employee=db.session.get(Employee,item_id);field_code=request.form.get("field_code","")
 if not employee:abort(404)
 if field_code not in EMPLOYEE_REMOVABLE_FIELDS:abort(400)
 if HiddenProfileField.query.filter_by(employee_id=employee.id,field_code=field_code).first():flash("That field is already removed from this employee.","warning")
 else:
  old_value=getattr(employee,field_code);reason=request.form.get("reason","").strip() or "Field no longer required"
  db.session.add(HiddenProfileField(employee=employee,field_code=field_code,field_label=EMPLOYEE_REMOVABLE_FIELDS[field_code],removed_value=str(old_value) if old_value is not None else None,reason=reason,removed_by=session.get("user_name")));setattr(employee,field_code,None)
  audit("Employee",employee.id,"Standard Field Removed",old={field_code:old_value},new={field_code:None},reason=reason);db.session.commit();flash(f"{EMPLOYEE_REMOVABLE_FIELDS[field_code]} removed from this employee. The previous value remains in audit history.","success")
 return redirect(url_for("masters.employee_history",item_id=employee.id))

@bp.route("/employees/<int:item_id>/fields/<int:field_id>/restore",methods=["POST"])
@login_required
def employee_restore_field(item_id,field_id):
 employee=db.session.get(Employee,item_id);item=db.session.get(HiddenProfileField,field_id)
 if not employee or not item:abort(404)
 if item.employee_id!=employee.id or item.field_code not in EMPLOYEE_REMOVABLE_FIELDS:abort(400)
 label=item.field_label;restored=request.form.get("value",item.removed_value or "").strip() or None;setattr(employee,item.field_code,restored);audit("Employee",employee.id,"Standard Field Restored",old={item.field_code:None},new={item.field_code:restored},reason=request.form.get("reason") or "Field restored");db.session.delete(item);db.session.commit();flash(f"{label} restored.","success")
 return redirect(url_for("masters.employee_history",item_id=employee.id))

@bp.route("/employees/<int:item_id>/resign",methods=["POST"])
@login_required
def resign(item_id):
 e=db.session.get(Employee,item_id)
 if not e:return "Not found",404
 if Asset.query.filter_by(employee_id=e.id).count():flash("Resignation cannot be completed until all assigned assets are returned, transferred, marked lost, damaged, or pending recovery.","danger")
 else:e.employment_status="Inactive";e.active=False;e.blocked=False;e.blocked_reason=None;e.blocked_date=None;e.blocked_by=None;e.it_clearance_status="Cleared";audit("Employee",e.id,"IT Clearance",new={"status":"Cleared","employment_status":"Inactive"},reason=request.form.get("reason") or "Resignation");db.session.commit();flash("Employee marked Inactive and IT clearance completed.","success")
 return redirect(url_for("masters.employee_history",item_id=e.id))
