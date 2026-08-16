from __future__ import annotations

from pathlib import Path
import re

from flask import current_app


REFERENCE_PATTERN = re.compile(r"^ITAM-AST-\d{6,}$")


def configured_base_url() -> str:
    return str(current_app.config.get("ASSET_PROFILE_BASE_URL", "")).strip().rstrip("/")


def asset_profile_url(asset) -> str:
    base_url = configured_base_url()
    if not base_url:
        raise RuntimeError(
            "Asset Profile Base URL is not configured. Set ITAMS_ASSET_PROFILE_BASE_URL "
            "or application.asset_profile_base_url in itams.ini."
        )
    reference = asset.system_asset_reference
    if not reference or not REFERENCE_PATTERN.fullmatch(reference):
        raise RuntimeError("This asset does not have a valid permanent system reference.")
    return f"{base_url}/assets/profile/{reference}"


def qr_file_path(asset) -> Path:
    reference = asset.system_asset_reference
    if not reference or not REFERENCE_PATTERN.fullmatch(reference):
        raise RuntimeError("This asset does not have a valid permanent system reference.")
    root = Path(current_app.config["QR_CODE_DIR"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{reference}.png"


def ensure_qr_code(asset, force: bool = False) -> tuple[Path, str, bool]:
    """Return (path, encoded URL, created_now), generating only when missing/forced."""
    path = qr_file_path(asset)
    encoded_url = asset_profile_url(asset)
    if path.exists() and not force:
        return path, encoded_url, False

    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:
        raise RuntimeError("QR support is unavailable. Install the qrcode[pil] package.") from exc

    code = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    code.add_data(encoded_url)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    temporary = path.with_suffix(".tmp.png")
    image.save(temporary, format="PNG")
    temporary.replace(path)
    return path, encoded_url, True


def generate_for_assets(assets, force: bool = False) -> dict:
    result = {"generated": 0, "existing": 0, "failed": []}
    for asset in assets:
        try:
            _path, _url, created = ensure_qr_code(asset, force=force)
            result["generated" if created else "existing"] += 1
        except RuntimeError as exc:
            result["failed"].append((asset.system_asset_reference or str(asset.id), str(exc)))
    return result
