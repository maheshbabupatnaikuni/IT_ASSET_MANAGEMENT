from io import BytesIO

from conftest import login
from itams.extensions import db
from itams.models import (
    Asset, AssetAssignment, AssetLifecycle, AssetRequest, Employee,
    InfrastructureItem, RepairLog,
)
from itams.services.qr_management import active_identity, ensure_qr_code
from itams.services.uploads import validate_upload
from werkzeug.datastructures import FileStorage


def test_application_health_and_seed_counts(app, client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["healthy"] is True
    with app.app_context():
        assert Employee.query.count() == 15
        assert Asset.query.count() == 30
        assert InfrastructureItem.query.count() == 10
        assert all(item.system_asset_reference.startswith("ITAM-AST-") for item in Asset.query.all())


def test_login_dashboard_profiles_reports_and_permissions(app, client):
    assert login(client, "viewer", "test-only-viewer-password").status_code == 302
    for path in ("/", "/assets/", "/masters/employees", "/infrastructure/", "/reports/"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"IT Asset Management" in response.data
    assert b"ITAM-AST-000001" in client.get("/assets/").data
    assert client.post("/assets/new", data={}).status_code == 403


def test_administrator_can_manage_future_users_and_roles(app, client):
    assert login(client).status_code == 302
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/roles").status_code == 200
    assert client.get("/admin/roles/new").status_code == 200


def test_location_user_isolation(app, client):
    assert login(client, "location_user", "test-only-location-password").status_code == 302
    response = client.get("/assets/")
    assert response.status_code == 200
    assert b"ITAM-AST-000003" in response.data
    assert b"ITAM-AST-000001" not in response.data


def test_asset_create_update_and_safe_delete_policy(app, client):
    assert login(client).status_code == 302
    with app.app_context():
        source = Asset.query.first()
        form = {
            "asset_tag": "ITAM-TAG-NEW1", "category_id": str(source.category_id),
            "location_id": str(source.location_id), "department_id": str(source.department_id),
            "manufacturer_id": str(source.manufacturer_id), "vendor_id": str(source.vendor_id),
            "model": "Test Model", "serial_number": "ITAM-SN-NEW001",
            "condition": "Good", "remarks": "SAMPLE DATA",
        }
    response = client.post("/assets/new", data=form, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        created = Asset.query.filter_by(asset_tag="ITAM-TAG-NEW1").one()
        created_id = created.id
        assert created.system_asset_reference.startswith("ITAM-AST-")
    form.update({"model": "Updated Test Model", "reason": "Test update"})
    assert client.post(f"/assets/{created_id}/edit", data=form, follow_redirects=False).status_code == 302
    with app.app_context():
        assert db.session.get(Asset, created_id).model == "Updated Test Model"
    assert client.post(f"/assets/{created_id}/delete", data={"reason": "test"}).status_code == 403


def test_assignment_approval_transfer_return_and_repair(app, client):
    assert login(client).status_code == 302
    with app.app_context():
        employee = Employee.query.filter_by(employee_code="ITAM-EMP-000011").one()
        destination = Employee.query.filter_by(employee_code="ITAM-EMP-000002").one()
        asset = Asset.query.filter_by(system_asset_reference="ITAM-AST-000011").one()
        repair_asset = Asset.query.filter_by(system_asset_reference="ITAM-AST-000012").one()
        assert asset.location_id == employee.location_id == destination.location_id
        employee_id, destination_id, asset_id, repair_asset_id = employee.id, destination.id, asset.id, repair_asset.id
    assert client.post("/requests/create", data={
        "employee_id": employee_id, "asset_id": asset_id, "reason": "Synthetic assignment request",
        "assignment_date": "2026-04-01", "condition": "Good", "accessories": "Power adapter",
    }).status_code == 302
    with app.app_context():
        request_row = AssetRequest.query.filter_by(asset_id=asset_id, status="Pending Approval").one()
        request_id = request_row.id
    assert client.post(f"/requests/{request_id}/decide", data={"decision": "approve", "remarks": "Approved"}).status_code == 302
    with app.app_context():
        assert db.session.get(Asset, asset_id).status == "Assigned"
        assert AssetAssignment.query.filter_by(asset_id=asset_id, active=True).count() == 1
    assert client.post("/operations/transfer", data={"asset_id": asset_id, "employee_id": destination_id, "reason": "Synthetic same-location transfer", "accessories": "Power adapter"}).status_code == 302
    with app.app_context():
        assert db.session.get(Asset, asset_id).employee_id == destination_id
        assert AssetLifecycle.query.filter_by(asset_id=asset_id, event_type="Transferred").count() == 1
    assert client.post("/operations/return", data={"asset_id": asset_id, "return_date": "2026-04-10", "condition": "Good", "accessories_returned": "Power adapter", "remarks": "Synthetic return"}).status_code == 302
    with app.app_context():
        assert db.session.get(Asset, asset_id).status == "Available"
    assert client.post("/operations/repairs", data={"asset_id": repair_asset_id, "issue_description": "Synthetic repair test", "reason": "Synthetic repair workflow", "repair_date": "2026-04-11"}).status_code == 302
    with app.app_context():
        assert db.session.get(Asset, repair_asset_id).status == "Under Repair"
        assert RepairLog.query.filter_by(asset_id=repair_asset_id).count() >= 1


def test_public_qr_is_anonymous_and_read_only(app, client):
    with app.app_context():
        asset = Asset.query.first()
        ensure_qr_code(asset, actor="Test Seeder", reason="Synthetic QR test")
        db.session.commit()
        token = active_identity(asset.id).token
    response = client.get(f"/q/{token}")
    assert response.status_code == 200
    assert b"Asset profile" in response.data
    assert b"read-only" in response.data
    assert b"Audit History" not in response.data
    assert client.post(f"/q/{token}").status_code == 405


def test_upload_allow_list_and_content_validation():
    valid = FileStorage(stream=BytesIO(b"%PDF-1.7\nsample file"), filename="sample.pdf", content_type="application/pdf")
    assert validate_upload(valid) == "sample.pdf"
    disguised = FileStorage(stream=BytesIO(b"not a pdf"), filename="sample.pdf", content_type="application/pdf")
    try:
        validate_upload(disguised)
    except ValueError as exc:
        assert "valid PDF" in str(exc)
    else:
        raise AssertionError("Disguised upload was accepted")
