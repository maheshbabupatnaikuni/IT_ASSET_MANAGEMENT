from datetime import date, datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
import json
from ..extensions import db
from ..models import Asset, Employee, Vendor, RepairLog, MovementLog, ScrapLog, AssetAssignment, AssetBlock, ApprovalRequest
from ..services.audit import audit, movement
from .helpers import login_required, required_reason, normalized_role, can, current_password_valid
from ..services.lifecycle import record_event
from ..services.lbac import allowed_location_ids, is_super_admin, current_user
from ..services.approval_workflow import action_label, replay_approved_request, request_target, submitted_form_rows
bp=Blueprint("operations",__name__,url_prefix="/operations")
def utc_now():return datetime.now(timezone.utc).replace(tzinfo=None)

def can_review_action(item=None):
 user=current_user()
 if is_super_admin():return True
 ids=allowed_location_ids()
 return bool(
  user and user.approval_level=="Location Admin" and can("location.approve.own")
  and ids is not None and (item is None or item.location_id in ids)
 )

@bp.before_request
def restrict_operations():
 if not normalized_role():return redirect(url_for("auth.login"))
 permissions={"operations.return_asset":"assignment.return","operations.transfer":"assignment.transfer","operations.location_approvals":"location.approve.own","operations.decide_location_transfer":"location.approve.own","operations.action_approvals":"location.approve.own","operations.action_request_detail":"dashboard.view","operations.decide_action_request":"location.approve.own","operations.my_requests":"dashboard.view","operations.repairs":"repair.add" if request.method=="POST" else "repair.view","operations.edit_repair":"repair.edit","operations.receive_repair":"repair.close","operations.movements":"assignment.history.view","operations.scrap":"asset.scrap","operations.issue":"assignment.request.create"}
 if request.endpoint in ("operations.action_approvals","operations.decide_action_request"):
  if not can_review_action():abort(403)
  return None
 if request.endpoint in ("operations.location_approvals","operations.decide_location_transfer") and (can("location.approve.all") or can("location.approve.own")):return None
 if not can(permissions.get(request.endpoint,"asset.view")):abort(403)
@bp.route("/issue",methods=["GET","POST"])
@login_required
def issue():
 flash("Direct assignment is disabled. Use the HR → Admin → Super Admin request workflow.","warning")
 return redirect(url_for("requests.index"))
@bp.route("/return",methods=["GET","POST"])
@login_required
def return_asset():
 if request.method=="POST":
  a=db.session.get(Asset,request.form.get("asset_id",type=int))
  assignment=AssetAssignment.query.filter_by(asset_id=a.id,active=True).order_by(AssetAssignment.assigned_at.desc()).first() if a else None
  if not a or a.status!="Assigned" or not a.employee_id or not assignment:flash("Choose an asset with a valid active assignment. Already returned assets cannot be returned again.","danger")
  else:
   try:
    old=(a.employee,a.department,a.location);old_condition=a.condition;return_date=date.fromisoformat(request.form.get("return_date") or str(date.today()));new_condition=request.form.get("condition") or a.condition
    claimed=Asset.query.filter(Asset.id==a.id,Asset.status=="Assigned",Asset.employee_id==a.employee_id,Asset.state_version==a.state_version).update({"employee_id":None,"department_id":None,"status":"Available","returned_date":return_date,"condition":new_condition,"state_version":Asset.state_version+1},synchronize_session=False)
    closed=AssetAssignment.query.filter_by(id=assignment.id,active=True).update({"active":False,"returned_at":datetime.combine(return_date,datetime.min.time()),"condition_in":new_condition,"accessories_returned":request.form.get("accessories_returned")},synchronize_session=False)
    if not claimed or not closed:raise ValueError("Another user already changed or returned this asset.")
    db.session.expire_all();a=db.session.get(Asset,a.id);record_event(a,"Returned",from_employee=old[0],condition_before=old_condition,condition_after=a.condition,remarks=request.form.get("remarks"));movement(a,"Return",*old,request.form.get("remarks", ""));audit("Asset",a.id,"Return");db.session.commit();flash("Asset returned.","success")
   except Exception as exc:db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else "Return failed. No records were changed.","danger")
  return redirect(url_for("operations.return_asset"))
 return render_template("operation.html",mode="return",assets=Asset.query.filter(Asset.status=="Assigned",Asset.employee_id.isnot(None)).order_by(Asset.asset_tag).all())
@bp.route("/transfer",methods=["GET","POST"])
@login_required
def transfer():
 if request.method=="POST":
  reason=required_reason("Reason for transfer")
  a=db.session.get(Asset,request.form.get("asset_id",type=int)); e=db.session.get(Employee,request.form.get("employee_id",type=int))
  if not reason:pass
  elif not a or not e:flash("Choose an assigned asset and a destination employee.","danger")
  elif e.blocked:flash(f"{e.name} is currently blocked by {e.blocked_by or 'IT Admin'} and cannot receive an asset.","danger")
  elif not e.active or e.employment_status!="Active":flash(f"{e.name} is not an active employee and cannot receive an asset.","danger")
  elif a.status=="Blocked":flash(f"{a.asset_tag} is blocked and cannot be transferred until it is unblocked.","danger")
  elif a.status!="Assigned" or not a.employee_id or a.employee_id==e.id:flash("Choose an assigned asset and a different eligible employee.","danger")
  elif a.location_id!=e.location_id:
   if not can("location.transfer"):abort(403)
   pending=ApprovalRequest.query.filter_by(module="Cross-Location Transfer",action="Transfer",entity_id=str(a.id),status="Pending").first()
   if pending:flash("A cross-location transfer request is already pending for this asset.","warning")
   else:
    item=ApprovalRequest(module="Cross-Location Transfer",action="Transfer",entity_id=str(a.id),requested_by=session.get("user_name","System"),reason=reason,payload=json.dumps({"employee_id":e.id,"asset_version":a.state_version,"accessories":request.form.get("accessories")}),location_id=a.location_id,target_location_id=e.location_id)
    db.session.add(item);db.session.flush();audit("Approval Request",item.id,"Submitted",new={"asset":a.asset_tag,"employee":e.name,"from_location":a.location.name if a.location else None,"to_location":e.location.name},reason=reason,location_id=a.location_id);db.session.commit();flash("Cross-location transfer submitted for Location Admin approval. No asset data changed.","success")
  else:
   prior=AssetAssignment.query.filter_by(asset_id=a.id,active=True).order_by(AssetAssignment.assigned_at.desc()).first()
   if not prior:flash("Transfer requires an active assignment.","danger")
   else:
    try:
     if not Employee.query.filter_by(id=e.id,active=True,blocked=False,employment_status="Active").first():raise ValueError(f"{e.name} became blocked or inactive before the transfer completed.")
     old=(a.employee,a.department,a.location);now=utc_now()
     claimed=Asset.query.filter(Asset.id==a.id,Asset.status=="Assigned",Asset.employee_id==a.employee_id,Asset.state_version==a.state_version).update({"employee_id":e.id,"department_id":e.department_id,"location_id":e.location_id,"state_version":Asset.state_version+1},synchronize_session=False)
     closed=AssetAssignment.query.filter_by(id=prior.id,active=True).update({"active":False,"returned_at":now},synchronize_session=False)
     if not claimed or not closed:raise ValueError("Another user already changed this assignment.")
     db.session.expire_all();a=db.session.get(Asset,a.id);e=db.session.get(Employee,e.id);db.session.add(AssetAssignment(asset=a,employee=e,assigned_at=now,condition_out=a.condition,accessories=request.form.get("accessories"),remarks=reason));record_event(a,"Transferred",from_employee=old[0],to_employee=e,condition_before=a.condition,condition_after=a.condition,reason=reason);movement(a,"Transfer",*old,reason);audit("Asset",a.id,"Transfer",old={"employee":old[0].name if old[0] else None},new={"employee":e.name},reason=reason);db.session.commit();flash("Asset transferred.","success")
    except Exception as exc:db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else "Transfer failed. No records were changed.","danger")
  return redirect(url_for("operations.transfer"))
 return render_template("operation.html",mode="transfer",assets=Asset.query.filter(Asset.status=="Assigned",Asset.employee_id.isnot(None)).order_by(Asset.asset_tag).all(),employees=Employee.query.filter_by(active=True,blocked=False,employment_status="Active").order_by(Employee.name).all())

def execute_transfer(a,e,reason,accessories=None):
 prior=AssetAssignment.query.filter_by(asset_id=a.id,active=True).order_by(AssetAssignment.assigned_at.desc()).first()
 if not prior:raise ValueError("Transfer requires an active assignment.")
 if e.blocked or not e.active or e.employment_status!="Active":raise ValueError(f"{e.name} is blocked or inactive.")
 if a.status!="Assigned" or not a.employee_id or a.employee_id==e.id:raise ValueError("The asset no longer has an eligible active assignment.")
 old=(a.employee,a.department,a.location);now=utc_now()
 claimed=Asset.query.filter(Asset.id==a.id,Asset.status=="Assigned",Asset.employee_id==a.employee_id,Asset.state_version==a.state_version).update({"employee_id":e.id,"department_id":e.department_id,"location_id":e.location_id,"state_version":Asset.state_version+1},synchronize_session=False)
 closed=AssetAssignment.query.filter_by(id=prior.id,active=True).update({"active":False,"returned_at":now},synchronize_session=False)
 if not claimed or not closed:raise ValueError("Another user already changed this assignment.")
 db.session.flush();db.session.expire_all();a=db.session.get(Asset,a.id);e=db.session.get(Employee,e.id)
 db.session.add(AssetAssignment(asset=a,employee=e,assigned_at=now,condition_out=a.condition,accessories=accessories,remarks=reason))
 record_event(a,"Transferred",from_employee=old[0],to_employee=e,condition_before=a.condition,condition_after=a.condition,reason=reason)
 movement(a,"Transfer",*old,reason);audit("Asset",a.id,"Transfer",old={"employee":old[0].name if old[0] else None,"location":old[2].name if old[2] else None},new={"employee":e.name,"location":e.location.name},reason=reason,location_id=e.location_id)

@bp.route("/location-approvals")
@login_required
def location_approvals():
 if not (is_super_admin() or can("location.approve.all") or can("location.approve.own")):abort(403)
 rows=ApprovalRequest.query.filter_by(module="Cross-Location Transfer").order_by(ApprovalRequest.created_at.desc()).all()
 return render_template("location_approvals.html",requests=rows)

@bp.route("/location-approvals/<int:request_id>/decide",methods=["POST"])
@login_required
def decide_location_transfer(request_id):
 item=db.session.get(ApprovalRequest,request_id);decision=request.form.get("decision");notes=request.form.get("remarks","").strip()
 if not item or item.module!="Cross-Location Transfer" or item.status!="Pending":flash("This location request is no longer pending.","warning");return redirect(url_for("operations.location_approvals"))
 ids=allowed_location_ids() or ()
 if not (is_super_admin() or can("location.approve.all") or (can("location.approve.own") and item.location_id in ids and item.target_location_id in ids)):abort(403)
 if item.requested_by==session.get("user_name") and not is_super_admin():flash("A different Location Admin must approve this request.","danger");return redirect(url_for("operations.location_approvals"))
 if decision not in ("approve","reject"):flash("Choose Approve or Reject.","danger")
 elif not notes:flash("Approval or rejection remarks are required.","danger")
 elif decision=="reject":
  item.status="Rejected";item.reviewed_by=session.get("user_name");item.reviewed_at=utc_now();item.review_notes=notes;audit("Approval Request",item.id,"Rejected",reason=notes,location_id=item.location_id);db.session.commit();flash("Cross-location transfer rejected.","warning")
 else:
  try:
   data=json.loads(item.payload or "{}");a=db.session.get(Asset,int(item.entity_id));e=db.session.get(Employee,int(data["employee_id"]))
   if not a or not e:raise ValueError("The asset or destination employee is no longer accessible.")
   if a.state_version!=data.get("asset_version"):raise ValueError("The asset changed after this request was submitted. Submit a new request.")
   execute_transfer(a,e,item.reason,data.get("accessories"));item.status="Approved";item.reviewed_by=session.get("user_name");item.reviewed_at=utc_now();item.review_notes=notes;audit("Approval Request",item.id,"Approved",reason=notes,location_id=item.target_location_id);db.session.commit();flash("Cross-location transfer approved and completed atomically.","success")
  except Exception as exc:db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else "Approval failed. No records were changed.","danger")
 return redirect(url_for("operations.location_approvals"))

@bp.route("/action-requests")
@login_required
def action_approvals():
 if not can_review_action():abort(403)
 query=ApprovalRequest.query.filter_by(module="Location Action");location_id=request.args.get("location",type=int)
 if location_id:query=query.filter(ApprovalRequest.location_id==location_id)
 rows=query.order_by(ApprovalRequest.created_at.desc()).all()
 return render_template("action_approvals.html",requests=rows,approval_mode=True,action_label=action_label)

@bp.route("/my-requests")
@login_required
def my_requests():
 user=current_user()
 rows=ApprovalRequest.query.filter_by(module="Location Action",requested_by=user.full_name).order_by(ApprovalRequest.created_at.desc()).all()
 return render_template("action_approvals.html",requests=rows,approval_mode=False,action_label=action_label)

@bp.route("/action-requests/<int:request_id>")
@login_required
def action_request_detail(request_id):
 item=db.session.get(ApprovalRequest,request_id)
 if not item or item.module!="Location Action":abort(404)
 user=current_user()
 approval_mode=can_review_action(item)
 if not approval_mode and (not user or item.requested_by!=user.full_name):abort(403)
 return render_template(
  "action_approval_detail.html",item=item,approval_mode=approval_mode,
  action_name=action_label(item.action),target_name=request_target(item),
  submitted_rows=submitted_form_rows(item),
 )

@bp.route("/action-requests/<int:request_id>/decide",methods=["POST"])
@login_required
def decide_action_request(request_id):
 item=db.session.get(ApprovalRequest,request_id);decision=request.form.get("decision");notes=request.form.get("remarks","").strip()
 if not item or item.module!="Location Action" or item.status!="Pending":flash("This action request is no longer pending.","warning");return redirect(url_for("operations.action_approvals"))
 if not can_review_action(item):abort(403)
 return_to=url_for("operations.action_request_detail",request_id=item.id) if request.form.get("return_to")=="detail" else url_for("operations.action_approvals")
 if decision not in ("approve","reject"):flash("Choose Approve or Reject.","danger")
 elif not notes:flash("Approval or rejection remarks are required.","danger")
 elif decision=="approve" and not current_password_valid():flash("Enter your current password to approve and execute this request.","danger")
 elif decision=="reject":
  item.status="Rejected";item.reviewed_by=session.get("user_name");item.reviewed_at=utc_now();item.review_notes=notes;audit("Approval Request",item.id,"Rejected",reason=notes,location_id=item.location_id);db.session.commit();flash("Action request rejected.","warning")
 else:
  try:
   item_id=item.id;replay_approved_request(item,request.form.get("current_password",""))
   item=db.session.get(ApprovalRequest,item_id)
   item.status="Approved";item.reviewed_by=session.get("user_name");item.reviewed_at=utc_now();item.review_notes=notes
   audit("Approval Request",item.id,"Approved and Executed",new={"action":item.action},reason=notes,location_id=item.location_id);db.session.commit();flash("Request approved and the original operation completed.","success")
  except Exception as exc:
   db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else "Approval failed. No live records were changed.","danger")
 return redirect(return_to)
@bp.route("/repairs",methods=["GET","POST"])
@login_required
def repairs():
 if request.method=="POST":
  reason=required_reason("Reason for repair")
  a=db.session.get(Asset,request.form.get("asset_id",type=int)); v=db.session.get(Vendor,request.form.get("vendor_id",type=int)) if request.form.get("vendor_id") else None
  issue_description=request.form.get("issue_description","").strip()
  if reason and a and a.status in ("Available","Assigned") and issue_description:
   old=(a.employee,a.department,a.location);a.status="Under Repair";r=RepairLog(asset=a,vendor=v,issue_description=issue_description,repair_cost=request.form.get("repair_cost",0,type=float) or 0,repair_date=date.fromisoformat(request.form.get("repair_date") or str(date.today())),remarks=request.form.get("remarks"),invoice_number=request.form.get("invoice_number"),parts_changed=request.form.get("parts_changed"),payment_type=request.form.get("payment_type"),condition_before=a.condition);db.session.add(r);record_event(a,"Sent for Repair",from_employee=a.employee,condition_before=a.condition,reason=reason,remarks=r.remarks);movement(a,"Send for Repair",*old,reason);audit("Repair",a.id,"Send",new={"status":"Under Repair"},reason=reason);db.session.commit();flash("Repair record created.","success")
  elif reason:flash("Only available or assigned assets can be sent for repair.","danger")
  return redirect(url_for("operations.repairs"))
 location_id=request.args.get("location",type=int);assets_query=Asset.query.filter(Asset.status.in_(("Available","Assigned")));repairs_query=RepairLog.query
 if location_id:assets_query=assets_query.filter(Asset.location_id==location_id);repairs_query=repairs_query.filter(RepairLog.asset.has(Asset.location_id==location_id))
 return render_template("repairs_v2.html",assets=assets_query.all(),vendors=Vendor.query.filter_by(active=True).all(),repairs=repairs_query.order_by(RepairLog.created_at.desc()).all())

@bp.route("/repairs/<int:repair_id>/edit",methods=["POST"])
@login_required
def edit_repair(repair_id):
 r=db.session.get(RepairLog,repair_id);reason=required_reason("Reason for repair update")
 if not r:abort(404)
 if reason:
  try:
   old={"vendor":r.vendor.name if r.vendor else None,"complaint":r.issue_description,"cost":r.repair_cost,"invoice":r.invoice_number,"parts":r.parts_changed,"payment":r.payment_type,"remarks":r.remarks}
   issue=request.form.get("issue_description","").strip()
   if not issue:raise ValueError("Complaint is required.")
   r.vendor_id=request.form.get("vendor_id",type=int) or None;r.issue_description=issue;r.repair_cost=request.form.get("repair_cost",type=float) or 0;r.invoice_number=request.form.get("invoice_number","").strip() or None;r.parts_changed=request.form.get("parts_changed","").strip() or None;r.payment_type=request.form.get("payment_type","").strip() or None;r.remarks=request.form.get("remarks","").strip() or None
   audit("Repair",r.id,"Update",old=old,new={"vendor_id":r.vendor_id,"complaint":r.issue_description,"cost":r.repair_cost,"invoice":r.invoice_number,"parts":r.parts_changed,"payment":r.payment_type,"remarks":r.remarks},reason=reason);db.session.commit();flash("Repair record updated.","success")
  except (ValueError,TypeError) as exc:db.session.rollback();flash(str(exc) or "Repair update failed.","danger")
 return redirect(url_for("operations.repairs"))
@bp.route("/repairs/<int:repair_id>/receive",methods=["POST"])
@login_required
def receive_repair(repair_id):
 r=db.session.get(RepairLog,repair_id)
 reason=required_reason("Reason for receiving repair")
 if r and r.status=="Sent" and r.asset.status=="Under Repair" and reason:
  prior_employee=r.asset.employee;r.status="Received";r.received_date=date.today();r.condition_after=request.form.get("condition_after") or r.asset.condition;r.asset.condition=r.condition_after;r.asset.status="Available";r.asset.employee=None;r.asset.department=None;assignment=AssetAssignment.query.filter_by(asset_id=r.asset_id,active=True).order_by(AssetAssignment.assigned_at.desc()).first()
  if assignment:assignment.active=False;assignment.returned_at=utc_now();assignment.condition_in=r.condition_after
  record_event(r.asset,"Received from Repair",from_employee=prior_employee,condition_before=r.condition_before,condition_after=r.condition_after,reason=reason);movement(r.asset,"Receive from Repair",remarks=reason);audit("Repair",r.id,"Receive",new={"status":"Received"},reason=reason);db.session.commit();flash("Asset received and marked available.","success")
 return redirect(url_for("operations.repairs"))

@bp.route("/scrap",methods=["GET","POST"])
@login_required
def scrap():
 if request.method=="POST":
  reason=required_reason("Scrapping reason"); a=db.session.get(Asset,request.form.get("asset_id",type=int))
  eligible=("Available","Lost")
  if reason and a and a.status in eligible and not ScrapLog.query.filter_by(asset_id=a.id).first():
   old=(a.employee,a.department,a.location)
   record=ScrapLog(asset=a,scrap_date=date.fromisoformat(request.form.get("scrap_date") or str(date.today())),reason=reason,disposal_method=request.form.get("disposal_method","").strip(),approved_by=request.form.get("approved_by","").strip(),remarks=request.form.get("remarks","").strip())
   if not record.disposal_method or not record.approved_by:flash("Disposal method and approved by are required.","danger")
   else:
    previous_status=a.status;a.status="Scrapped";active_block=AssetBlock.query.filter_by(asset_id=a.id,active=True).first()
    if active_block:active_block.active=False;active_block.unblocked_date=date.today()
    db.session.add(record);record_event(a,"Scrapped",condition_before=a.condition,condition_after=a.condition,reason=reason,remarks=record.remarks);movement(a,"Scrap",*old,reason);audit("Asset",a.id,"Scrap",old={"status":previous_status},new={"status":"Scrapped","disposal_method":record.disposal_method},reason=reason);db.session.commit();flash("Asset marked as scrapped. Its record and history were retained.","success")
  elif reason:flash("Asset is not eligible for scrapping. Return assigned assets, complete repair, or unblock blocked assets first.","danger")
  return redirect(url_for("operations.scrap"))
 return render_template(
  "scrap.html",
  assets=Asset.query.order_by(Asset.asset_tag).all(),
  eligible_statuses=("Available","Lost"),
  scrapped_asset_ids={row[0] for row in db.session.query(ScrapLog.asset_id).all()},
  scraps=ScrapLog.query.order_by(ScrapLog.scrap_date.desc()).all(),
 )
@bp.route("/movements")
@login_required
def movements():
 query=MovementLog.query;location_id=request.args.get("location",type=int)
 if location_id:query=query.filter(MovementLog.asset.has(Asset.location_id==location_id))
 return render_template("movements.html",movements=query.order_by(MovementLog.date.desc()).all())
