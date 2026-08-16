"""Anonymous, read-only QR profiles containing only approved public fields."""

from flask import Blueprint, abort, render_template
from sqlalchemy import select

from ..extensions import db
from ..models import (
    Asset, AssetQRIdentity, InfrastructureItem, InfrastructureQRIdentity,
)
from ..services.qr_management import TOKEN_PATTERN


bp = Blueprint("public", __name__)


@bp.get("/q/<string:token>")
def qr_profile(token):
    if not TOKEN_PATTERN.fullmatch(token or ""):
        abort(404)
    asset_row = db.session.execute(
        select(AssetQRIdentity, Asset)
        .join(Asset, Asset.id == AssetQRIdentity.asset_id)
        .where(AssetQRIdentity.token == token, AssetQRIdentity.active.is_(True))
        .execution_options(lbac_bypass=True)
    ).first()
    if asset_row:
        identity, asset = asset_row
        return render_template("public_qr_profile.html", kind="asset", record=asset)

    infrastructure_row = db.session.execute(
        select(InfrastructureQRIdentity, InfrastructureItem)
        .join(InfrastructureItem, InfrastructureItem.id == InfrastructureQRIdentity.infrastructure_id)
        .where(InfrastructureQRIdentity.token == token, InfrastructureQRIdentity.active.is_(True))
        .execution_options(lbac_bypass=True)
    ).first()
    if infrastructure_row:
        identity, item = infrastructure_row
        return render_template("public_qr_profile.html", kind="infrastructure", record=item)
    abort(404)
