from sqlalchemy import func
from datetime import date, timedelta, datetime
from pathlib import Path
from flask import Blueprint, render_template, jsonify, current_app, request, session, redirect, url_for, flash
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from sqlalchemy import text, or_
from ..extensions import db
from .helpers import login_required, permission_required
from ..models import Asset, Department, Location, Employee, AssetCategory, MovementLog, RepairLog, AssetRequest, ApprovalRequest, InfrastructureItem
from ..services.lbac import is_super_admin, workspace_location_id, workspace_locations
bp=Blueprint("main",__name__)

def safe_workspace_return(value):
 target=(value or url_for("main.dashboard")).strip()
 if not target.startswith("/") or target.startswith("//"):
  return url_for("main.dashboard")
 parsed=urlsplit(target)
 query=urlencode([(key,item) for key,item in parse_qsl(parsed.query,keep_blank_values=True) if key!="location"])
 return urlunsplit(("","",parsed.path or "/",query,parsed.fragment))

@bp.route("/workspace/location",methods=["POST"])
@login_required
def set_workspace_location():
 location_id=request.form.get("location_id",type=int)
 options={location.id:location for location in workspace_locations()}
 if location_id:
  location=options.get(location_id)
  if not location:
   flash("That location is not available to your account.","danger")
  else:
   session["workspace_location_id"]=location.id
   flash(f"Operational workspace changed to {location.name}.","success")
 else:
  session.pop("workspace_location_id",None)
  flash("Operational workspace changed to all accessible locations.","success")
 return redirect(safe_workspace_return(request.form.get("next")))
@bp.route("/health")
def health():
 checks={}
 try:
  db.session.execute(text("SELECT 1"));checks["database"]=True
 except Exception:
  db.session.rollback();checks["database"]=False
 for name,key in (("uploads","UPLOAD_DIR"),("documents","DOCUMENT_DIR"),("qr","QR_CODE_DIR"),("logs","LOG_DIR")):
  path=Path(current_app.config[key]);checks[name]=path.is_dir()
 checks["routes"]=all(endpoint in current_app.view_functions for endpoint in ("auth.login","main.dashboard","assets.list_assets","masters.employees","infrastructure.index","reports.index"))
 healthy=all(checks.values())
 return jsonify(healthy=healthy,application=current_app.config["APP_NAME"],version=current_app.config["APP_VERSION"],checks=checks),200 if healthy else 503

@bp.route("/")
@permission_required("dashboard.view")
def dashboard():
 locations_list=workspace_locations()
 selected_location_id=workspace_location_id()
 if not selected_location_id and is_super_admin():selected_location_id=request.args.get("location",type=int)
 selected_location=next((row for row in locations_list if row.id==selected_location_id),None)
 if selected_location_id and not selected_location:selected_location_id=None

 asset_query=Asset.query
 employee_query=Employee.query.filter_by(active=True,employment_status="Active")
 infrastructure_query=InfrastructureItem.query
 assignment_request_query=AssetRequest.query.filter_by(status="Pending Approval")
 approval_request_query=ApprovalRequest.query.filter_by(status="Pending")
 movement_query=MovementLog.query
 repair_query=RepairLog.query
 if selected_location_id:
  asset_query=asset_query.filter(Asset.location_id==selected_location_id)
  employee_query=employee_query.filter(Employee.location_id==selected_location_id)
  infrastructure_query=infrastructure_query.filter(InfrastructureItem.location_id==selected_location_id)
  assignment_request_query=assignment_request_query.filter(AssetRequest.requested_for.has(Employee.location_id==selected_location_id))
  approval_request_query=approval_request_query.filter(or_(ApprovalRequest.location_id==selected_location_id,ApprovalRequest.target_location_id==selected_location_id))
  movement_query=movement_query.filter(MovementLog.asset.has(Asset.location_id==selected_location_id))
  repair_query=repair_query.filter(RepairLog.asset.has(Asset.location_id==selected_location_id))

 status_counts=dict(asset_query.with_entities(Asset.status,func.count(Asset.id)).group_by(Asset.status).all())
 counts={s:int(status_counts.get(s,0)) for s in ["Assigned","Available","Under Repair","Scrapped","Blocked","Lost"]}
 counts["Pending Approvals"]=assignment_request_query.count()
 active_employee_count=employee_query.count()
 counts["Active Employees"]=active_employee_count
 # Kept as a compatibility alias for older dashboard/report consumers.
 counts["Total Employees"]=active_employee_count
 def chart_rows(rows, empty_label="No data"):
  """Return plain JSON-compatible values for the dashboard JavaScript."""
  values=[[str(label or "Unassigned"), int(count)] for label, count in rows]
  return values or [[empty_label,0]]
 location_rows=db.session.query(Location.id,Location.name,func.count(Asset.id)).outerjoin(Asset,Asset.location_id==Location.id).filter(Location.active==True).group_by(Location.id,Location.name).order_by(Location.name).all()
 location_asset_counts={row[0]:row[1] for row in db.session.query(Asset.location_id,func.count(Asset.id)).group_by(Asset.location_id).all()}
 location_assigned_counts={row[0]:row[1] for row in db.session.query(Asset.location_id,func.count(Asset.id)).filter(Asset.status=="Assigned").group_by(Asset.location_id).all()}
 location_available_counts={row[0]:row[1] for row in db.session.query(Asset.location_id,func.count(Asset.id)).filter(Asset.status=="Available").group_by(Asset.location_id).all()}
 location_employee_counts={row[0]:row[1] for row in db.session.query(Employee.location_id,func.count(Employee.id)).filter(Employee.active==True,Employee.employment_status=="Active").group_by(Employee.location_id).all()}
 location_infrastructure_counts={row[0]:row[1] for row in db.session.query(InfrastructureItem.location_id,func.count(InfrastructureItem.id)).group_by(InfrastructureItem.location_id).all()}
 summary_locations=[selected_location] if selected_location else locations_list
 location_summary=[{
  "id":location.id,"name":location.name,
  "assets":int(location_asset_counts.get(location.id,0)),
  "assigned":int(location_assigned_counts.get(location.id,0)),
  "available":int(location_available_counts.get(location.id,0)),
  "employees":int(location_employee_counts.get(location.id,0)),
  "infrastructure":int(location_infrastructure_counts.get(location.id,0)),
 } for location in summary_locations]

 department_rows=db.session.query(Department.name,func.count(Asset.id)).join(Employee,Employee.department_id==Department.id).join(Asset,(Asset.employee_id==Employee.id)&(Asset.status=="Assigned"))
 category_rows=db.session.query(AssetCategory.name,func.count(Asset.id)).join(Asset)
 if selected_location_id:
  department_rows=department_rows.filter(Asset.location_id==selected_location_id)
  category_rows=category_rows.filter(Asset.location_id==selected_location_id)
 charts={
  "department": chart_rows(department_rows.group_by(Department.id,Department.name).order_by(Department.name).all(),"No assigned assets"),
  "location": chart_rows([(name,count) for _id,name,count in location_rows],"No location data"),
  "category": chart_rows(category_rows.group_by(AssetCategory.id,AssetCategory.name).having(func.count(Asset.id)>0).order_by(AssetCategory.name).all(),"No categories"),
 "status": [[s,counts[s]] for s in ["Assigned","Available","Under Repair","Scrapped","Blocked","Lost"]],
 }
 today=date.today()
 infrastructure_counts={
  "total":infrastructure_query.count(),
  "active":infrastructure_query.filter(InfrastructureItem.status=="Active").count(),
  "repair":infrastructure_query.filter(InfrastructureItem.status=="Under Repair").count(),
  "recent":infrastructure_query.filter(InfrastructureItem.created_at>=datetime.combine(today-timedelta(days=7),datetime.min.time())).count(),
 }
 warranty_query=asset_query.filter(Asset.warranty_expiry.between(today,today+timedelta(days=30)))
 recent_assigned_query=movement_query.filter(MovementLog.action.in_(("Issue","Transfer")),MovementLog.date>=datetime.combine(today-timedelta(days=7),datetime.min.time()))
 alerts={
  "repair_overdue": repair_query.filter(RepairLog.status=="Sent",RepairLog.repair_date<today-timedelta(days=14)).count(),
  "warranty_expiring": warranty_query.count(),
  "blocked_assets": counts.get("Blocked",0),
  "recent_assigned": recent_assigned_query.count(),
  "pending_approvals": counts["Pending Approvals"],
  "under_repair": counts.get("Under Repair",0),
 }
 recent_approved=AssetRequest.query.filter_by(status="Approved")
 if selected_location_id:recent_approved=recent_approved.filter(AssetRequest.requested_for.has(Employee.location_id==selected_location_id))
 approvals={
  "assignments":alerts["pending_approvals"],
  "returns":approval_request_query.filter(ApprovalRequest.module=="Return").count(),
  "transfers":approval_request_query.filter(ApprovalRequest.module.in_(("Transfer","Cross-Location Transfer"))).count(),
  "actions":approval_request_query.filter(ApprovalRequest.module=="Location Action").count(),
  "recent":recent_approved.order_by(AssetRequest.decided_at.desc()).limit(5).all(),
 }
 return render_template(
  "dashboard_v3.html",total=sum(status_counts.values()),counts=counts,
  departments=Department.query.count(),locations=len(locations_list),employees=active_employee_count,
  recent=movement_query.order_by(MovementLog.date.desc()).limit(8).all(),charts=charts,
  location_rows=location_rows,location_summary=location_summary,location_options=locations_list,
  selected_location_id=selected_location_id,selected_location=selected_location,
  approvals=approvals,alerts=alerts,infrastructure_counts=infrastructure_counts,
 )
