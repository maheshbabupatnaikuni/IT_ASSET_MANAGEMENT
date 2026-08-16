from datetime import date, datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from ..extensions import db
from ..models import AssetRequest, Asset, Employee, AssetCategory, AssetAssignment, User
from ..services.audit import audit, movement
from ..services.lifecycle import record_event
from ..services.lbac import allowed_location_ids, current_user, is_super_admin
from .helpers import login_required, permission_required, normalized_role, can

bp=Blueprint("requests",__name__,url_prefix="/requests")
def utc_now():return datetime.now(timezone.utc).replace(tzinfo=None)

def can_approve_request(item):
 if is_super_admin():return True
 user=current_user();ids=allowed_location_ids()
 location_id=item.requested_for.location_id if item and item.requested_for else None
 return bool(
  user and user.approval_level=="Location Admin" and can("location.approve.own")
  and ids is not None and location_id in ids
 )

@bp.route("/")
@login_required
def index():
 role=normalized_role();query=AssetRequest.query;location_id=request.args.get("location",type=int)
 if not (can("assignment.view") or can("assignment.request.create")):abort(403)
 if location_id:query=query.filter(AssetRequest.requested_for.has(Employee.location_id==location_id))
 available_assets=Asset.query.filter(Asset.status=="Available",Asset.employee_id.is_(None)).order_by(db.func.lower(db.func.coalesce(Asset.hostname,Asset.location_asset_reference)),Asset.location_asset_reference).all()
 return render_template("asset_requests_v3.html",requests=query.order_by(AssetRequest.created_at.desc()).all(),role=role,employees=Employee.query.filter_by(active=True,blocked=False,employment_status="Active").order_by(Employee.name).all(),categories=AssetCategory.query.filter_by(active=True).order_by(AssetCategory.name).all(),assets=available_assets)

@bp.route("/create",methods=["POST"])
@permission_required("assignment.request.create")
def create_request():
 employee=db.session.get(Employee,request.form.get("employee_id",type=int));asset=db.session.get(Asset,request.form.get("asset_id",type=int));reason=request.form.get("reason","").strip()
 if not employee or not reason:flash("Employee and assignment reason are required.","danger")
 elif employee.blocked:flash(f"{employee.name} is currently blocked by {employee.blocked_by or 'IT Admin'} and cannot receive an asset.","danger")
 elif not employee.active or employee.employment_status!="Active":flash(f"{employee.name} is not an active employee and cannot receive an asset.","danger")
 elif not asset:flash("Select an asset.","danger")
 elif asset.location_id!=employee.location_id:flash("Asset and employee must belong to the same location. Use a cross-location transfer request where applicable.","danger")
 elif asset.status=="Blocked":flash(f"{asset.asset_tag} is blocked and cannot be assigned until it is unblocked.","danger")
 elif asset.status!="Available" or asset.employee_id is not None:flash(f"{asset.asset_tag} is {asset.status} and is not eligible for assignment.","danger")
 else:
  item=AssetRequest(requested_for=employee,category=asset.category,request_reason=reason,status="Pending Approval",requested_by_id=session["user_id"],asset=asset,processed_by_id=session["user_id"],processed_at=utc_now(),admin_remarks=request.form.get("remarks","").strip(),assignment_date=date.fromisoformat(request.form.get("assignment_date") or str(date.today())),expected_return_date=date.fromisoformat(request.form["expected_return_date"]) if request.form.get("expected_return_date") else None,condition=request.form.get("condition") or asset.condition,accessories=request.form.get("accessories"),location_validated=True);db.session.add(item);db.session.flush();audit("Asset Request",item.id,"Submitted for Approval",new={"asset":asset.hostname or asset.display_reference,"asset_reference":asset.display_reference,"employee":employee.name},reason=reason);db.session.commit();flash("Assignment request submitted to the Location Admin and Super Admin. The asset remains Available until approval.","success")
 return redirect(url_for("requests.index"))

@bp.route("/<int:item_id>/resubmit",methods=["POST"])
@permission_required("assignment.request.edit")
def resubmit(item_id):
 item=db.session.get(AssetRequest,item_id);asset=db.session.get(Asset,request.form.get("asset_id",type=int))
 if not item or item.status not in ("HR Raised","Rejected"):flash("This request is not available for resubmission.","danger")
 elif not asset or asset.status!="Available" or asset.employee_id is not None:flash("Select an unassigned asset whose current status is Available.","danger")
 elif asset.location_id!=item.requested_for.location_id:flash("Asset and employee must belong to the same location.","danger")
 elif item.requested_for.blocked:flash(f"{item.requested_for.name} is currently blocked by {item.requested_for.blocked_by or 'IT Admin'} and cannot receive an asset.","danger")
 elif not item.requested_for.active or item.requested_for.employment_status!="Active":flash("The employee is no longer eligible for assignment.","danger")
 else:
  item.asset=asset;item.category=asset.category;item.request_reason=request.form.get("reason","").strip() or item.request_reason;item.processed_by_id=session["user_id"];item.processed_at=utc_now();item.admin_remarks=request.form.get("remarks","").strip();item.assignment_date=date.fromisoformat(request.form.get("assignment_date") or str(date.today()));item.expected_return_date=date.fromisoformat(request.form["expected_return_date"]) if request.form.get("expected_return_date") else None;item.condition=request.form.get("condition") or asset.condition;item.accessories=request.form.get("accessories");item.status="Pending Approval";item.decision_remarks=None;item.location_validated=True;audit("Asset Request",item.id,"Resubmitted for Approval",new={"asset":asset.asset_tag},reason=item.request_reason);db.session.commit();flash("Assignment request resubmitted to the Location Admin and Super Admin.","success")
 return redirect(url_for("requests.index"))

@bp.route("/<int:item_id>/cancel",methods=["POST"])
@permission_required("assignment.request.cancel")
def cancel(item_id):
 item=db.session.get(AssetRequest,item_id);reason=request.form.get("reason","").strip()
 if not item or item.status not in ("Pending Approval","Rejected","HR Raised"):
  flash("Only pending or rejected assignment requests can be cancelled.","danger")
 elif not reason:
  flash("Cancellation reason is required.","danger")
 else:
  old=item.status
  claimed=AssetRequest.query.filter(AssetRequest.id==item.id,AssetRequest.status==old).update({"status":"Cancelled","decision_remarks":reason,"state_version":AssetRequest.state_version+1},synchronize_session=False)
  if not claimed:db.session.rollback();flash("Another user already processed this request.","warning")
  else:audit("Asset Request",item.id,"Cancelled",old={"status":old},new={"status":"Cancelled"},reason=reason);db.session.commit();flash("Assignment request cancelled.","success")
 return redirect(url_for("requests.index"))

@bp.route("/<int:item_id>/decide",methods=["POST"])
@login_required
def decide(item_id):
 item=db.session.get(AssetRequest,item_id);decision=request.form.get("decision");remarks=request.form.get("remarks","").strip()
 required={"approve":"assignment.approve","reject":"assignment.reject"}.get(decision)
 if not required or not can(required):abort(403)
 if item and not can_approve_request(item):abort(403)
 if not item or item.status!="Pending Approval":flash("This request is not pending approval.","danger")
 elif decision=="reject":
  if not remarks:flash("Rejection remarks are required.","danger")
  else:
   claimed=AssetRequest.query.filter_by(id=item_id,status="Pending Approval").update({"status":"Rejected","approved_by_id":session["user_id"],"decided_at":utc_now(),"decision_remarks":remarks,"state_version":AssetRequest.state_version+1},synchronize_session=False)
   if not claimed:db.session.rollback();flash("Another user already processed this request.","warning")
   else:audit("Asset Request",item.id,"Rejected",reason=remarks);db.session.commit();flash("Request rejected and returned to Admin.","warning")
 elif decision=="approve":
  asset=item.asset;employee=item.requested_for
  if not asset or asset.status!="Available" or asset.employee_id is not None:flash("Approval failed because the selected asset is no longer Available.","danger")
  elif item.location_validated and asset.location_id!=employee.location_id:flash("Approval failed because asset and employee locations no longer match.","danger")
  elif employee.blocked:flash(f"Approval failed: {employee.name} is currently blocked by {employee.blocked_by or 'IT Admin'}.","danger")
  elif not employee.active or employee.employment_status!="Active":flash("Approval failed because the employee is no longer active.","danger")
  else:
   try:
    claimed=AssetRequest.query.filter_by(id=item_id,status="Pending Approval").update({"status":"Approving","state_version":AssetRequest.state_version+1},synchronize_session=False)
    if not claimed:raise ValueError("Another user already processed this request.")
    assigned_date=item.assignment_date or date.today()
    claimed_asset=Asset.query.filter(Asset.id==asset.id,Asset.status=="Available",Asset.employee_id.is_(None)).update({"employee_id":employee.id,"department_id":employee.department_id,"location_id":employee.location_id,"status":"Assigned","assigned_date":assigned_date,"returned_date":None,"condition":item.condition or asset.condition,"state_version":Asset.state_version+1},synchronize_session=False)
    if not claimed_asset:raise ValueError("Another user assigned or changed this asset.")
    db.session.expire_all();item=db.session.get(AssetRequest,item_id);asset=db.session.get(Asset,item.asset_id);employee=item.requested_for
    if AssetAssignment.query.filter_by(asset_id=asset.id,active=True).first():raise ValueError("An active assignment already exists for this asset.")
    db.session.add(AssetAssignment(asset=asset,employee=employee,assigned_at=datetime.combine(assigned_date,datetime.min.time()),expected_return_date=item.expected_return_date,condition_out=asset.condition,accessories=item.accessories,remarks=item.admin_remarks,active=True))
    item.status="Approved";item.approved_by_id=session["user_id"];item.decided_at=utc_now();item.decision_remarks=remarks
    record_event(asset,"Assigned",to_employee=employee,condition_after=asset.condition,reason="Approved asset request",remarks=item.admin_remarks);movement(asset,"Issue",None,None,None,item.admin_remarks or "Approved request");audit("Asset Request",item.id,"Approved",new={"asset":asset.asset_tag,"employee":employee.name},reason=remarks or "Approved");db.session.commit();flash("Assignment approved and activated.","success")
   except Exception as exc:
    db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else "Approval could not be completed. No records were changed.","danger")
 elif decision!="reject":flash("Choose Approve or Reject.","danger")
 return redirect(url_for("requests.index"))
