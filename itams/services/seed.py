"""Create the deterministic IT Asset Management sample dataset."""

from datetime import date, datetime, timedelta
import os
from pathlib import Path
import secrets

from ..extensions import db
from ..models import (
    Asset, AssetAssignment, AssetCategory, AssetLifecycle, AuditLog, Department,
    Employee, InfrastructureCategory, InfrastructureHistory, InfrastructureItem,
    Location, Manufacturer, MovementLog, RepairLog, Role, Setting, SystemCounter, User, Vendor,
)


SAMPLE_NOTICE = "SAMPLE DATA"


def _credential() -> str:
    name = "ITAMS_ADMIN_PASSWORD"
    supplied = os.environ.get(name, "").strip()
    if supplied:
        if len(supplied) < 8:
            raise RuntimeError(f"{name} must contain at least 8 characters.")
        return supplied
    return secrets.token_urlsafe(14)


def _write_local_credentials(credentials: dict[str, str]) -> None:
    if db.engine.url.database in (None, ":memory:"):
        return
    target = Path(db.engine.url.database).resolve().parent / "login_credentials.txt"
    lines = [
        "IT Asset Management - local login credentials",
        "This ignored runtime file is not part of the public repository.", "",
        *(f"{username}: {password}" for username, password in credentials.items()), "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")


def seed_database() -> None:
    if User.query.first():
        return

    roles = {
        name: Role(name=name, description=description, is_system_role=True, is_active=True)
        for name, description in (
            ("Administrator", "Application administrator"),
            ("IT User", "Daily asset and infrastructure operations"),
            ("Location User", "Operations limited to one assigned location"),
            ("Viewer", "Read-only access"),
        )
    }
    db.session.add_all(roles.values())

    locations = [
        Location(name="Corporate Office", code="CORP", short_name="Corporate", city="Example City", country="Sample Country", remarks=SAMPLE_NOTICE),
        Location(name="Development Center", code="DEV", short_name="Development", city="Sample City", country="Sample Country", remarks=SAMPLE_NOTICE),
        Location(name="Operations Site", code="OPS", short_name="Operations", city="Sample City", country="Sample Country", remarks=SAMPLE_NOTICE),
    ]
    departments = [Department(name=name) for name in (
        "Information Technology", "Finance", "Human Resources", "Operations", "Administration"
    )]
    categories = [AssetCategory(name=name) for name in (
        "Laptop", "Desktop", "Monitor", "Network Printer", "Smartphone", "Projector"
    )]
    manufacturers = [Manufacturer(name=name) for name in ("Dell", "Lenovo", "HP", "Canon", "Samsung", "Epson", "Cisco")]
    vendors = [
        Vendor(name="Northstar Technology Supplies", contact_person="Account Team", email="sales@northstar.example", services="Hardware supply"),
        Vendor(name="Blue Oak Device Services", contact_person="Service Desk", email="service@blueoak.example", services="Repair services"),
        Vendor(name="Vertex Office Systems", contact_person="Support Team", email="support@vertex.example", services="Workplace equipment"),
    ]
    db.session.add_all(locations + departments + categories + manufacturers + vendors)
    db.session.flush()

    passwords = {"admin": _credential()}
    administrator = User(
        username="admin", full_name="Administrator", role=roles["Administrator"],
        approval_level="Location Admin", location_scope_enabled=False,
    )
    administrator.set_password(passwords["admin"])
    db.session.add(administrator)

    employee_names = [
        "Avery Stone", "Jordan Vale", "Morgan Reed", "Riley Quinn", "Casey Hart",
        "Taylor Brooks", "Cameron Lake", "Drew Parker", "Skyler Lane", "Reese Rowan",
        "Alex Winter", "Jamie Rivers", "Robin Ash", "Sage Monroe", "Kendall Frost",
    ]
    employees = []
    for index, name in enumerate(employee_names, start=1):
        employee = Employee(
            employee_code=f"ITAM-EMP-{index:06d}", name=name,
            email=f"employee{index:02d}@example.test",
            designation=("Analyst", "Coordinator", "Engineer", "Specialist", "Manager")[(index - 1) % 5],
            department=departments[(index - 1) % len(departments)],
            location=locations[(index - 1) % len(locations)], employment_status="Active",
            active=True, manually_created=True,
        )
        employees.append(employee)
    db.session.add_all(employees)
    db.session.flush()

    catalog = [
        ("Laptop", "Dell", "Latitude 5440"), ("Laptop", "Lenovo", "ThinkPad E14"),
        ("Desktop", "HP", "ProDesk 400"), ("Monitor", "Dell", "P2422H"),
        ("Network Printer", "Canon", "ImageClass Sample"), ("Smartphone", "Samsung", "Galaxy A Sample"),
        ("Projector", "Epson", "PowerLite Sample"),
    ]
    category_by_name = {item.name: item for item in categories}
    manufacturer_by_name = {item.name: item for item in manufacturers}
    assets = []
    for index in range(1, 31):
        category_name, manufacturer_name, model = catalog[(index - 1) % len(catalog)]
        employee = employees[index - 1] if index <= 10 else None
        status = "Assigned" if employee else ("Under Repair" if index in {26, 27} else "Available")
        asset = Asset(
            system_asset_reference=f"ITAM-AST-{index:06d}", location_asset_reference=f"ITAM-AST-{index:06d}",
            asset_tag=f"ITAM-TAG-{index:04d}", category=category_by_name[category_name],
            manufacturer=manufacturer_by_name[manufacturer_name], vendor=vendors[(index - 1) % len(vendors)],
            model=model, serial_number=f"ITAM-SN-{index:06d}", hostname=f"itam-device-{index:02d}",
            operating_system="Windows 11" if category_name in {"Laptop", "Desktop"} else None,
            processor="Intel Core i5" if category_name in {"Laptop", "Desktop"} else None,
            ram="16 GB" if category_name in {"Laptop", "Desktop"} else None,
            storage="512 GB SSD" if category_name in {"Laptop", "Desktop"} else None,
            purchase_date=date(2025, 1, 10) + timedelta(days=index * 5),
            warranty_expiry=date(2028, 1, 10) + timedelta(days=index * 5),
            purchase_cost=round(42000 + index * 875, 2), invoice_number=f"ITAM-INV-{index:05d}",
            location=employee.location if employee else locations[(index - 1) % len(locations)],
            department=employee.department if employee else departments[(index - 1) % len(departments)],
            employee=employee, assigned_date=date(2026, 1, index) if employee else None,
            status=status, condition="Good", remarks=SAMPLE_NOTICE, updated_by="System Seeder",
        )
        assets.append(asset)
    db.session.add_all(assets)
    db.session.flush()

    base_time = datetime(2026, 1, 5, 9, 0, 0)
    for index in range(10):
        assignment = AssetAssignment(asset=assets[index], employee=employees[index], assigned_at=base_time + timedelta(days=index), condition_out="Good", accessories="Power adapter", active=True, remarks=SAMPLE_NOTICE)
        db.session.add_all([
            assignment,
            AssetLifecycle(event_key=f"ITAM-LIFE-ASSIGN-{index + 1:04d}", event_type="Assigned", asset=assets[index], to_employee=employees[index], location=employees[index].location, occurred_at=assignment.assigned_at, condition_after="Good", reason="Sample assignment", performed_by="System Seeder"),
            MovementLog(movement_id=f"ITAM-MOV-{index + 1:04d}", date=assignment.assigned_at, asset=assets[index], action="Issue", new_user=employees[index].name, new_department=employees[index].department.name, new_location=employees[index].location.name, updated_by="System Seeder", remarks=SAMPLE_NOTICE),
        ])

    for index in range(5):
        asset, employee = assets[10 + index], employees[10 + index]
        assigned_at = base_time - timedelta(days=120 - index * 7)
        returned_at = assigned_at + timedelta(days=45)
        db.session.add_all([
            AssetAssignment(asset=asset, employee=employee, assigned_at=assigned_at, returned_at=returned_at, condition_out="Good", condition_in="Good", accessories="Power adapter", accessories_returned="Power adapter", active=False, remarks=SAMPLE_NOTICE),
            AssetLifecycle(event_key=f"ITAM-LIFE-HIST-A-{index + 1:04d}", event_type="Assigned", asset=asset, to_employee=employee, location=employee.location, occurred_at=assigned_at, reason="Sample historical assignment", performed_by="System Seeder"),
            AssetLifecycle(event_key=f"ITAM-LIFE-HIST-R-{index + 1:04d}", event_type="Returned", asset=asset, from_employee=employee, location=employee.location, occurred_at=returned_at, reason="Sample historical return", performed_by="System Seeder"),
            MovementLog(movement_id=f"ITAM-MOV-HIST-{index + 1:04d}", date=returned_at, asset=asset, action="Return", previous_user=employee.name, previous_department=employee.department.name, previous_location=employee.location.name, updated_by="System Seeder", remarks=SAMPLE_NOTICE),
        ])

    for index, asset_index in enumerate((25, 26, 20, 21), start=1):
        received = index > 2
        db.session.add(RepairLog(
            asset=assets[asset_index], vendor=vendors[1],
            issue_description=("Battery health warning", "Display flicker", "Printer feed maintenance", "Projector lamp inspection")[index - 1],
            repair_cost=950 + index * 275, repair_date=date(2026, 3, index * 3),
            received_date=date(2026, 3, index * 3 + 4) if received else None,
            status="Received" if received else "Sent", invoice_number=f"ITAM-REPAIR-{index:04d}",
            parts_changed="Synthetic service parts", condition_before="Attention Required",
            condition_after="Good" if received else None, remarks=SAMPLE_NOTICE,
        ))

    infrastructure_categories = {}
    for name in ("Switches", "Firewalls", "Wireless Access Points", "CCTV Cameras", "Printers", "UPS", "Projectors"):
        category = InfrastructureCategory.query.filter_by(name=name).first() or InfrastructureCategory(name=name)
        infrastructure_categories[name] = category
        db.session.add(category)
    db.session.flush()
    infrastructure_catalog = [
        ("Switches", "Core Switch"), ("Firewalls", "Edge Firewall"),
        ("Wireless Access Points", "Wireless Access Point"), ("Wireless Access Points", "Meeting Area Access Point"),
        ("CCTV Cameras", "Entrance Camera"), ("Printers", "Shared Network Printer"),
        ("UPS", "Server Room UPS"), ("UPS", "Workplace UPS"),
        ("Projectors", "Conference Projector"), ("Switches", "Access Switch"),
    ]
    for index, (category_name, name) in enumerate(infrastructure_catalog, start=1):
        item = InfrastructureItem(
            infrastructure_reference=f"ITAM-INF-{index:06d}", category=infrastructure_categories[category_name],
            location_id=locations[(index - 1) % 3].id, name=name,
            manufacturer=manufacturers[index % len(manufacturers)], vendor=vendors[index % len(vendors)],
            model=f"Sample Model {index:02d}", serial_number=f"ITAM-INF-SN-{index:05d}",
            building="Main Building", floor=f"Level {(index - 1) % 3 + 1}", room=f"Room {index:02d}",
            status="Active", condition="Good", installation_date=date(2025, 6, 1) + timedelta(days=index * 10),
            specifications_json='{"data_notice":"SAMPLE DATA"}', remarks=SAMPLE_NOTICE,
            updated_by="System Seeder",
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(InfrastructureHistory(
            infrastructure=item, event_type="Installation",
            occurred_at=datetime(2025, 6, 1, 10, 0, 0) + timedelta(days=index * 10),
            reason="Synthetic installation record", details_json='{"result":"Installation complete"}',
            performed_by="System Seeder", remarks=SAMPLE_NOTICE,
        ))

    for counter_name, next_value in (
        ("employee_reference", 16), ("asset_reference", 31),
        ("asset_location_reference::ITAM", 31), ("infrastructure_reference", 11),
    ):
        counter = db.session.get(SystemCounter, counter_name)
        if counter:
            counter.next_value = max(counter.next_value, next_value)
        else:
            db.session.add(SystemCounter(name=counter_name, next_value=next_value))
    db.session.add_all([
        Setting(key="company_name", value="IT Asset Management"),
        Setting(key="ui.application_name", value="IT Asset Management"),
        Setting(key="data_notice", value=SAMPLE_NOTICE), Setting(key="theme", value="default"),
        AuditLog(user="System Seeder", entity="Sample Dataset", entity_id="1", action="Created", reason=SAMPLE_NOTICE),
    ])
    db.session.commit()
    _write_local_credentials(passwords)


def generate_sample_import() -> None:
    """Tracked sample workbooks are intentionally not generated in this edition."""
    return None
