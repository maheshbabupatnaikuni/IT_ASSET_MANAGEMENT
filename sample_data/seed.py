"""Seed the local database and generate QR images for sample records."""

from itams import create_app
from itams.extensions import db
from itams.models import Asset, InfrastructureItem
from itams.services.infrastructure import ensure_infrastructure_qr
from itams.services.qr_management import ensure_qr_code


def seed() -> dict[str, int]:
    app = create_app()
    with app.app_context():
        assets = Asset.query.order_by(Asset.id).all()
        infrastructure = InfrastructureItem.query.order_by(InfrastructureItem.id).all()
        for asset in assets:
            ensure_qr_code(asset, actor="System Seeder", reason="Synthetic QR generation")
        for item in infrastructure:
            ensure_infrastructure_qr(item, actor="System Seeder", reason="Synthetic QR generation")
        db.session.commit()
        return {"assets": len(assets), "infrastructure": len(infrastructure)}


if __name__ == "__main__":
    counts = seed()
    print(f"Seeded sample data: {counts['assets']} assets, {counts['infrastructure']} infrastructure items.")
