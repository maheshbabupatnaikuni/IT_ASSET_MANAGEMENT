from functools import wraps
from flask import session, redirect, url_for, flash, request, abort, g
from ..extensions import db
from ..models import User
def login_required(f):
 @wraps(f)
 def decorated(*args, **kwargs):
  if not session.get("user_id"):
   if not session.get("_flashes"):
    flash("Your session has expired. Please login again.", "warning")
   return redirect(url_for("auth.login",next=request.full_path if request.query_string else request.path))
  if session.get("must_change_password") and request.endpoint not in ("auth.password","auth.logout"):
   flash("You must change your password before continuing.","warning"); return redirect(url_for("auth.password"))
  return f(*args, **kwargs)
 return decorated

def current_password_valid():
 if getattr(g,"approval_replay_authorized",False):
  return True
 user=getattr(g,"current_user",None) or db.session.get(User,session.get("user_id")); return bool(user and user.active and user.check_password(request.form.get("current_password","")))

def required_reason(label="Reason"):
 reason=request.form.get("reason","").strip()
 if not reason: flash(f"{label} is required.","danger"); return None
 return reason

def normalized_role():
 if not session.get("user_id"):return ""
 user=getattr(g,"current_user",None)
 return user.role.name if user and user.active and user.role and user.role.is_active else ""

def role_required(*roles):
 def decorator(f):
  @wraps(f)
  @login_required
  def guarded(*args,**kwargs):
   if normalized_role() not in roles:abort(403)
   return f(*args,**kwargs)
  return guarded
 return decorator

def can(permission):
 aliases={"dashboard":"dashboard.view","search":"asset.view","requests.create":"assignment.request.create","returns":"assignment.return","transfers":"assignment.transfer","repairs":"repair.view","reports":"report.view","history":"asset.history.view","assets.view":"asset.view","lost_recovered":"lost.view","profile":"dashboard.view"}
 permission=aliases.get(permission,permission)
 user=getattr(g,"current_user",None)
 if not user or not user.active or not user.role or not user.role.is_active:return False
 if user.role.name=="Super Admin":return True
 return any(item.code==permission for item in user.role.permissions)

def permission_required(permission):
 def decorator(f):
  @wraps(f)
  @login_required
  def guarded(*args,**kwargs):
   if not can(permission):
    g.required_permission=permission;abort(403)
   return f(*args,**kwargs)
  return guarded
 return decorator
