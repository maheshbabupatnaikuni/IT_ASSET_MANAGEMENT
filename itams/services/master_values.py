"""Safe creation of simple master values entered through an Other option."""
from sqlalchemy import func

from ..extensions import db
from .audit import audit


OTHER_VALUE = "__other__"


def resolve_master_id(model, form, field, *, required=False, label=None):
    label = label or field.replace("_id", "").replace("_", " ").title()
    raw = (form.get(field) or "").strip()
    if raw == OTHER_VALUE:
        name = (form.get(f"{field}_other") or "").strip()
        if not name:
            raise ValueError(f"Enter the new {label}.")
        item = model.query.filter(func.lower(func.trim(model.name)) == name.lower()).first()
        if item and not item.active:
            raise ValueError(f"{label} '{name}' exists but is inactive. Ask Super Admin to activate it.")
        if not item:
            item = model(name=name, active=True)
            db.session.add(item)
            db.session.flush()
            audit(model.__name__, item.id, "Created from Other option", new={"name": name})
        return item.id
    if not raw:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    try:
        item_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Select a valid {label}.") from exc
    item = db.session.get(model, item_id)
    if not item or not item.active:
        raise ValueError(f"Select an active {label}.")
    return item.id
