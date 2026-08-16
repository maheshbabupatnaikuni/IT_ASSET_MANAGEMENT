from ..extensions import db
from ..models import Permission,Role

PERMISSIONS={
"Dashboard":[("dashboard.view","View dashboard"),("dashboard.charts","View dashboard charts"),("dashboard.summary","View summary counts")],
"Assets":[("asset.view","View assets"),("asset.add","Add asset"),("asset.edit","Edit asset"),("asset.custom.manage","Manage asset custom fields"),("asset.specification.replace","Replace tracked asset specifications"),("asset.delete","Delete asset"),("asset.import","Import assets"),("asset.export","Export assets"),("asset.available.view","View available assets"),("asset.blocked.view","View blocked assets"),("asset.block","Block asset"),("asset.unblock","Unblock asset"),("asset.scrap","Scrap asset"),("asset.history.view","View asset history"),("asset.qr.manage","Manage QR Codes")],
"Infrastructure":[("infrastructure.view","View infrastructure"),("infrastructure.add","Add infrastructure"),("infrastructure.edit","Edit infrastructure"),("infrastructure.delete","Delete infrastructure"),("infrastructure.history.manage","Manage infrastructure history"),("infrastructure.document.upload","Upload infrastructure documents and photos"),("infrastructure.export","Export infrastructure reports"),("infrastructure.qr.manage","Manage infrastructure QR codes"),("infrastructure.relationship.manage","Manage device relationships"),("infrastructure.port.manage","Manage network ports"),("infrastructure.incident.manage","Manage infrastructure incidents"),("infrastructure.verification.manage","Manage infrastructure verification"),("infrastructure.report.view","View infrastructure reports")],
"Employees":[("employee.view","View employees"),("employee.add","Add employee"),("employee.edit","Edit employee"),("employee.custom.manage","Manage employee custom fields"),("employee.delete","Delete employee"),("employee.history.view","View employee history"),("employee.without_assets.view","View employees without assets"),("employee.block","Block employee"),("employee.unblock","Unblock employee")],
"Assignments":[("assignment.view","View assignments"),("assignment.request.create","Create assignment request"),("assignment.request.edit","Edit pending assignment request"),("assignment.request.cancel","Cancel assignment request"),("assignment.approve","Approve assignment"),("assignment.reject","Reject assignment"),("assignment.return","Return asset"),("assignment.transfer","Transfer asset"),("assignment.history.view","View assignment history"),("assignment.acknowledgement.download","Download acknowledgement")],
"Repairs":[("repair.view","View repairs"),("repair.add","Add repair request"),("repair.edit","Edit repair record"),("repair.close","Close repair"),("repair.history.view","View repair history")],
"Lost and Recovered":[("lost.view","View lost assets"),("lost.mark","Mark asset as lost"),("lost.recover","Mark asset as recovered")],
"Resignation Clearance":[("clearance.view","View clearance records"),("clearance.create","Create clearance"),("clearance.edit","Edit clearance"),("clearance.complete","Complete clearance")],
"Reports":[("report.view","View reports"),("report.export","Export reports"),("report.print","Print reports"),("report.audit.view","View audit reports")],
"Users":[("user.view","View users"),("user.create","Create user"),("user.edit","Edit user"),("user.toggle","Activate/deactivate user"),("user.password.reset","Reset user password"),("user.role.change","Change user role"),("user.delete","Delete user"),("login_history.view","View login history")],
"Locations":[("location.view.own","View assigned location"),("location.view.multiple","View multiple assigned locations"),("location.manage.own","Manage data in assigned locations"),("location.approve.own","Approve requests for assigned locations"),("location.approve.all","Approve requests for all locations"),("location.transfer","Request and process cross-location transfers")],
"System":[("audit.view","View audit logs"),("role.manage","Manage roles"),("privilege.manage","Manage privileges"),("database.backup","Backup database"),("database.restore","Restore database"),("settings.manage","Manage application settings"),("import.reset","Reset imported data"),("master.manage","Manage administration reference lists")],
}

ADMIN_CODES={"dashboard.view","dashboard.charts","dashboard.summary","asset.view","asset.custom.manage","asset.specification.replace","asset.available.view","asset.history.view","infrastructure.view","infrastructure.add","infrastructure.edit","infrastructure.history.manage","infrastructure.document.upload","infrastructure.export","infrastructure.relationship.manage","infrastructure.port.manage","infrastructure.incident.manage","infrastructure.verification.manage","infrastructure.report.view","employee.view","employee.custom.manage","employee.history.view","employee.without_assets.view","assignment.view","assignment.request.create","assignment.request.edit","assignment.request.cancel","assignment.approve","assignment.reject","assignment.return","assignment.transfer","assignment.history.view","assignment.acknowledgement.download","repair.view","repair.add","repair.edit","repair.close","repair.history.view","lost.view","lost.mark","lost.recover","clearance.view","clearance.create","clearance.edit","clearance.complete","report.view","report.export","report.print","location.view.own","location.view.multiple","location.manage.own","location.approve.own","location.transfer"}

def seed_permissions():
 created_codes=set()
 for category,items in PERMISSIONS.items():
  for code,name in items:
   permission=Permission.query.filter_by(code=code).first()
   if not permission:db.session.add(Permission(code=code,name=name,category=category));created_codes.add(code)
   else:permission.name=name;permission.category=category
 db.session.flush()
 super_role=Role.query.filter_by(name="Super Admin").first()
 legacy_role=Role.query.filter_by(name="IT Admin").first()
 if not super_role and legacy_role:
  legacy_role.name="Super Admin";super_role=legacy_role;legacy_role=None
 if super_role and legacy_role:
  from ..models import User
  User.query.filter_by(role_id=legacy_role.id).update({"role_id":super_role.id})
  legacy_role.is_active=False;legacy_role.description="Legacy role retained after migration"
 admin_role=Role.query.filter_by(name="Admin").first()
 if super_role:
  super_role.name="Super Admin";super_role.is_system_role=True;super_role.is_active=True;super_role.description=super_role.description or "Protected system role with full access";super_role.permissions=Permission.query.all()
 if admin_role:
  admin_role.is_system_role=True;admin_role.is_active=True;admin_role.description=admin_role.description or "Daily IT operations role"
  if not admin_role.permissions:admin_role.permissions=Permission.query.filter(Permission.code.in_(ADMIN_CODES)).all()
  elif created_codes:
   existing={item.code for item in admin_role.permissions}
   admin_role.permissions.extend(Permission.query.filter(Permission.code.in_(created_codes & ADMIN_CODES),Permission.code.notin_(existing)).all())
 administrator=Role.query.filter_by(name="Administrator").first()
 it_user=Role.query.filter_by(name="IT User").first()
 location_user=Role.query.filter_by(name="Location User").first()
 viewer=Role.query.filter_by(name="Viewer").first()
 viewer_codes={
  "dashboard.view","dashboard.charts","dashboard.summary","asset.view","asset.available.view",
  "asset.blocked.view","asset.history.view","infrastructure.view","infrastructure.report.view",
  "employee.view","employee.history.view","employee.without_assets.view","assignment.view",
  "assignment.history.view","repair.view","repair.history.view","lost.view","report.view",
  "report.print","location.view.own","location.view.multiple",
 }
 operator_codes=ADMIN_CODES-{"asset.delete","employee.delete","infrastructure.delete","infrastructure.document.upload"}
 location_codes=operator_codes-{"location.view.multiple","location.transfer"}
 administrator_codes={code for code in (item.code for item in Permission.query.all()) if code not in {
  "database.backup","database.restore","asset.import","import.reset",
  "asset.delete","employee.delete","infrastructure.delete","user.delete","user.password.reset",
 }}
 for role,codes in (
  (administrator,administrator_codes),(it_user,operator_codes),
  (location_user,location_codes),(viewer,viewer_codes),
 ):
  if role:
   role.is_system_role=True;role.is_active=True
   role.permissions=Permission.query.filter(Permission.code.in_(codes)).all()
 db.session.commit()
