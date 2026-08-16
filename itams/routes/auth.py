from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, session
from datetime import datetime, timezone
from collections import defaultdict, deque
from threading import Lock
import time
from urllib.parse import urlsplit
from ..models import User, LoginHistory, AuditLog, utc_now
from ..extensions import db
from ..services.audit import audit
from ..services.active_sessions import (
 active_session_warning, claim_active_session, release_active_session,
 SESSION_TOKEN_KEY,
)
from .helpers import login_required
bp=Blueprint("auth",__name__,url_prefix="/auth")
_login_attempts=defaultdict(deque)
_login_attempts_lock=Lock()

def login_rate_limited(key):
 window=int(current_app.config.get("LOGIN_WINDOW_SECONDS",60))
 maximum=int(current_app.config.get("MAX_LOGIN_ATTEMPTS",8))
 now=time.monotonic()
 with _login_attempts_lock:
  attempts=_login_attempts[key]
  while attempts and attempts[0] <= now-window:attempts.popleft()
  if len(attempts)>=maximum:return True
  attempts.append(now)
  return False
def safe_next_url(value):
 if not value:return None
 parsed=urlsplit(value)
 if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):return None
 return value
@bp.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  key=request.remote_addr or "unknown"
  if login_rate_limited(key):
   flash("Too many sign-in attempts. Please wait and try again.","warning")
   return render_template("login.html",next_url=safe_next_url(request.form.get("next") or request.args.get("next"))),429
  username=request.form.get("username","").strip()
  user=User.query.filter_by(username=username,active=True).first()
  if user and user.role and user.role.is_active and user.check_password(request.form.get("password","")):
   token=claim_active_session(user)
   if not token:
    warning=active_session_warning(user)
    db.session.add(AuditLog(user=username,entity="Authentication",entity_id=str(user.id),action="Login Blocked",reason=warning));db.session.commit()
    flash(warning,"warning")
    return render_template("login.html",next_url=safe_next_url(request.form.get("next") or request.args.get("next")))
   now=utc_now();user.last_login_at=now
   session.clear(); session.permanent=True; session.update(user_id=user.id,user_name=user.full_name,role=user.role.name,login_at=now.isoformat(),must_change_password=bool(user.must_change_password),active_session_token=token)
   db.session.add(LoginHistory(user=user,ip_address=request.remote_addr));audit("Authentication",user.id,"Login",new={"ip_address":request.remote_addr});db.session.commit()
   next_url=safe_next_url(request.form.get("next") or request.args.get("next"))
   if next_url:session["post_login_next"]=next_url
   if user.must_change_password:flash("You must change your password before continuing.","warning");return redirect(url_for("auth.password"))
   return redirect(session.pop("post_login_next",None) or url_for("main.dashboard"))
  db.session.add(AuditLog(user=username or "Anonymous",entity="Authentication",entity_id=username or "-",action="Login Failed",reason="Invalid credentials or inactive account"));db.session.commit()
  flash("Incorrect username or password.","danger")
 return render_template("login.html",next_url=safe_next_url(request.form.get("next") or request.args.get("next")))
@bp.route("/logout")
def logout():
 if session.get("user_id"):
  release_active_session(session["user_id"],session.get(SESSION_TOKEN_KEY))
  audit("Authentication",session["user_id"],"Logout");db.session.commit()
 session.clear(); return redirect(url_for("auth.login"))
@bp.route("/password",methods=["GET","POST"])
@login_required
def password():
 if request.method=="POST":
  user=db.session.get(User,session["user_id"])
  if not user.check_password(request.form.get("current_password","")): flash("Current password is incorrect.","danger")
  elif len(request.form.get("new_password",""))<8: flash("Use at least 8 characters.","danger")
  elif request.form.get("new_password")!=request.form.get("confirm_password"):flash("New password and confirmation do not match.","danger")
  else: user.set_password(request.form["new_password"]);user.must_change_password=False;audit("User",user.id,"Password Changed");db.session.commit();session["must_change_password"]=False;flash("Password updated.","success");return redirect(session.pop("post_login_next",None) or url_for("main.dashboard"))
 return render_template("password.html")

@bp.route("/profile")
@login_required
def profile():
 return render_template("profile.html",user=db.session.get(User,session["user_id"]))
